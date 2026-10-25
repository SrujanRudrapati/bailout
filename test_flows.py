"""End-to-end tests of the coordination flows with a fake Discord.

`ask` is scripted per discord id, `post` just collects events.
"""

import asyncio
import os
import tempfile
import unittest

from flows import Hub
from market import ledger_sum

IDS = {"maya": 1, "jordan": 2, "priya": 3}


class FakeDiscord:
    def __init__(self, answers=None):
        self.answers = answers or {}  # discord_id -> list of answers, popped in order
        self.questions = []  # (discord_id, question)
        self.events = []

    async def ask(self, discord_id, question):
        self.questions.append((discord_id, question))
        queue = self.answers.get(discord_id, [])
        return queue.pop(0) if queue else "YES"

    async def post(self, event):
        self.events.append(event)


def make_hub(fake):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    hub = Hub(tmp.name, ask=fake.ask, post=fake.post)
    hub.seed(IDS)
    return hub, tmp.name


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class BailoutTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDiscord()
        self.hub, self.path = make_hub(self.fake)

    def tearDown(self):
        os.remove(self.path)

    def test_gate1_no_means_nothing_posted(self):
        self.fake.answers = {1: ["NO"]}
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        self.assertEqual(self.fake.events, [])

    def test_jordan_wins_dishes_and_is_paid_priyas_cost(self):
        # jordan dishes 4*1.3=5.2, priya 6*1.5=9.0 -> jordan wins, paid 9.0
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        favor = self.hub.open_favor("dishes")
        self.assertEqual(favor["helper"], "jordan")
        self.assertEqual(favor["price"], 9.0)

    def test_request_only_shares_busy_band(self):
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        request = self.fake.events[0]
        self.assertEqual(request["type"], "request")
        self.assertEqual(request["busy_band"], "high")
        self.assertNotIn("midterm", str(request))
        self.assertNotIn("busy", {k for k in request if k != "busy_band"})

    def test_bid_events_only_contain_allowed_fields(self):
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        for event in self.fake.events:
            if event["type"] == "bid":
                self.assertEqual(set(event), {"type", "name", "task", "bid", "busy_band"})

    def test_winner_declines_so_next_bidder_is_asked(self):
        self.fake.answers = {2: ["NO"]}  # jordan says no at gate 2
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        favor = self.hub.open_favor("dishes")
        self.assertEqual(favor["helper"], "priya")
        # priya is the only one left, so she's paid her own cost
        self.assertEqual(favor["price"], 9.0)
        self.assertIn("declined", [e["type"] for e in self.fake.events])

    def test_cap_dm_fires_when_bid_exceeds_cap(self):
        priya = self.hub.agent("priya").with_cap(5.0)
        self.hub.save_agent(priya)
        self.fake.answers = {3: ["NO"]}
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        cap_questions = [q for uid, q in self.fake.questions if uid == 3 and "cap" in q]
        self.assertEqual(len(cap_questions), 1)
        # priya sat out, so jordan is the only bidder and gets his own cost
        self.assertEqual(self.hub.open_favor("dishes")["price"], 5.2)

    def test_cap_reply_raises_cap_and_bids(self):
        self.hub.save_agent(self.hub.agent("priya").with_cap(5.0))
        self.fake.answers = {3: ["CAP 20"]}
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        self.assertEqual(self.hub.agent("priya").cap, 20.0)
        self.assertEqual(self.hub.open_favor("dishes")["price"], 9.0)

    def test_shared_log_matches_posted_events(self):
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes", "trash"]))
        logged = [e["sent"] for e in self.hub.state["shared_log"]["entries"]]
        self.assertEqual(logged, self.fake.events)
        report = self.hub.shared_report()
        self.assertIn("stayed private", report)
        self.assertIn("reasons", report)


class DoneAndPaybackTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDiscord()
        self.hub, self.path = make_hub(self.fake)
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))

    def tearDown(self):
        os.remove(self.path)

    def test_done_settles_and_ledger_sums_to_zero(self):
        run(self.hub.done(self.hub.agent("jordan"), "dishes"))
        self.assertEqual(self.hub.balance("jordan"), 9.0)
        self.assertEqual(self.hub.balance("maya"), -9.0)
        self.assertAlmostEqual(ledger_sum(self.hub.state["ledger"]), 0.0)
        self.assertIsNone(self.hub.open_favor("dishes"))

    def test_done_by_stranger_is_refused(self):
        msg = run(self.hub.done(self.hub.agent("priya"), "dishes"))
        self.assertIn("only", msg)
        self.assertIsNotNone(self.hub.open_favor("dishes"))

    def test_advance_without_credits_does_nothing(self):
        msg = run(self.hub.advance("jordan", 9))
        self.assertIn("no payback", msg)

    def test_advance_triggers_payback_from_biggest_debtor(self):
        run(self.hub.done(self.hub.agent("jordan"), "dishes"))
        self.fake.events.clear()
        msg = run(self.hub.advance("jordan", 9))
        self.assertEqual(self.fake.events[0]["type"], "payback")
        self.assertEqual(self.fake.events[0]["debtor"], "maya")
        # gate 3: both jordan (2) and maya (1) were asked
        asked = [uid for uid, _ in self.fake.questions[-2:]]
        self.assertEqual(asked, [2, 1])
        # maya's agent offered a task back, maya has default dislike 5 everywhere so trash/groceries tie
        self.assertIn("off jordan's plate", msg)
        offered = [t for t, f in self.hub.state["open"].items() if f["helper"] == "maya"]
        self.assertEqual(len(offered), 1)

    def test_payback_debtor_can_say_no(self):
        run(self.hub.done(self.hub.agent("jordan"), "dishes"))
        self.fake.answers = {1: ["NO"]}
        msg = run(self.hub.advance("jordan", 9))
        self.assertIn("passed", msg)
        self.assertEqual([f["helper"] for f in self.hub.state["open"].values()], [])


if __name__ == "__main__":
    unittest.main()
