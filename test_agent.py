"""Tests for agent.py - mostly about what leaks and what doesn't."""

import unittest

from agent import PRIVATE_FIELDS, SHARED_FIELDS, Agent, busy_band


def jordan():
    return Agent(
        name="jordan",
        discord_id=1,
        busy=3,
        dislikes={"trash": 2, "dishes": 4},
        reasons="chill week",
        availability="evenings",
        cap=8.0,
    )


class ShareTests(unittest.TestCase):
    def test_share_returns_only_the_allowed_fields(self):
        shared = jordan().share(["dishes"], ledger_balance=2.0)
        self.assertEqual(set(shared.keys()), set(SHARED_FIELDS))

    def test_share_never_includes_raw_private_values(self):
        agent = jordan()
        shared = agent.share(["dishes", "trash"], ledger_balance=0.0)
        text = str(shared)
        self.assertNotIn("chill week", text)
        self.assertNotIn("evenings", text)
        self.assertNotIn("dislike", text)
        # exact busy number should not appear, only the band
        self.assertEqual(shared["busy_band"], "low")
        self.assertNotIn("busy", {k for k in shared if k != "busy_band"})

    def test_bids_use_private_cost(self):
        shared = jordan().share(["dishes"], ledger_balance=0.0)
        # 4 * (1 + 3/10) = 5.2
        self.assertEqual(shared["bids"]["dishes"], 5.2)

    def test_unknown_task_gets_default_dislike(self):
        # default dislike 5, busy 3 -> 6.5
        self.assertEqual(jordan().bid("groceries"), 6.5)


class UpdateTests(unittest.TestCase):
    def test_with_busy_returns_new_agent(self):
        original = jordan()
        updated = original.with_busy(9)
        self.assertEqual(original.busy, 3)
        self.assertEqual(updated.busy, 9)

    def test_with_dislike_clamps_and_lowercases(self):
        updated = jordan().with_dislike("Trash", 15)
        self.assertEqual(updated.dislike_for("trash"), 10)

    def test_cap_check(self):
        agent = jordan().with_cap(5.0)
        self.assertTrue(agent.bid_exceeds_cap("dishes"))  # 5.2 > 5
        self.assertFalse(agent.bid_exceeds_cap("trash"))  # 2.6 < 5


class BandTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(busy_band(1), "low")
        self.assertEqual(busy_band(3), "low")
        self.assertEqual(busy_band(4), "medium")
        self.assertEqual(busy_band(7), "medium")
        self.assertEqual(busy_band(8), "high")


class RoundTripTests(unittest.TestCase):
    def test_to_dict_from_dict(self):
        agent = jordan()
        self.assertEqual(Agent.from_dict(agent.to_dict()), agent)

    def test_private_field_list_is_documented(self):
        self.assertIn("reasons", " ".join(PRIVATE_FIELDS))


if __name__ == "__main__":
    unittest.main()
