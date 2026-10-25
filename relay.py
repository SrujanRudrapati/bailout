"""The ntfy.sh relay: the same Cubbie bridge line, carried by a public pub/sub topic.

Discord blocks webhook posts from Cubbie's servers, so the Skill publishes the line
to https://ntfy.sh/<topic> instead and the bot polls that topic every few seconds:

    GET https://ntfy.sh/<topic>/json?poll=1&since=<last id, or 60s on the first poll>

The topic name is the shared secret, so it is never logged: errors are reported by
status or exception class and anything that might echo it goes through scrub().
Each message is checked the same way as a webhook line (bridge.parse_bridge_line,
known user) plus three relay-only rules: it must be a "message" event, its id must
not have been seen before, and it must be under RELAY_STALE_SECONDS old.

The first poll after a (re)start only primes the inbox: whatever is already in the
topic is marked seen and not acted on, so a restart never replays a bailout that
the previous process already ran. A line published during the restart itself is
lost; that is the price of never running one twice.
"""

import json
import re
import time
from collections import OrderedDict
from typing import Callable, Optional

import aiohttp

from bridge import BridgeError, BridgeRequest, parse_bridge_line

RELAY_HOST = "https://ntfy.sh"
RELAY_POLL_SECONDS = 3
RELAY_STALE_SECONDS = 300
RELAY_FIRST_SINCE = "60s"
RELAY_TIMEOUT_SECONDS = 10
MAX_SEEN_IDS = 1000
# ntfy topic names: letters, digits, dash, underscore, up to 64. Anything else would change the URL path.
_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$", re.ASCII)


class RelayError(RuntimeError):
    """A poll of the relay failed. str() says why, never the topic."""


def is_topic_name(topic: str) -> bool:
    return bool(_TOPIC_RE.match(topic or ""))


def relay_url(topic: str, last_id: Optional[str]) -> str:
    since = last_id if last_id else RELAY_FIRST_SINCE
    return f"{RELAY_HOST}/{topic}/json?poll=1&since={since}"


def scrub(text: str, topic: str) -> str:
    """Replace the topic wherever it appears, for anything that may be logged."""
    return text.replace(topic, "<topic>") if topic else text


def parse_relay_feed(text: str) -> list[dict]:
    """ntfy answers one JSON object per line. Anything that isn't one is skipped."""
    events = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


async def fetch_relay_events(session: aiohttp.ClientSession, topic: str, last_id: Optional[str]) -> list[dict]:
    """One poll of the topic. Raises RelayError (topic-free) on HTTP or network trouble."""
    timeout = aiohttp.ClientTimeout(total=RELAY_TIMEOUT_SECONDS)
    try:
        async with session.get(relay_url(topic, last_id), timeout=timeout) as response:
            response.raise_for_status()
            text = await response.text()
    except aiohttp.ClientResponseError as exc:
        raise RelayError(f"ntfy answered HTTP {exc.status}") from None
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        # class name only: aiohttp and decode errors can carry the URL, and with it the topic
        raise RelayError(f"could not reach or read ntfy ({type(exc).__name__})") from None
    return parse_relay_feed(text)


class RelayInbox:
    """Remembers which relay messages were already handled, and where the next poll starts."""

    def __init__(self, max_seen: int = MAX_SEEN_IDS):
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max_seen = max_seen
        self.last_id: Optional[str] = None
        self.primed = False

    def prime(self, events: list[dict]) -> None:
        """Mark everything already in the topic as seen without acting on it (first poll after a start)."""
        for event in events:
            message_id = event.get("id")
            if isinstance(message_id, str) and message_id:
                self._remember(message_id)
        self.primed = True

    def accept(self, event: dict, find_user: Callable[[int], Optional[object]], now: Optional[float] = None) -> BridgeRequest:
        """Gate one ntfy event: message event, new id, fresh, strict line, known user. Raises BridgeError."""
        if event.get("event", "message") != "message":
            raise BridgeError(f"{event.get('event')} event, not a message")
        message_id = event.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise BridgeError("message has no id")
        if message_id in self._seen:
            raise BridgeError(f"duplicate of message {message_id}")
        self._remember(message_id)
        sent_at = event.get("time")
        if not isinstance(sent_at, (int, float)) or isinstance(sent_at, bool):
            raise BridgeError("message has no timestamp")
        age = (time.time() if now is None else now) - sent_at
        if age > RELAY_STALE_SECONDS:
            raise BridgeError(f"stale, sent {int(age)}s ago")
        request = parse_bridge_line(str(event.get("message", "")))
        if find_user(request.user_id) is None:
            raise BridgeError(f"unknown user {request.user_id}, run /seed")
        return request

    def _remember(self, message_id: str) -> None:
        self._seen[message_id] = None
        self.last_id = message_id
        while len(self._seen) > self._max_seen:
            self._seen.popitem(last=False)
