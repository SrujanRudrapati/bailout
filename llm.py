"""All the Claude calls live here.

Two jobs:
1. floor_message() - turn a structured event into one short in-character line
   for #agent-floor. It only ever sees share() data (name, busy band, bids,
   ledger balance). Never raw dislikes, reasons or availability.
2. parse_reply() - read what a human typed back to their agent and decide
   YES / NO / CAP <n> / ESCALATE. ESCALATE means "not sure, ask the human
   plainly instead of guessing".

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

FLOOR_SYSTEM = (
    "You are a roommate's personal chore agent posting one line in a shared Discord channel. "
    "You are given a JSON event. Write ONE short, casual line (under 25 words) that says only "
    "what the event says. Never invent reasons, schedules, feelings, or numbers that are not in "
    "the event. Only use the fields given. No emojis except at most one. No quotes around the line."
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
    """One in-character line for #agent-floor. `event` must only contain share() data."""
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
            messages=[{"role": "user", "content": json.dumps(event)}],
        )
        line = _text_of(response).splitlines()
        return line[0].strip() if line else fallback
    except anthropic.RateLimitError:
        log.warning("rate limited, using template line")
    except anthropic.APIStatusError as err:
        log.warning("api error %s, using template line", err.status_code)
    except anthropic.APIConnectionError:
        log.warning("connection error, using template line")
    return fallback


def _template_line(event: dict) -> str:
    """Plain fallback so the floor still works without an API key."""
    kind = event.get("type")
    who = event.get("name", "someone")
    if kind == "request":
        return f"{who}'s agent: {who} is {event.get('busy_band')} on time. anyone able to cover {', '.join(event.get('tasks', []))}?"
    if kind == "bid":
        return f"{who}'s agent: I can do {event.get('task')} for {event.get('bid')} credits (busy: {event.get('busy_band')})."
    if kind == "result":
        return f"{who}'s agent takes {event.get('task')} for {event.get('price')} credits."
    if kind == "declined":
        return f"{who}'s agent: passing on {event.get('task')}, asking the next bidder."
    if kind == "settled":
        return f"ledger: {who} +{event.get('price')} for {event.get('task')}."
    if kind == "payback":
        return f"{who}'s agent: {who} is {event.get('busy_band')} on time and {event.get('debtor')} owes {event.get('owed')} credits. calling in a favor."
    return json.dumps(event)


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
