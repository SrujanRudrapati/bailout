#!/usr/bin/env python3
"""Publish one bailout request line to the house's relay topic on ntfy.sh.

Discord blocks webhook posts from Cubbie's servers, so the line goes to a public
pub/sub topic that the Bailout bot polls. The bot only accepts this exact shape:

    bailout_request user=<discord_user_id> busy=<1-10> tasks=<task1,task2> source=cubbie

The topic name comes from the environment variable BAILOUT_RELAY_TOKEN. It is the
shared secret (anyone who knows it can post as the house), so it is never printed.
Run it as Cubbie's instructions say:

    BAILOUT_RELAY_TOKEN="$BAILOUT_RELAY_TOKEN" python3 scripts/post.py --user <id> --busy <X> --tasks dishes,trash

Add --dry-run to print the line without sending it.
Exit codes: 0 sent, 2 bad arguments, 3 relay topic not set or not a topic name, 4 post failed.
Python standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from urllib import error, request

ENV_VAR = "BAILOUT_RELAY_TOKEN"
RELAY_HOST = "https://ntfy.sh"
TIMEOUT_SECONDS = 15
# Some edges answer 403 to urllib's default "Python-urllib/3.x" agent, so always say who we are.
USER_AGENT = "BailoutLink (https://github.com/SrujanRudrapati/bailout, 1.0)"
ERROR_BODY_CHARS = 200
BUSY_MIN, BUSY_MAX = 1, 10
MAX_TASKS = 5
MAX_TASK_LEN = 30

# Same rules the bot checks: a snowflake id, letters-only task names (no digits, no @).
USER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
TASK_RE = re.compile(rf"^[A-Za-z][A-Za-z_-]{{0,{MAX_TASK_LEN - 1}}}$", re.ASCII)
# ntfy topic names: letters, digits, dash, underscore, up to 64. A URL or a slash is a mistake.
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$", re.ASCII)


class ArgumentError(ValueError):
    """Something we were asked to send is off. str() says what."""


class PostError(RuntimeError):
    """ntfy did not take the message. str() says what, never the topic."""


def clean_tasks(raw: str) -> list[str]:
    tasks = []
    for part in raw.split(","):
        task = "-".join(part.strip().lower().split())
        if not task:
            raise ArgumentError("tasks must be a comma separated list of chores, no empty entries")
        if not TASK_RE.match(task):
            raise ArgumentError(f"task {part.strip()!r} must be letters only, up to {MAX_TASK_LEN} characters")
        if task not in tasks:
            tasks.append(task)
    if len(tasks) > MAX_TASKS:
        raise ArgumentError(f"at most {MAX_TASKS} tasks per request")
    return tasks


def build_line(user: str, busy: int, tasks: str) -> str:
    if not USER_RE.match(str(user)):
        raise ArgumentError("user must be a Discord user id (digits only)")
    if not isinstance(busy, int) or not BUSY_MIN <= busy <= BUSY_MAX:
        raise ArgumentError(f"busy must be a whole number from {BUSY_MIN} to {BUSY_MAX}")
    return f"bailout_request user={user} busy={busy} tasks={','.join(clean_tasks(tasks))} source=cubbie"


def check_topic(topic: str) -> str:
    """The relay topic as saved in Cubbie: a bare ntfy topic name, not a URL. Never echoed."""
    topic = (topic or "").strip()
    if not TOPIC_RE.match(topic):
        raise ArgumentError(f"{ENV_VAR} must be the ntfy topic name only (letters, digits, - or _, up to 64), not a URL")
    return topic


def relay_url(topic: str) -> str:
    return f"{RELAY_HOST}/{topic}"


def scrub(text: str, topic: str) -> str:
    return text.replace(topic, "<topic>")


def error_snippet(exc: error.HTTPError, topic: str) -> str:
    """First ERROR_BODY_CHARS of the response body, with the topic scrubbed."""
    try:
        raw = exc.read()
    except (OSError, AttributeError):
        return ""
    text = scrub((raw or b"").decode("utf-8", errors="replace"), topic)
    return " ".join(text.split())[:ERROR_BODY_CHARS]


def send(topic: str, line: str) -> None:
    """POST the line as plain text to https://ntfy.sh/<topic>. Raises PostError, never naming the topic."""
    headers = {"Content-Type": "text/plain; charset=utf-8", "User-Agent": USER_AGENT}
    req = request.Request(relay_url(topic), data=line.encode(), method="POST", headers=headers)
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            if response.status >= 300:
                raise PostError(f"ntfy answered HTTP {response.status}")
    except error.HTTPError as exc:
        # Never include exc itself or its url: both carry the topic.
        message = f"ntfy answered HTTP {exc.code}"
        snippet = error_snippet(exc, topic)
        raise PostError(f"{message}: {snippet}" if snippet else message) from None
    except error.URLError as exc:
        raise PostError(f"could not reach ntfy ({scrub(str(exc.reason), topic)})") from None
    except OSError as exc:
        raise PostError(f"could not reach ntfy ({type(exc).__name__})") from None


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user", required=True, help="the student's Discord user id")
    parser.add_argument("--busy", required=True, type=int, help="busy level 1-10 from busy.py")
    parser.add_argument("--tasks", required=True, help="chores to cover, comma separated, e.g. dishes,trash")
    parser.add_argument("--dry-run", action="store_true", help="print the line instead of posting it")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        line = build_line(args.user, args.busy, args.tasks)
    except ArgumentError as exc:
        print(f"bad arguments: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"would send: {line}")
        return 0
    try:
        topic = check_topic(os.environ.get(ENV_VAR, ""))
    except ArgumentError as exc:
        print(f"relay topic not set: {exc}", file=sys.stderr)
        return 3
    try:
        send(topic, line)
    except PostError as exc:
        print(f"post failed: {exc}", file=sys.stderr)
        return 4
    print(f"sent: busy={args.busy} tasks={line.split('tasks=')[1].split()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
