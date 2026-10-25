"""One Agent per roommate.

The agent holds the stuff its human would rather not broadcast: exact busy
score, how much they hate each chore, and why. The only thing that ever leaves
the object is what share() returns. Nothing else is posted to Discord or sent
to the LLM.
"""

from dataclasses import dataclass, field, replace

from market import cost

DEFAULT_DISLIKE = 5
DEFAULT_BUSY = 5
DEFAULT_CAP = 20.0  # high enough that the scripted demo never trips a cap check; /me cap changes it

# the fields share() is allowed to return. Kept as a constant so /shared can
# print it and the test can check nothing else sneaks out.
SHARED_FIELDS = ("name", "busy_band", "bids", "ledger_balance")
PRIVATE_FIELDS = ("busy (exact number)", "dislikes per task", "reasons", "availability", "cap")


def busy_band(busy: int) -> str:
    """Coarse version of busy so others know roughly how slammed you are, not the exact score."""
    if busy <= 3:
        return "low"
    if busy <= 7:
        return "medium"
    return "high"


@dataclass(frozen=True)
class Agent:
    name: str
    discord_id: int
    busy: int = DEFAULT_BUSY
    dislikes: dict = field(default_factory=dict)  # task -> 1-10
    reasons: str = ""  # e.g. "two midterms this week" - private
    availability: str = ""  # e.g. "free after 6pm" - private
    cap: float = DEFAULT_CAP  # bid above this and the agent asks the human first

    # --- updates return a new Agent, we never mutate ---

    def with_busy(self, busy: int) -> "Agent":
        return replace(self, busy=_clamp(busy))

    def with_dislike(self, task: str, level: int) -> "Agent":
        new_dislikes = {**self.dislikes, task.lower(): _clamp(level)}
        return replace(self, dislikes=new_dislikes)

    def with_cap(self, cap: float) -> "Agent":
        return replace(self, cap=max(0.0, float(cap)))

    # --- private reasoning ---

    def dislike_for(self, task: str) -> int:
        return self.dislikes.get(task.lower(), DEFAULT_DISLIKE)

    def bid(self, task: str) -> float:
        """What this agent would charge, in credits. Uses the private cost formula."""
        return round(cost(self.dislike_for(task), self.busy), 1)

    def bid_exceeds_cap(self, task: str) -> bool:
        return self.bid(task) > self.cap

    # --- the one public surface ---

    def share(self, tasks: list[str], ledger_balance: float) -> dict:
        """Everything another agent (or the LLM) is allowed to know about me."""
        return {
            "name": self.name,
            "busy_band": busy_band(self.busy),
            "bids": {task: self.bid(task) for task in tasks},
            "ledger_balance": ledger_balance,
        }

    # --- JSON state ---

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "discord_id": self.discord_id,
            "busy": self.busy,
            "dislikes": dict(self.dislikes),
            "reasons": self.reasons,
            "availability": self.availability,
            "cap": self.cap,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Agent":
        return cls(
            name=data["name"],
            discord_id=int(data["discord_id"]),
            busy=int(data.get("busy", DEFAULT_BUSY)),
            dislikes=dict(data.get("dislikes", {})),
            reasons=data.get("reasons", ""),
            availability=data.get("availability", ""),
            cap=float(data.get("cap", DEFAULT_CAP)),
        )


def _clamp(value: int, low: int = 1, high: int = 10) -> int:
    return max(low, min(high, int(value)))
