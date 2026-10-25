#!/usr/bin/env python3
"""Post one bailout request line to the house's Bailout webhook in Discord.

The Bailout bot only accepts this exact shape, from this one webhook:

    bailout_request user=<discord_user_id> busy=<1-10> tasks=<task1,task2> source=cubbie

The webhook URL comes from the environment variable BAILOUT_HOOK_TOKEN and is never
printed. Run it as Cubbie's instructions say:

    BAILOUT_HOOK_TOKEN="$BAILOUT_HOOK_TOKEN" python3 scripts/post.py --user <id> --busy <X> --tasks dishes,trash

Add --dry-run to print the line without sending it.
Exit codes: 0 sent, 2 bad arguments, 3 webhook not set or not a Discord webhook, 4 post failed.
Python standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib import error, request

ENV_VAR = "BAILOUT_HOOK_TOKEN"
WEBHOOK_PREFIX = "https://discord.com/api/webhooks/"
TIMEOUT_SECONDS = 15
# Discord's edge answers 403 to urllib's default "Python-urllib/3.x" agent.
USER_AGENT = "BailoutLink (https://github.com/SrujanRudrapati/bailout, 1.0)"
ERROR_BODY_CHARS = 200
BUSY_MIN, BUSY_MAX = 1, 10
MAX_TASKS = 5
MAX_TASK_LEN = 30

# Same rules the bot checks: a snowflake id, letters-only task names (no digits, no @).
USER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
TASK_RE = re.compile(rf"^[A-Za-z][A-Za-z_-]{{0,{MAX_TASK_LEN - 1}}}$", re.ASCII)


class ArgumentError(ValueError):
    """Something we were asked to send is off. str() says what."""


class PostError(RuntimeError):
    """Discord did not take the message. str() says what, never the URL."""


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


def check_webhook_url(url: str) -> str:
    if not url or not url.startswith(WEBHOOK_PREFIX) or len(url) <= len(WEBHOOK_PREFIX):
        raise ArgumentError(f"{ENV_VAR} must be a Discord webhook URL starting with {WEBHOOK_PREFIX}")
    return url


def error_snippet(exc: error.HTTPError, url: str) -> str:
    """First ERROR_BODY_CHARS of the response body, with the webhook URL and token scrubbed."""
    try:
        text = exc.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    token = url.rstrip("/").rsplit("/", 1)[-1]
    text = text.replace(url, "<webhook>").replace(token, "<token>")
    return " ".join(text.split())[:ERROR_BODY_CHARS]


def send(url: str, line: str) -> None:
    body = json.dumps({"content": line}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    req = request.Request(url, data=body, method="POST", headers=headers)
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            if response.status >= 300:
                raise PostError(f"Discord answered HTTP {response.status}")
    except error.HTTPError as exc:
        # Never include exc itself or its url: both carry the webhook token.
        message = f"Discord answered HTTP {exc.code}"
        snippet = error_snippet(exc, url)
        raise PostError(f"{message}: {snippet}" if snippet else message) from None
    except error.URLError as exc:
        raise PostError(f"could not reach Discord ({exc.reason})") from None
    except OSError as exc:
        raise PostError(f"could not reach Discord ({type(exc).__name__})") from None


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
        url = check_webhook_url(os.environ.get(ENV_VAR, ""))
    except ArgumentError as exc:
        print(f"webhook not set: {exc}", file=sys.stderr)
        return 3
    try:
        send(url, line)
    except PostError as exc:
        print(f"post failed: {exc}", file=sys.stderr)
        return 4
    print(f"sent: busy={args.busy} tasks={line.split('tasks=')[1].split()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
