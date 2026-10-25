"""Tests for scripts/busy.py: busy level 1-10 from what's due in the next 7 days."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import busy  # noqa: E402

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)


def due_in(days: float) -> str:
    return (NOW + timedelta(days=days)).isoformat()


def item(name="hw", days=3.0, points=None, weight=None, kind="assignment"):
    data = {"name": name, "due": due_in(days), "type": kind}
    if points is not None:
        data["points"] = points
    if weight is not None:
        data["weight"] = weight
    return data


class BusyLevelTest(unittest.TestCase):
    def test_empty_list_is_busy_1(self):
        self.assertEqual(busy.busy_level([], NOW), 1)

    def test_never_below_1_or_above_10(self):
        light = [item(days=6.9, points=1)]
        heavy = [item(f"exam{i}", days=0.5, points=300, kind="exam") for i in range(8)]
        self.assertEqual(busy.busy_level(light, NOW), 1)
        self.assertEqual(busy.busy_level(heavy, NOW), 10)

    def test_sooner_is_busier(self):
        soon = busy.busy_level([item(days=0.5, points=100)], NOW)
        later = busy.busy_level([item(days=6.5, points=100)], NOW)
        self.assertGreater(soon, later)

    def test_more_points_is_busier(self):
        small = busy.busy_level([item(days=2, points=10)], NOW)
        big = busy.busy_level([item(days=2, points=200)], NOW)
        self.assertGreater(big, small)

    def test_weight_counts_like_points(self):
        light = busy.busy_level([item(days=2, weight=2)], NOW)
        heavy = busy.busy_level([item(days=2, weight=30)], NOW)
        self.assertGreater(heavy, light)

    def test_exam_is_heavier_than_assignment_with_same_points(self):
        hw = busy.busy_level([item(days=2, points=100, kind="assignment")], NOW)
        exam = busy.busy_level([item(days=2, points=100, kind="exam")], NOW)
        self.assertGreater(exam, hw)

    def test_more_items_is_busier(self):
        one = busy.busy_level([item(days=3, points=50)], NOW)
        three = busy.busy_level([item(f"hw{i}", days=3, points=50) for i in range(3)], NOW)
        self.assertGreater(three, one)

    def test_items_outside_the_week_are_ignored(self):
        outside = [item("later", days=9, points=500, kind="exam"), item("done", days=-2, points=500, kind="exam")]
        self.assertEqual(busy.busy_level(outside, NOW), 1)

    def test_items_without_a_due_date_are_ignored(self):
        self.assertEqual(busy.busy_level([{"name": "someday", "points": 500}], NOW), 1)

    def test_two_exams_this_week_is_near_the_top(self):
        week = [item("midterm", days=1, points=100, kind="exam"), item("final", days=3, points=150, kind="exam")]
        self.assertGreaterEqual(busy.busy_level(week, NOW), 8)

    def test_one_small_homework_is_near_the_bottom(self):
        self.assertLessEqual(busy.busy_level([item(days=5, points=10)], NOW), 3)

    def test_submitted_items_do_not_count(self):
        done = [dict(item(days=1, points=200, kind="exam"), submitted=True)]
        self.assertEqual(busy.busy_level(done, NOW), 1)


class ParsingTest(unittest.TestCase):
    def test_accepts_canvas_style_keys(self):
        raw = [{"title": "Quiz 3", "due_at": due_in(2), "points_possible": 20, "type": "quiz"}]
        items = busy.normalize_items(raw)
        self.assertEqual(items[0].name, "Quiz 3")
        self.assertEqual(items[0].points, 20)
        self.assertEqual(items[0].kind, "quiz")

    def test_accepts_a_dict_with_an_items_key(self):
        items = busy.normalize_items({"items": [item(points=5)]})
        self.assertEqual(len(items), 1)

    def test_naive_due_dates_are_read_as_utc(self):
        naive = (NOW + timedelta(days=2)).replace(tzinfo=None).isoformat()
        items = busy.normalize_items([{"name": "x", "due": naive}])
        self.assertEqual(items[0].due, NOW + timedelta(days=2))

    def test_z_suffix_is_accepted(self):
        items = busy.normalize_items([{"name": "x", "due": "2026-10-26T09:00:00Z"}])
        self.assertEqual(items[0].due, datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc))

    def test_bad_due_date_is_skipped_not_fatal(self):
        items = busy.normalize_items([{"name": "x", "due": "next tuesday-ish"}])
        self.assertIsNone(items[0].due)

    def test_bad_points_are_treated_as_unknown(self):
        items = busy.normalize_items([{"name": "x", "due": due_in(1), "points": "lots"}])
        self.assertIsNone(items[0].points)

    def test_rejects_non_list_input(self):
        with self.assertRaises(busy.InputError):
            busy.normalize_items("just a string")
        with self.assertRaises(busy.InputError):
            busy.normalize_items([1, 2, 3])


class CommandLineTest(unittest.TestCase):
    def run_main(self, argv, stdin_text=None):
        out, err = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        if stdin_text is not None:
            sys.stdin = io.StringIO(stdin_text)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = busy.main(argv)
        finally:
            sys.stdin = old_stdin
        return code, out.getvalue(), err.getvalue()

    def test_first_line_is_busy_equals_number(self):
        code, out, _ = self.run_main(["--now", NOW.isoformat(), "--input", "-"], json.dumps([item(points=50)]))
        self.assertEqual(code, 0)
        first = out.splitlines()[0]
        self.assertRegex(first, r"^busy=(?:[1-9]|10)$")

    def test_empty_json_list_gives_busy_1(self):
        code, out, _ = self.run_main(["--now", NOW.isoformat(), "--input", "-"], "[]")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "busy=1")

    def test_reads_from_a_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump([item(days=1, points=100, kind="exam")], handle)
        try:
            code, out, _ = self.run_main(["--now", NOW.isoformat(), "--input", handle.name])
        finally:
            os.unlink(handle.name)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("busy="))

    def test_sample_mode_runs_without_input(self):
        code, out, _ = self.run_main(["--sample", "--now", "2026-10-19T09:00:00+00:00"])
        self.assertEqual(code, 0)
        self.assertRegex(out.splitlines()[0], r"^busy=(?:[1-9]|10)$")

    def test_sample_mode_defaults_to_a_date_inside_the_sample_week(self):
        code, out, _ = self.run_main(["--sample"])
        self.assertEqual(code, 0)
        self.assertGreaterEqual(int(out.splitlines()[0].split("=")[1]), 2)

    def test_invalid_json_exits_2(self):
        code, _, err = self.run_main(["--now", NOW.isoformat(), "--input", "-"], "{not json")
        self.assertEqual(code, 2)
        self.assertIn("JSON", err)

    def test_bad_now_exits_2(self):
        code, _, err = self.run_main(["--now", "yesterday", "--input", "-"], "[]")
        self.assertEqual(code, 2)
        self.assertIn("--now", err)

    def test_missing_file_exits_2(self):
        code, _, err = self.run_main(["--now", NOW.isoformat(), "--input", "/nope/nothing.json"])
        self.assertEqual(code, 2)
        self.assertIn("read", err.lower())


if __name__ == "__main__":
    unittest.main()
