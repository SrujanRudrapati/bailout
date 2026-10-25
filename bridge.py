"""The Cubbie bridge: one strict line format posted by a Discord webhook.

A Cubbie Skill posts into #agent-floor through a webhook. We only trust messages
whose webhook id matches BRIDGE_WEBHOOK_ID, and only one line shape:

    bailout_request user=<discord_user_id> busy=<1-10> tasks=<task1,task2> source=cubbie

Everything else raises BridgeError with a short reason. The reason never repeats
the busy value, and task names are letters only (no digits, no mentions), so
nothing from the raw line can smuggle the busy number or a ping onto the floor.
"""

import re
from dataclasses import dataclass
from typing import Callable, Optional

BRIDGE_SOURCE = "cubbie"
BUSY_MIN, BUSY_MAX = 1, 10
MAX_TASKS = 5
MAX_TASK_LEN = 30
MAX_LINE_LEN = 300

# ASCII only on purpose: str.isdigit() accepts things like "²" that int() then rejects.
_LINE_RE = re.compile(
    r"^bailout_request user=(?P<user>\S+) busy=(?P<busy>\S*) tasks=(?P<tasks>\S*) source=(?P<source>\S+)$",
    re.ASCII,
)
_USER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_BUSY_RE = re.compile(r"^(?:[1-9]|10)$", re.ASCII)
_TASK_RE = re.compile(rf"^[A-Za-z][A-Za-z_-]{{0,{MAX_TASK_LEN - 1}}}$", re.ASCII)


class BridgeError(ValueError):
    """The message is not a bridge request we should act on. str() says why."""


@dataclass(frozen=True)
class BridgeRequest:
    user_id: int
    busy: int
    tasks: tuple[str, ...]
    source: str


def parse_bridge_line(content: str) -> BridgeRequest:
    """Strictly parse one bridge line. Raises BridgeError for anything off-format."""
    if len(content) > MAX_LINE_LEN:
        raise BridgeError("line too long")
    line = content.strip()
    if "\n" in line or "\r" in line:
        raise BridgeError("more than one line")
    match = _LINE_RE.match(line)
    if match is None:
        raise BridgeError("not a bailout_request line")
    if match["source"] != BRIDGE_SOURCE:
        raise BridgeError(f"unexpected source {match['source']!r}")
    if not _USER_RE.match(match["user"]):
        raise BridgeError("user is not a discord id")
    if not _BUSY_RE.match(match["busy"]):
        raise BridgeError(f"busy is not an integer {BUSY_MIN}-{BUSY_MAX}")
    tasks = _parse_tasks(match["tasks"])
    return BridgeRequest(user_id=int(match["user"]), busy=int(match["busy"]), tasks=tasks, source=match["source"])


def _parse_tasks(raw: str) -> tuple[str, ...]:
    parts = raw.split(",")
    if len(parts) > MAX_TASKS:
        raise BridgeError(f"more than {MAX_TASKS} tasks")
    if any(not _TASK_RE.match(part) for part in parts):
        raise BridgeError("tasks must be letters only, comma separated, no empty entries")
    return tuple(dict.fromkeys(part.lower() for part in parts))


def accept_bridge_message(
    webhook_id: Optional[int],
    content: str,
    trusted_webhook_id: Optional[int],
    find_user: Callable[[int], Optional[object]],
) -> BridgeRequest:
    """Gate a Discord message: trusted webhook, strict line, known user. Raises BridgeError."""
    if not is_bridge_webhook(webhook_id, trusted_webhook_id):
        raise BridgeError(f"webhook {webhook_id} is not the bridge webhook")
    request = parse_bridge_line(content)
    if find_user(request.user_id) is None:
        raise BridgeError(f"unknown user {request.user_id}, they need to /join")
    return request


def is_bridge_webhook(webhook_id: Optional[int], trusted_webhook_id: Optional[int]) -> bool:
    """True only when a bridge webhook is configured and this message came from it."""
    if trusted_webhook_id is None or webhook_id is None:
        return False
    return int(webhook_id) == int(trusted_webhook_id)
