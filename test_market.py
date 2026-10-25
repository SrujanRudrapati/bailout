"""Tests for market.py. Run with: python -m unittest test_market -v"""

import unittest

from market import Bidder, cost, ledger_sum, payback_order, run_auction, settle


def make_bidders():
    # dislike for "dishes" and busy level for three roommates
    return [
        Bidder(name="jordan", dislike=4, busy=3, balance=0.0),
        Bidder(name="priya", dislike=6, busy=5, balance=0.0),
        Bidder(name="sam", dislike=8, busy=2, balance=0.0),
    ]


class CostTests(unittest.TestCase):
    def test_busier_helper_bids_higher_for_same_task(self):
        # Arrange: same dislike, different busy
        relaxed = cost(dislike=4, busy=2)
        swamped = cost(dislike=4, busy=8)
        # Assert
        self.assertGreater(swamped, relaxed)

    def test_cost_formula(self):
        # dislike * (1 + busy/10) -> 4 * 1.3 = 5.2
        self.assertAlmostEqual(cost(dislike=4, busy=3), 5.2)


class AuctionTests(unittest.TestCase):
    def test_lowest_cost_wins(self):
        result = run_auction(make_bidders())
        self.assertEqual(result.winner, "jordan")

    def test_winner_is_paid_second_lowest_cost(self):
        # jordan = 4*1.3 = 5.2, priya = 6*1.5 = 9.0, sam = 8*1.2 = 9.6
        result = run_auction(make_bidders())
        self.assertEqual(result.price, 9.0)

    def test_single_bidder_is_paid_own_cost(self):
        result = run_auction([Bidder("jordan", dislike=4, busy=3, balance=0.0)])
        self.assertEqual(result.winner, "jordan")
        self.assertEqual(result.price, 5.2)

    def test_tie_goes_to_lowest_ledger_balance(self):
        bidders = [
            Bidder("rich", dislike=4, busy=3, balance=5.0),
            Bidder("broke", dislike=4, busy=3, balance=-5.0),
        ]
        result = run_auction(bidders)
        self.assertEqual(result.winner, "broke")

    def test_ranking_is_in_cost_order(self):
        result = run_auction(make_bidders())
        self.assertEqual([b.name for b in result.ranking], ["jordan", "priya", "sam"])

    def test_no_bidders_returns_none(self):
        self.assertIsNone(run_auction([]))


class LedgerTests(unittest.TestCase):
    def test_ledger_sums_to_zero_after_settle(self):
        ledger = {"maya": 0.0, "jordan": 0.0, "priya": 0.0}
        new_ledger = settle(ledger, requester="maya", helper="jordan", credits=9.0)
        self.assertAlmostEqual(ledger_sum(new_ledger), 0.0)
        self.assertEqual(new_ledger["jordan"], 9.0)
        self.assertEqual(new_ledger["maya"], -9.0)

    def test_settle_does_not_mutate_input(self):
        ledger = {"maya": 0.0, "jordan": 0.0}
        settle(ledger, requester="maya", helper="jordan", credits=3.0)
        self.assertEqual(ledger, {"maya": 0.0, "jordan": 0.0})

    def test_ledger_still_zero_after_many_rounds(self):
        ledger = {"maya": 0.0, "jordan": 0.0, "priya": 0.0}
        ledger = settle(ledger, "maya", "jordan", 9.0)
        ledger = settle(ledger, "maya", "priya", 4.5)
        ledger = settle(ledger, "jordan", "maya", 6.1)
        self.assertAlmostEqual(ledger_sum(ledger), 0.0)


class PaybackTests(unittest.TestCase):
    def test_payback_picks_biggest_debtor_first(self):
        # jordan is owed 13.5: maya owes 9, priya owes 4.5
        ledger = {"maya": -9.0, "jordan": 13.5, "priya": -4.5}
        self.assertEqual(payback_order(ledger, "jordan", busy=9), ["maya", "priya"])

    def test_payback_needs_busy_8_or_more(self):
        ledger = {"maya": -9.0, "jordan": 9.0}
        self.assertEqual(payback_order(ledger, "jordan", busy=7), [])
        self.assertEqual(payback_order(ledger, "jordan", busy=8), ["maya"])

    def test_payback_needs_positive_balance(self):
        ledger = {"maya": 9.0, "jordan": -9.0}
        self.assertEqual(payback_order(ledger, "jordan", busy=10), [])

    def test_payback_ignores_people_with_positive_balance(self):
        ledger = {"maya": -5.0, "jordan": 2.0, "priya": 3.0}
        self.assertEqual(payback_order(ledger, "jordan", busy=9), ["maya"])


if __name__ == "__main__":
    unittest.main()
