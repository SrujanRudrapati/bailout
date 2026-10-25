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

    def test_done_posts_exactly_one_completed_line(self):
        self.fake.events.clear()
        run(self.hub.done(self.hub.agent("jordan"), "dishes"))
        self.assertEqual(
            self.fake.events, [{"type": "completed", "name": "jordan", "task": "dishes", "price": 9.0, "requester": "maya"}]
        )

    def test_done_reply_is_just_the_ledger_block(self):
        msg = run(self.hub.done(self.hub.agent("jordan"), "dishes"))
        self.assertEqual(msg, self.hub.ledger_report())
        self.assertNotIn("finished", msg)
        self.assertNotIn("done.", msg)

    def test_winning_posts_awarded_not_completed(self):
        types = [e["type"] for e in self.fake.events]
        self.assertIn("awarded", types)
        self.assertNotIn("completed", types)
        self.assertNotIn("result", types)

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
        # one friendly floor line for the offer, with no credit amount on the floor
        self.assertEqual(
            self.fake.events[0], {"type": "payback_request", "name": "jordan", "busy_band": "high", "debtor": "maya"}
        )
        self.assertEqual([e["type"] for e in self.fake.events], ["payback_request", "payback_done"])
        # gate 3: both jordan (2) and maya (1) were asked
        asked = [uid for uid, _ in self.fake.questions[-2:]]
        self.assertEqual(asked, [2, 1])
        # maya's agent offered a task back, maya has default dislike 5 everywhere so trash/groceries tie
        self.assertIn("off jordan's plate", msg)
        offered = [t for t, f in self.hub.state["open"].items() if f["helper"] == "maya"]
        self.assertEqual(len(offered), 1)
        payback_done = self.fake.events[-1]
        self.assertEqual(payback_done["type"], "payback_done")
        self.assertEqual(payback_done["creditor"], "jordan")
        self.assertEqual(payback_done["task"], offered[0])

    def test_payback_debtor_can_say_no(self):
        run(self.hub.done(self.hub.agent("jordan"), "dishes"))
        self.fake.answers = {1: ["NO"]}
        self.fake.events.clear()
        msg = run(self.hub.advance("jordan", 9))
        self.assertIn("passed", msg)
        self.assertEqual([e["type"] for e in self.fake.events], ["payback_request", "declined"])
        self.assertEqual([f["helper"] for f in self.hub.state["open"].values()], [])


if __name__ == "__main__":
    unittest.main()


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDiscord()
        self.hub, self.path = make_hub(self.fake)

    def tearDown(self):
        os.remove(self.path)

    def test_duplicate_tasks_are_collapsed(self):
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes", "Dishes", " dishes "]))
        self.assertEqual(len([e for e in self.fake.events if e["type"] == "awarded"]), 1)

    def test_task_with_open_favor_is_not_reauctioned(self):
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        first = self.hub.open_favor("dishes")
        msg = run(self.hub.bailout(self.hub.agent("priya"), ["dishes"]))
        self.assertIn("already", msg)
        self.assertEqual(self.hub.open_favor("dishes"), first)

    def test_payback_with_no_free_task_does_not_overwrite(self):
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes", "trash", "groceries"]))
        run(self.hub.done(self.hub.agent("jordan"), "dishes"))
        before = dict(self.hub.state["open"])
        # dishes is free again after /done, so this should use dishes; make it busy again first
        run(self.hub.bailout(self.hub.agent("priya"), ["dishes"]))
        before = dict(self.hub.state["open"])
        msg = run(self.hub.advance("jordan", 9))
        self.assertIn("already has an open favor", msg)
        self.assertEqual(self.hub.state["open"], before)


class CapCheckTests(unittest.TestCase):
    """The cap check must never leave the floor looking stuck."""

    def setUp(self):
        self.fake = FakeDiscord()
        self.hub, self.path = make_hub(self.fake)

    def tearDown(self):
        os.remove(self.path)

    def test_seed_gives_everyone_cap_20(self):
        for agent in self.hub.agents():
            self.assertEqual(agent.cap, 20.0, agent.name)

    def test_seeded_demo_never_asks_about_a_cap(self):
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes", "trash", "groceries"]))
        self.assertEqual([q for _, q in self.fake.questions if "cap" in q], [])

    def test_me_cap_still_lowers_the_seeded_cap(self):
        self.hub.save_agent(self.hub.agent("priya").with_cap(5.0))
        self.assertEqual(self.hub.agent("priya").cap, 5.0)

    def test_floor_hears_the_agent_is_checking_before_the_cap_dm(self):
        self.hub.save_agent(self.hub.agent("priya").with_cap(5.0))
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        checks = [e for e in self.fake.events if e["type"] == "cap_check"]
        self.assertEqual(checks, [{"type": "cap_check", "name": "priya", "task": "dishes"}])
        # posted before priya's own bid, i.e. while her agent was still waiting on the DM
        types = [e["type"] for e in self.fake.events]
        priya_bid = next(i for i, e in enumerate(self.fake.events) if e["type"] == "bid" and e["name"] == "priya")
        self.assertLess(types.index("cap_check"), priya_bid)

    def test_cap_declined_posts_passed_and_auction_continues(self):
        self.hub.save_agent(self.hub.agent("priya").with_cap(5.0))
        self.fake.answers = {3: ["NO"]}  # gates.py also returns NO on timeout
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        self.assertIn({"type": "passed", "name": "priya", "task": "dishes"}, self.fake.events)
        self.assertNotIn("priya", [e["name"] for e in self.fake.events if e["type"] == "bid"])
        favor = self.hub.open_favor("dishes")
        self.assertEqual((favor["helper"], favor["price"]), ("jordan", 5.2))

    def test_cap_escalate_is_treated_as_a_pass(self):
        self.hub.save_agent(self.hub.agent("priya").with_cap(5.0))
        self.fake.answers = {3: ["ESCALATE"]}
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        self.assertIn({"type": "passed", "name": "priya", "task": "dishes"}, self.fake.events)
        self.assertEqual(self.hub.open_favor("dishes")["helper"], "jordan")

    def test_cap_raised_but_still_too_low_is_a_pass(self):
        self.hub.save_agent(self.hub.agent("priya").with_cap(5.0))
        self.fake.answers = {3: ["CAP 6"]}  # priya's dishes bid is 9.0
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        self.assertIn({"type": "passed", "name": "priya", "task": "dishes"}, self.fake.events)
        self.assertEqual(self.hub.agent("priya").cap, 6.0)

    def test_cap_accepted_posts_no_pass(self):
        self.hub.save_agent(self.hub.agent("priya").with_cap(5.0))
        self.fake.answers = {3: ["YES"]}
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        self.assertNotIn("passed", [e["type"] for e in self.fake.events])
        self.assertEqual(self.hub.open_favor("dishes")["price"], 9.0)


class CubbieBailoutTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDiscord()
        self.hub, self.path = make_hub(self.fake)

    def tearDown(self):
        os.remove(self.path)

    def test_skips_gate_1_and_still_runs_gate_2(self):
        run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=9, tasks=["dishes"]))
        asked = [discord_id for discord_id, _ in self.fake.questions]
        self.assertNotIn(1, asked)  # maya is never asked, cubbie already confirmed
        self.assertIn(2, asked)  # jordan still gets the gate 2 DM
        self.assertEqual(self.hub.open_favor("dishes")["helper"], "jordan")

    def test_sets_the_requesters_busy_level(self):
        run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=4, tasks=["dishes"]))
        self.assertEqual(self.hub.agent("maya").busy, 4)

    def test_floor_sees_a_cubbie_request_with_only_the_band(self):
        run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=8, tasks=["dishes", "trash"]))
        request = self.fake.events[0]
        self.assertEqual(request, {"type": "cubbie_request", "name": "maya", "busy_band": "high", "tasks": ["dishes", "trash"]})
        self.assertNotIn("8", str(request))

    def test_already_open_tasks_are_skipped_without_posting(self):
        run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=9, tasks=["dishes"]))
        self.fake.events.clear()
        summary = run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=9, tasks=["dishes"]))
        self.assertIn("already has an open favor", summary)
        self.assertEqual(self.fake.events, [])
