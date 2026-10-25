"""All the Claude calls live here.

Two jobs:
1. floor_message() - turn a structured event into one short in-character line
   for #agent-floor. It only ever sees share() data (name, busy band, bids,
   ledger balance). Never raw dislikes, reasons or availability.
2. parse_reply() - read what a human typed back to their agent and decide
   YES / NO / CAP <n> / ESCALATE. ESCALATE means "not sure, ask the human
   plainly instead of guessing". The DM gate (gates.py) no longer calls this;
   it uses plain keyword matching. Kept for anything that wants a fuzzier read.

Optional: verify_photo() looks at a chore photo with Sonnet vision.

If ANTHROPIC_API_KEY is missing the floor messages fall back to plain
templates so the demo still runs, and parse_reply only trusts obvious answers.
"""

import base64
import json
import logging
import os
import re

import anthropic

log = logging.getLogger("bailout.llm")

FLOOR_MODEL = "claude-haiku-5-5"
PHOTO_MODEL = "claude-sonnet-5-5"
ONE_LINE_TOKENS = 120

REPLY_OPTIONS = ("YES", "NO", "CAP", "ESCALATE")
PHOTO_OPTIONS = ("DONE", "NOT_DONE", "UNSURE")

# Each floor event type and what it means. The LLM is told exactly which one it
# is describing so it can't mix "accepted the task" up with "finished the task".
FLOOR_EVENT_TYPES = {
    "request": "someone is asking the house to cover one or more tasks",
    "cubbie_request": (
        "the requester's Cubbie (their planner app) reports they are swamped and asks the agents to bid on the tasks. "
        "Only the busy band is known, never a number. Capitalize the name and never use a pronoun for the requester "
        "(you don't know their pronouns), so write 'says: swamped' rather than 'says they're swamped'. "
        "Shape: '<Name>'s Cubbie says: swamped this week (busy: <band>). Agents, bids please.'"
    ),
    "bid": "an agent offers to do a task for a number of credits",
    "cap_check": "the agent's bid is over its human's limit, so it is checking with its human before bidding. nothing decided yet",
    "passed": "the agent is not bidding on this task (its human declined or didn't answer)",
    "awarded": "the winner's human ACCEPTED the task. It is NOT done yet. Do not say finished/done/completed",
    "declined": "an agent passed on a task",
    "completed": "the task was actually finished (/done). the helper earned the credits from the requester",
    "payback_request": (
        "someone is swamped, so the agent of the roommate who owes them the most is first in line to help. "
        "This is a friendly heads-up, never a demand: do not say owe/owes/debt/pay back, do not mention "
        "amounts, and talk about the debtor's agent rather than addressing the debtor. "
        "Shape: '<name>'s swamped. <debtor>'s agent is first in line to help.'"
    ),
    "payback_done": "the debtor's agent is taking a task off the creditor's plate to pay back credits",
}

FLOOR_SYSTEM = (
    "You are a roommate's personal chore agent posting one line in a shared Discord channel. "
    "You get an event type, what that type means, and the event's JSON data. Write ONE short, "
    "casual line (under 25 words) describing ONLY that event. Credits are favors, not money: "
    "always say 'credits', never '$', 'dollars', 'paid' or 'money'. Never invent reasons, "
    "schedules, feelings, or numbers that are not in the data. Only use the fields given. "
    "No emojis except at most one. No quotes around the line.\n\nEvent types:\n"
    + "\n".join(f"- {kind}: {meaning}" for kind, meaning in FLOOR_EVENT_TYPES.items())
)

REPLY_SYSTEM = (
    "A roommate's chore agent asked its human a yes/no question over DM. You read the human's "
    "reply and classify it. Answer with exactly one of:\n"
    "YES - they agree\n"
    "NO - they decline\n"
    "CAP <number> - they agree but set a max number of credits, e.g. 'fine but not more than 5' -> CAP 5\n"
    "ESCALATE - the reply is unclear, conditional in a way you can't resolve, asks a question, "
    "or you are not confident.\n"
    "When in doubt, ESCALATE. Output only the label."
)

PHOTO_SYSTEM = (
    "You check whether a household chore looks finished in a photo. Answer with exactly one of "
    "DONE, NOT_DONE, or UNSURE. Say UNSURE if the photo is blurry, unrelated, or you can't tell. "
    "Output only the label."
)


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return anthropic.Anthropic()


def _text_of(response) -> str:
    if response.stop_reason == "refusal":
        return ""
    return "".join(block.text for block in response.content if block.type == "text").strip()


# ---------------------------------------------------------------- floor lines


def floor_message(event: dict) -> str:
    """One in-character line for #agent-floor. `event` must only contain share() data.

    The LLM line is validated before it is used; anything that fails falls back
    to the plain template and is logged.
    """
    fallback = _template_line(event)
    client = _client()
    if client is None:
        return fallback
    try:
        response = client.messages.create(
            model=FLOOR_MODEL,
            max_tokens=ONE_LINE_TOKENS,
            output_config={"effort": "low"},
            system=FLOOR_SYSTEM,
            messages=[{"role": "user", "content": _floor_prompt(event)}],
        )
    except anthropic.RateLimitError:
        log.warning("rate limited, using template line")
        return fallback
    except anthropic.APIStatusError as err:
        log.warning("api error %s, using template line", err.status_code)
        return fallback
    except anthropic.APIConnectionError:
        log.warning("connection error, using template line")
        return fallback
    lines = _text_of(response).splitlines()
    line = lines[0].strip() if lines else ""
    if not validate_line(line, event):
        log.warning("floor line failed validation for %s event, using template: %r", event.get("type"), line)
        return fallback
    return line


def _floor_prompt(event: dict) -> str:
    kind = event.get("type", "unknown")
    meaning = FLOOR_EVENT_TYPES.get(kind, "unknown event")
    return f"event type: {kind}\nmeaning: {meaning}\ndata: {json.dumps(event)}"


# ---------------------------------------------------------------- validation

_FORBIDDEN_WORDS = ("$", "dollar", "paid", "money")
# a payback offer is a friendly heads-up, never a demand
_DEMAND_WORDS = re.compile(r"\b(owe|owes|owed|owing|debt|debts|pay\s*(?:it\s*)?back|payback|must|time to)\b", re.IGNORECASE)
_COMPLETION_WORDS = re.compile(r"\b(finish|finished|done|complete|completed)\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def validate_line(line: str, event: dict) -> bool:
    """True if an LLM floor line is safe to post for `event`.

    Rejects money wording, any number not present in the event data,
    completion wording on an `awarded` event (accepted != finished), and
    demand wording on a `payback_request` (it is an offer, not a bill).
    """
    if not line.strip() or "\n" in line:
        return False
    lowered = line.lower()
    if any(word in lowered for word in _FORBIDDEN_WORDS):
        return False
    allowed = _numbers_in(event)
    if any(float(n) not in allowed for n in _NUMBER_RE.findall(line)):
        return False
    if event.get("type") == "awarded" and _COMPLETION_WORDS.search(line):
        return False
    if event.get("type") == "payback_request" and _DEMAND_WORDS.search(line):
        return False
    return True


def _numbers_in(value) -> set[float]:
    """Every number reachable in the event data, as floats so 9 == 9.0 == 9.00."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {float(value)}
    if isinstance(value, str):
        return {float(n) for n in _NUMBER_RE.findall(value)}
    if isinstance(value, dict):
        return set().union(*(_numbers_in(v) for v in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_numbers_in(v) for v in value)) if value else set()
    return set()


# ---------------------------------------------------------------- templates


def _template_line(event: dict) -> str:
    """Plain fallback so the floor still works without an API key. One template per event type."""
    kind = event.get("type")
    who = event.get("name", "someone")
    task = event.get("task")
    price = event.get("price")
    if kind == "request":
        return f"{who} is {event.get('busy_band')} on time. anyone able to cover {', '.join(event.get('tasks', []))}?"
    if kind == "cubbie_request":
        return f"{_capitalized(who)}'s Cubbie says: swamped this week (busy: {event.get('busy_band')}). Agents, bids on {', '.join(event.get('tasks', []))} please."
    if kind == "bid":
        return f"{who} can do {task} for {event.get('bid')} credits (busy: {event.get('busy_band')})."
    if kind == "cap_check":
        return f"{who}'s agent is checking with them before bidding on {task}."
    if kind == "passed":
        return f"{who}'s agent passed on {task}."
    if kind == "awarded":
        return f"{who}'s taking {task} for {price} credits."
    if kind == "declined":
        return f"{who} is passing on {task}, asking the next bidder."
    if kind == "completed":
        return f"{who} finished {task} for {event.get('requester')}. +{price} credits to {who}."
    if kind == "payback_request":
        return f"{who}'s swamped. {event.get('debtor')}'s agent is first in line to help."
    if kind == "payback_done":
        return f"{who}'s taking {task} off {event.get('creditor')}'s plate for {price} credits. paying back."
    return json.dumps(event)


def _capitalized(name: str) -> str:
    """Upper-case only the first letter, leaving the rest as the roommate spelled it."""
    return name[:1].upper() + name[1:]


# ---------------------------------------------------------------- human replies

_YES = {"yes", "y", "yep", "yeah", "sure", "ok", "okay", "✅", "👍", "do it", "go"}
_NO = {"no", "n", "nope", "nah", "❌", "👎", "pass", "skip"}
_CAP_RE = re.compile(r"\bcap\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)


def parse_reply(text: str) -> str:
    """Return 'YES', 'NO', 'CAP <n>' or 'ESCALATE'."""
    cleaned = text.strip().lower()
    # obvious answers don't need a model call
    if cleaned in _YES:
        return "YES"
    if cleaned in _NO:
        return "NO"
    cap_match = _CAP_RE.search(cleaned)
    if cap_match and len(cleaned) < 20:
        return f"CAP {cap_match.group(1)}"

    client = _client()
    if client is None:
        return "ESCALATE"
    try:
        response = client.messages.create(
            model=FLOOR_MODEL,
            max_tokens=20,
            output_config={"effort": "low"},
            system=REPLY_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        return _normalize_reply(_text_of(response))
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as err:
        log.warning("parse_reply failed (%s), escalating", err)
        return "ESCALATE"


def _normalize_reply(label: str) -> str:
    label = label.strip().upper()
    if label in ("YES", "NO", "ESCALATE"):
        return label
    cap = re.match(r"CAP\s+(\d+(?:\.\d+)?)", label)
    if cap:
        return f"CAP {cap.group(1)}"
    return "ESCALATE"


# ---------------------------------------------------------------- photo proof


def verify_photo(image_bytes: bytes, media_type: str, task: str) -> str:
    """Return DONE, NOT_DONE or UNSURE for a photo of a finished chore."""
    client = _client()
    if client is None:
        return "UNSURE"
    data = base64.standard_b64encode(image_bytes).decode("utf-8")
    try:
        response = client.messages.create(
            model=PHOTO_MODEL,
            max_tokens=20,
            output_config={"effort": "low"},
            system=PHOTO_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                        {"type": "text", "text": f"Is the chore '{task}' done in this photo?"},
                    ],
                }
            ],
        )
        label = _text_of(response).strip().upper()
        return label if label in PHOTO_OPTIONS else "UNSURE"
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as err:
        log.warning("verify_photo failed (%s)", err)
        return "UNSURE"
