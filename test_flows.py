"""End-to-end tests of the coordination flows with a fake Discord.

`ask` is scripted per discord id, `post` just collects events.
"""

import asyncio
import os
import tempfile
import unittest

from flows import Hub, player_name, unfilled_reason
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

    def test_floor_never_names_who_declined_but_the_requester_dm_does(self):
        self.fake.answers = {2: ["NO"]}  # jordan says no at gate 2
        summary = run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        declined = [e for e in self.fake.events if e["type"] == "declined"]
        self.assertEqual(declined, [{"type": "declined", "task": "dishes", "next": True}])
        # the shared log attributes the line to the auction, not to anyone's agent
        logged = [e for e in self.hub.state["shared_log"]["entries"] if e["sent"]["type"] == "declined"]
        self.assertEqual([e["agent"] for e in logged], ["auction"])
        report_lines = [line for line in self.hub.shared_report().splitlines() if "(declined)" in line]
        self.assertEqual(report_lines, ["- auction (declined): task=dishes, next=True"])
        # only maya's own summary says the task moved on, and from whom
        self.assertEqual(summary, "dishes: jordan passed, so priya has it for 9.0 credits (settles on /done dishes)")

    def test_last_decline_says_nobody_is_next(self):
        self.fake.answers = {2: ["NO"], 3: ["NO"]}
        summary = run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        declined = [e for e in self.fake.events if e["type"] == "declined"]
        self.assertEqual(declined[-1], {"type": "declined", "task": "dishes", "next": False})
        self.assertTrue(summary.endswith("jordan and priya passed."), summary)

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
        self.assertEqual(self.fake.events[1:], [{"type": "declined", "task": "payback", "next": False}])
        self.assertEqual([f["helper"] for f in self.hub.state["open"].values()], [])


class UnfilledTaskTests(unittest.TestCase):
    """When no one ends up taking a task, the floor and the requester are told why, in counts only."""

    def setUp(self):
        self.fake = FakeDiscord()
        self.hub, self.path = make_hub(self.fake)

    def tearDown(self):
        os.remove(self.path)

    def test_every_human_passing_posts_an_unfilled_line_with_counts(self):
        self.fake.answers = {2: ["NO"], 3: ["NO"]}
        summary = run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=2, tasks=["dishes"]))
        self.assertEqual(
            self.fake.events[-1],
            {"type": "unfilled", "name": "maya", "task": "dishes", "bids": 2, "reason": "2 agents bid, but every human passed."},
        )
        self.assertEqual(summary, "dishes: nobody could take it this time. 2 agents bid, but every human passed. jordan and priya passed.")

    def _nobody_bids(self):
        # both helpers' agents sit out: bids over a tiny cap, humans say no to bidding anyway
        self.hub.save_agent(self.hub.agent("jordan").with_cap(1))
        self.hub.save_agent(self.hub.agent("priya").with_cap(1))
        self.fake.answers = {2: ["NO"], 3: ["NO"]}

    def test_no_bids_and_nobody_less_busy_says_so_without_numbers(self):
        # maya busy 2 is "low", jordan is low and priya medium: nobody is in a lower band than maya
        self._nobody_bids()
        summary = run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=2, tasks=["dishes"]))
        self.assertEqual(
            self.fake.events[-1],
            {"type": "unfilled", "name": "maya", "task": "dishes", "bids": 0, "reason": "nobody's less busy than Maya this week."},
        )
        self.assertEqual(summary, "dishes: nobody could take it this time. nobody's less busy than you this week. jordan's and priya's agents sat out.")
        self.assertNotRegex(str(self.fake.events[-1]["reason"]), r"\d")

    def test_no_bids_but_someone_less_busy_blames_the_caps(self):
        # maya busy 9 is "high" and the helpers are low/medium, so "nobody's less busy" would be a lie
        self._nobody_bids()
        summary = run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=9, tasks=["dishes"]))
        self.assertEqual(
            self.fake.events[-1],
            {"type": "unfilled", "name": "maya", "task": "dishes", "bids": 0, "reason": "every agent's bid was over its human's limit."},
        )
        self.assertEqual(summary, "dishes: nobody could take it this time. every agent's bid was over its human's limit. jordan's and priya's agents sat out.")

    def test_unfilled_line_never_carries_a_busy_number(self):
        self.fake.answers = {2: ["NO"], 3: ["NO"]}
        run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=2, tasks=["dishes"]))
        unfilled = self.fake.events[-1]
        self.assertNotIn("busy", str(unfilled))
        self.assertNotIn("busy_band", unfilled)

    def test_an_awarded_task_posts_no_unfilled_line(self):
        run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=2, tasks=["dishes"]))
        self.assertNotIn("unfilled", [e["type"] for e in self.fake.events])


if __name__ == "__main__":
    unittest.main()


class UnfilledReasonTests(unittest.TestCase):
    """The reason is plain code over public facts: the bid count and the busy bands already on the floor."""

    def test_no_other_agents(self):
        self.assertEqual(unfilled_reason(0, "low", [], "Maya"), "nobody else has joined the house yet.")

    def test_no_bids_and_every_helper_at_least_as_busy(self):
        self.assertEqual(unfilled_reason(0, "medium", ["medium", "high"], "Maya"), "nobody's less busy than Maya this week.")
        self.assertEqual(unfilled_reason(0, "low", ["low"], "you"), "nobody's less busy than you this week.")

    def test_no_bids_with_a_less_busy_helper_is_about_caps(self):
        self.assertEqual(unfilled_reason(0, "high", ["low", "high"], "Maya"), "every agent's bid was over its human's limit.")

    def test_bids_but_every_human_passed(self):
        self.assertEqual(unfilled_reason(1, "high", ["low"], "Maya"), "1 agent bid, but every human passed.")
        self.assertEqual(unfilled_reason(3, "low", ["low", "low", "low"], "Maya"), "3 agents bid, but every human passed.")


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
        self.assertEqual(checks, [{"type": "cap_check", "task": "dishes"}])
        # posted before priya's own bid, i.e. while her agent was still waiting on the DM
        types = [e["type"] for e in self.fake.events]
        priya_bid = next(i for i, e in enumerate(self.fake.events) if e["type"] == "bid" and e["name"] == "priya")
        self.assertLess(types.index("cap_check"), priya_bid)

    def test_cap_declined_posts_passed_and_auction_continues(self):
        self.hub.save_agent(self.hub.agent("priya").with_cap(5.0))
        self.fake.answers = {3: ["NO"]}  # gates.py also returns NO on timeout
        summary = run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        self.assertIn({"type": "passed", "task": "dishes"}, self.fake.events)
        self.assertNotIn("priya", [e["name"] for e in self.fake.events if e["type"] == "bid"])
        favor = self.hub.open_favor("dishes")
        self.assertEqual((favor["helper"], favor["price"]), ("jordan", 5.2))
        # the floor line has no name in it; maya's own summary says whose agent sat out
        self.assertEqual(summary, "dishes: priya's agent sat out, so jordan has it for 5.2 credits (settles on /done dishes)")

    def test_cap_escalate_is_treated_as_a_pass(self):
        self.hub.save_agent(self.hub.agent("priya").with_cap(5.0))
        self.fake.answers = {3: ["ESCALATE"]}
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        self.assertIn({"type": "passed", "task": "dishes"}, self.fake.events)
        self.assertEqual(self.hub.open_favor("dishes")["helper"], "jordan")

    def test_cap_raised_but_still_too_low_is_a_pass(self):
        self.hub.save_agent(self.hub.agent("priya").with_cap(5.0))
        self.fake.answers = {3: ["CAP 6"]}  # priya's dishes bid is 9.0
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        self.assertIn({"type": "passed", "task": "dishes"}, self.fake.events)
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

    def test_every_task_in_the_request_is_auctioned(self):
        summary = run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=9, tasks=["dishes", "trash"]))
        self.assertEqual(self.hub.open_favor("dishes")["helper"], "jordan")
        self.assertEqual(self.hub.open_favor("trash")["helper"], "jordan")
        awarded = [e["task"] for e in self.fake.events if e["type"] == "awarded"]
        self.assertEqual(awarded, ["dishes", "trash"])
        self.assertEqual(summary.splitlines()[0][:7], "dishes:")
        self.assertEqual(summary.splitlines()[1][:6], "trash:")

    def test_a_task_that_blows_up_does_not_stop_the_next_one(self):
        real_post = self.fake.post

        async def flaky_post(event):
            if event["type"] == "awarded" and event["task"] == "dishes":
                raise RuntimeError("discord hiccup")
            await real_post(event)

        self.hub.post = flaky_post
        with self.assertLogs("bailout.flows", level="ERROR") as captured:
            summary = run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=9, tasks=["dishes", "trash"]))
        self.assertEqual(self.hub.open_favor("trash")["helper"], "jordan")
        self.assertIn("trash", [e["task"] for e in self.fake.events if e["type"] == "awarded"])
        lines = summary.splitlines()
        self.assertIn("went wrong", lines[0])
        self.assertIn("trash: jordan", lines[1])
        self.assertTrue(any("dishes" in msg for msg in captured.output))

    def test_already_open_tasks_are_skipped_without_posting(self):
        run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=9, tasks=["dishes"]))
        self.fake.events.clear()
        summary = run(self.hub.cubbie_bailout(self.hub.agent("maya"), busy=9, tasks=["dishes"]))
        self.assertIn("already has an open favor", summary)
        self.assertEqual(self.fake.events, [])


class JoinTests(unittest.TestCase):
    """/join: any server member becomes a player with defaults, and the house works at any size."""

    def setUp(self):
        self.fake = FakeDiscord()
        self.hub, self.path = make_hub(self.fake)

    def tearDown(self):
        os.remove(self.path)

    def test_join_creates_an_agent_with_defaults_and_a_zero_balance(self):
        agent, created = self.hub.join(7, "Sam")
        self.assertTrue(created)
        self.assertEqual((agent.name, agent.discord_id, agent.busy, agent.cap), ("sam", 7, 5, 20.0))
        self.assertEqual(agent.dislikes, {})
        self.assertEqual(agent.dislike_for("anything"), 5)
        self.assertEqual(self.hub.balance("sam"), 0.0)
        self.assertEqual(self.hub.agent_for_user(7).name, "sam")

    def test_joining_twice_keeps_the_existing_agent(self):
        self.hub.join(7, "Sam")
        self.hub.save_agent(self.hub.agent("sam").with_busy(9))
        agent, created = self.hub.join(7, "Sam")
        self.assertFalse(created)
        self.assertEqual(agent.busy, 9)

    def test_display_names_become_safe_unique_lowercase_names(self):
        taken = {"maya", "jordan", "priya"}
        self.assertEqual(player_name("Sam", taken), "sam")
        self.assertEqual(player_name("  Alex  Kim ", taken), "alex-kim")
        self.assertEqual(player_name("Maya", taken), "maya-2")
        self.assertEqual(player_name("maya", taken | {"maya-2"}), "maya-3")
        self.assertEqual(player_name("🔥🔥", taken), "roommate")
        self.assertEqual(player_name("auction", taken), "auction-2")  # the auction's own label is reserved
        self.assertEqual(len(player_name("x" * 50, taken)), 20)

    def test_seed_keeps_joined_players_and_zeroes_everyone(self):
        self.hub.join(7, "Sam")
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        run(self.hub.done(self.hub.agent("jordan"), "dishes"))
        self.hub.seed(IDS)
        self.assertEqual(sorted(self.hub.state["agents"]), ["jordan", "maya", "priya", "sam"])
        self.assertEqual(set(self.hub.state["ledger"].values()), {0.0})
        self.assertEqual(self.hub.state["open"], {})

    def test_five_players_auction_lowest_cost_wins_and_is_paid_second_lowest(self):
        self.hub.join(7, "Sam")  # busy 5, dislike 5 -> 7.5
        self.hub.join(8, "Alex")
        self.hub.save_agent(self.hub.agent("alex").with_busy(1).with_dislike("dishes", 1))  # 1.1, the cheapest
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))
        bids = [e for e in self.fake.events if e["type"] == "bid"]
        self.assertEqual(sorted(b["name"] for b in bids), ["alex", "jordan", "priya", "sam"])
        favor = self.hub.open_favor("dishes")
        self.assertEqual((favor["helper"], favor["price"]), ("alex", 5.2))  # jordan's 5.2 is the second lowest

    def test_payback_with_many_players_goes_to_the_biggest_debtor(self):
        self.hub.join(7, "Sam")
        self.hub.join(8, "Alex")
        self.hub.save_agent(self.hub.agent("alex").with_busy(1).with_dislike("dishes", 1).with_dislike("trash", 1))
        run(self.hub.bailout(self.hub.agent("maya"), ["dishes"]))  # alex helps maya for 5.2
        run(self.hub.bailout(self.hub.agent("sam"), ["trash"]))  # alex helps sam for jordan's 2.6
        run(self.hub.done(self.hub.agent("alex"), "dishes"))
        run(self.hub.done(self.hub.agent("alex"), "trash"))
        self.fake.events.clear()
        run(self.hub.advance("alex", 9))
        self.assertEqual(self.fake.events[0]["debtor"], "maya")

    def test_a_house_of_one_is_told_nobody_else_joined(self):
        fake = FakeDiscord()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        try:
            hub = Hub(tmp.name, ask=fake.ask, post=fake.post)
            hub.join(7, "Sam")
            summary = run(hub.bailout(hub.agent("sam"), ["dishes"]))
            self.assertEqual(summary, "dishes: nobody could take it this time. nobody else has joined the house yet.")
            self.assertEqual(fake.events[-1]["reason"], "nobody else has joined the house yet.")
        finally:
            os.remove(tmp.name)

    def test_a_joined_player_can_be_the_cubbie_requester(self):
        self.hub.join(7, "Sam")
        run(self.hub.cubbie_bailout(self.hub.agent("sam"), busy=9, tasks=["dishes"]))
        self.assertEqual(self.hub.open_favor("dishes")["requester"], "sam")
        self.assertEqual(self.hub.agent("sam").busy, 9)
