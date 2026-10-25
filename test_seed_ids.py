"""Tests for resolving /seed ids from mentions or env defaults."""

import unittest

from seed_ids import SEED_ENV_VARS, resolve_seed_ids


class ResolveSeedIds(unittest.TestCase):
    def test_mentions_win_over_env(self):
        env = {"SEED_MAYA_ID": "1", "SEED_JORDAN_ID": "2", "SEED_PRIYA_ID": "3"}
        ids, missing = resolve_seed_ids({"maya": 10, "jordan": 20, "priya": 30}, env)
        self.assertEqual(ids, {"maya": 10, "jordan": 20, "priya": 30})
        self.assertEqual(missing, [])

    def test_env_fills_gaps(self):
        env = {"SEED_MAYA_ID": "1", "SEED_JORDAN_ID": "2", "SEED_PRIYA_ID": "3"}
        ids, missing = resolve_seed_ids({"maya": None, "jordan": 20, "priya": None}, env)
        self.assertEqual(ids, {"maya": 1, "jordan": 20, "priya": 3})
        self.assertEqual(missing, [])

    def test_reports_missing_names(self):
        ids, missing = resolve_seed_ids({"maya": None, "jordan": None, "priya": 30}, {"SEED_PRIYA_ID": "3"})
        self.assertEqual(ids, {"priya": 30})
        self.assertEqual(missing, ["maya", "jordan"])

    def test_bad_env_value_counts_as_missing(self):
        ids, missing = resolve_seed_ids({"maya": None, "jordan": 2, "priya": 3}, {"SEED_MAYA_ID": "not-a-number"})
        self.assertEqual(ids, {"jordan": 2, "priya": 3})
        self.assertEqual(missing, ["maya"])

    def test_env_var_names(self):
        self.assertEqual(SEED_ENV_VARS, {"maya": "SEED_MAYA_ID", "jordan": "SEED_JORDAN_ID", "priya": "SEED_PRIYA_ID"})


if __name__ == "__main__":
    unittest.main()
