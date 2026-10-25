#!/usr/bin/env python3
"""Turn what's due in the next 7 days into one busy number, 1 to 10.

Cubbie reads the student's Canvas assignments, quizzes and exams first and hands them to
this script as JSON (a file, or stdin with --input -). The script never talks to Canvas
itself. Only the number leaves the phone; the item names stay here.

    python3 scripts/busy.py --now "2026-10-24T09:00:00-05:00" --input items.json
    python3 scripts/busy.py --sample

Input: a JSON list (or {"items": [...]}) of objects with
    name / title          what it is (only used in the summary line)
    due / due_at          ISO 8601 date-time; items with no readable date are skipped
    points / points_possible   how many points it is worth (optional)
    weight                percent of the final grade (optional, used when there are no points)
    type                  assignment, quiz or exam (optional, default assignment)
    submitted             true if already turned in (optional, then it is skipped)

Output: first line "busy=<1-10>", then one short line saying why.
Exit codes: 0 ok, 2 bad input (unreadable file, not JSON, bad --now).
Python standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

WINDOW_DAYS = 7
SAMPLE = Path(__file__).resolve().parent.parent / "assets" / "sample_canvas.json"

# How much a kind of work weighs compared with a plain assignment.
KIND_FACTOR = {"assignment": 1.0, "quiz": 1.0, "exam": 2.5}
# Points or weight are turned into a size multiplier between these bounds, so a
# 1000-point project can't blow up the scale and a 2-point quiz still counts a bit.
SIZE_MIN, SIZE_MAX = 0.5, 3.0
POINTS_PER_SIZE = 50.0   # 50 points -> size 1.0
WEIGHT_PER_SIZE = 10.0   # 10% of the grade -> size 1.0
# Something due right now counts twice as much as something due a week out.
URGENCY_NOW, URGENCY_WEEK = 2.0, 1.0
# One plain assignment due mid-week (load about 1.5) lands at busy 2; two exams and a
# project due soon (load about 20) hit 10.
LOAD_PER_LEVEL = 2.0
BUSY_MIN, BUSY_MAX = 1, 10


class InputError(ValueError):
    """The data handed to the script can't be used. str() says why."""


@dataclass(frozen=True)
class Item:
    name: str
    due: Optional[datetime]
    points: Optional[float]
    weight: Optional[float]
    kind: str
    submitted: bool


def parse_when(value) -> Optional[datetime]:
    """ISO 8601 -> aware datetime (naive is read as UTC). None if unreadable."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _first(raw: dict, *keys):
    return next((raw[key] for key in keys if key in raw), None)


def normalize_items(data) -> list[Item]:
    """Accept the loose JSON Cubbie writes and return clean Items. Raises InputError."""
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    if not isinstance(data, list):
        raise InputError("expected a JSON list of assignments")
    if any(not isinstance(raw, dict) for raw in data):
        raise InputError("every item must be a JSON object")
    items = []
    for raw in data:
        kind = str(_first(raw, "type", "kind") or "assignment").strip().lower()
        items.append(Item(
            name=str(_first(raw, "name", "title") or "untitled"),
            due=parse_when(_first(raw, "due", "due_at")),
            points=_number(_first(raw, "points", "points_possible")),
            weight=_number(raw.get("weight")),
            kind=kind if kind in KIND_FACTOR else "assignment",
            submitted=bool(raw.get("submitted", False)),
        ))
    return items


def size_of(item: Item) -> float:
    if item.points is not None:
        raw = item.points / POINTS_PER_SIZE
    elif item.weight is not None:
        raw = item.weight / WEIGHT_PER_SIZE
    else:
        return 1.0
    return min(SIZE_MAX, max(SIZE_MIN, raw))


def urgency_of(days_left: float) -> float:
    fraction_left = max(0.0, min(1.0, days_left / WINDOW_DAYS))
    return URGENCY_NOW - (URGENCY_NOW - URGENCY_WEEK) * fraction_left


def this_week(items: list[Item], now: datetime) -> list[tuple[Item, float]]:
    """(item, days until due) for the unsubmitted items due between now and now + 7 days."""
    end = now + timedelta(days=WINDOW_DAYS)
    kept = []
    for item in items:
        if item.submitted or item.due is None or item.due < now or item.due > end:
            continue
        kept.append((item, (item.due - now).total_seconds() / 86400))
    return kept


def load_of(items: list[Item], now: datetime) -> float:
    return sum(size_of(item) * KIND_FACTOR[item.kind] * urgency_of(days) for item, days in this_week(items, now))


def busy_level(raw_items, now: datetime) -> int:
    """1 (nothing due) to 10 (buried). Heavier and sooner work pushes it up."""
    items = raw_items if raw_items and isinstance(raw_items[0], Item) else normalize_items(raw_items)
    levels = math.floor(load_of(items, now) / LOAD_PER_LEVEL + 0.5)  # round half up
    return min(BUSY_MAX, max(BUSY_MIN, BUSY_MIN + levels))


def summary(items: list[Item], now: datetime) -> str:
    week = this_week(items, now)
    if not week:
        return f"nothing due in the next {WINDOW_DAYS} days"
    exams = sum(1 for item, _ in week if item.kind == "exam")
    soonest, days = min(week, key=lambda pair: pair[1])
    parts = [f"{len(week)} due in the next {WINDOW_DAYS} days"]
    if exams:
        parts.append(f"{exams} exam{'s' if exams != 1 else ''}")
    parts.append(f"soonest: {soonest.name} in {days:.1f} days")
    return ", ".join(parts)


def read_json(source: str):
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text()
    except OSError as exc:
        raise InputError(f"could not read {source}: {exc.strerror or exc}") from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise InputError(f"input is not valid JSON: {exc}") from exc


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="JSON file with the Canvas items, or - for stdin")
    source.add_argument("--sample", action="store_true", help="use the bundled sample instead of real data")
    parser.add_argument("--now", help="the student's current date-time, ISO 8601 (default: the clock here, UTC)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    now = parse_when(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print("--now must be an ISO 8601 date-time like 2026-10-24T09:00:00-05:00", file=sys.stderr)
        return 2
    try:
        items = normalize_items(read_json(str(SAMPLE) if args.sample else args.input))
    except InputError as exc:
        print(f"bad input: {exc}", file=sys.stderr)
        return 2
    print(f"busy={busy_level(items, now)}")
    print(summary(items, now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
