"""The chore market. Pure Python, no LLM, no Discord.

How it works:
- each helper has a private cost for a task: dislike (1-10) * (1 + busy/10)
- lowest cost wins (Vickrey reverse auction) and gets paid the SECOND lowest
  cost in credits, so nobody gains by lying about their cost
- the ledger is zero-sum: requester goes down, helper goes up by the same amount
- payback rule: if you're swamped (busy >= 8) and people owe you, your agent
  asks the biggest debtor first
"""

from dataclasses import dataclass
from typing import Optional

PAYBACK_BUSY_THRESHOLD = 8
# how many decimals credits are rounded to
CREDIT_DECIMALS = 1


def cost(dislike: int, busy: int) -> float:
    """Private cost of doing a task. Busier people quote higher for the same chore."""
    return round(dislike * (1 + busy / 10), 2)


@dataclass(frozen=True)
class Bidder:
    name: str
    dislike: int
    busy: int
    balance: float

    @property
    def cost(self) -> float:
        return cost(self.dislike, self.busy)


@dataclass(frozen=True)
class AuctionResult:
    winner: str
    price: float  # credits the winner earns
    ranking: tuple  # bidders sorted best to worst, so we can fall back if the winner says no


def rank_bidders(bidders: list[Bidder]) -> list[Bidder]:
    """Sort by cost, and on a tie by who has the lowest ledger balance (they need credits more)."""
    return sorted(bidders, key=lambda b: (b.cost, b.balance, b.name))


def run_auction(bidders: list[Bidder]) -> Optional[AuctionResult]:
    """Vickrey: lowest cost wins, paid the second lowest cost. One bidder gets their own cost."""
    if not bidders:
        return None
    ranking = rank_bidders(bidders)
    winner = ranking[0]
    price = ranking[1].cost if len(ranking) > 1 else winner.cost
    return AuctionResult(
        winner=winner.name,
        price=round(price, CREDIT_DECIMALS),
        ranking=tuple(ranking),
    )


def settle(ledger: dict[str, float], requester: str, helper: str, credits: float) -> dict[str, float]:
    """Return a new ledger where the helper gained credits and the requester lost the same amount."""
    new_ledger = dict(ledger)
    new_ledger[helper] = round(new_ledger.get(helper, 0.0) + credits, CREDIT_DECIMALS)
    new_ledger[requester] = round(new_ledger.get(requester, 0.0) - credits, CREDIT_DECIMALS)
    return new_ledger


def ledger_sum(ledger: dict[str, float]) -> float:
    """Should always be zero. Favors are zero-sum."""
    return round(sum(ledger.values()), CREDIT_DECIMALS)


def payback_order(ledger: dict[str, float], name: str, busy: int) -> list[str]:
    """Who should this person's agent ask first when they're swamped?

    Returns debtors (negative balance) sorted by how much they owe, biggest first.
    Empty list if the person isn't busy enough or doesn't have credits to call in.
    """
    if busy < PAYBACK_BUSY_THRESHOLD:
        return []
    if ledger.get(name, 0.0) <= 0:
        return []
    debtors = [(other, bal) for other, bal in ledger.items() if other != name and bal < 0]
    debtors.sort(key=lambda pair: (pair[1], pair[0]))  # most negative first
    return [other for other, _ in debtors]
