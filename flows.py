"""The actual coordination logic, with Discord factored out.

Hub gets two async callbacks:
  ask(discord_id, question) -> "YES" | "NO" | "CAP <n>" | "ESCALATE"
  post(event)               -> posts one line to #agent-floor built from `event`

Every `event` we post is ALSO what gets written to the shared log, so /shared
shows exactly what left each agent. That's on purpose.
"""

import logging
from typing import Awaitable, Callable, Optional

import store
from agent import PRIVATE_FIELDS, Agent, busy_band
from market import Bid, payback_order, run_auction, settle

log = logging.getLogger("bailout.flows")

TASKS = ("dishes", "trash", "groceries")

AskFn = Callable[[int, str], Awaitable[str]]
PostFn = Callable[[dict], Awaitable[None]]


def seed_profiles(ids: dict[str, int]) -> list[Agent]:
    """The three demo roommates. ids maps lowercase name -> discord id."""
    return [
        Agent(name="maya", discord_id=ids["maya"], busy=9, reasons="two midterms this week", availability="basically none"),
        Agent(name="jordan", discord_id=ids["jordan"], busy=3, dislikes={"trash": 2, "dishes": 4}, reasons="chill week", availability="evenings"),
        Agent(name="priya", discord_id=ids["priya"], busy=5, dislikes={"dishes": 6, "trash": 7}, reasons="lab report due fri", availability="afternoons"),
    ]


class Hub:
    def __init__(self, state_path: str, ask: AskFn, post: PostFn):
        self.state_path = state_path
        self.ask = ask
        self.post = post
        self.state = store.load(state_path)

    # ------------------------------------------------------------ state helpers

    def _commit(self, new_state: dict) -> None:
        self.state = new_state
        store.save(self.state_path, new_state)

    def agents(self) -> list[Agent]:
        return [Agent.from_dict(d) for d in self.state["agents"].values()]

    def agent(self, name: str) -> Optional[Agent]:
        data = self.state["agents"].get(name.lower())
        return Agent.from_dict(data) if data else None

    def agent_for_user(self, discord_id: int) -> Optional[Agent]:
        for agent in self.agents():
            if agent.discord_id == discord_id:
                return agent
        return None

    def balance(self, name: str) -> float:
        return self.state["ledger"].get(name, 0.0)

    def save_agent(self, agent: Agent) -> None:
        agents = {**self.state["agents"], agent.name: agent.to_dict()}
        ledger = {**self.state["ledger"]}
        ledger.setdefault(agent.name, 0.0)
        self._commit({**self.state, "agents": agents, "ledger": ledger})

    def seed(self, ids: dict[str, int]) -> list[Agent]:
        agents = seed_profiles(ids)
        self._commit(
            {
                **self.state,
                "agents": {a.name: a.to_dict() for a in agents},
                "ledger": {a.name: 0.0 for a in agents},
                "open": {},
                "shared_log": {"round": 0, "entries": []},
            }
        )
        return agents

    # ------------------------------------------------------------ shared log

    def _new_round(self) -> None:
        current = self.state["shared_log"]["round"]
        self._commit({**self.state, "shared_log": {"round": current + 1, "entries": []}})

    async def _post_and_log(self, agent_name: str, event: dict) -> None:
        """Post to the floor and remember exactly which fields were sent."""
        entries = [*self.state["shared_log"]["entries"], {"agent": agent_name, "sent": event}]
        self._commit({**self.state, "shared_log": {**self.state["shared_log"], "entries": entries}})
        await self.post(event)

    def shared_report(self) -> str:
        log_data = self.state["shared_log"]
        if not log_data["entries"]:
            return "nothing shared yet. run /bailout first."
        lines = [f"**round {log_data['round']} - what each agent sent to the floor**"]
        for entry in log_data["entries"]:
            fields = ", ".join(f"{k}={v}" for k, v in entry["sent"].items() if k != "type")
            lines.append(f"- {entry['agent']} ({entry['sent']['type']}): {fields}")
        lines.append("")
        lines.append("**what stayed private (never posted, never sent to the LLM):**")
        for field_name in PRIVATE_FIELDS:
            lines.append(f"- {field_name}")
        return "\n".join(lines)

    def ledger_report(self) -> str:
        ledger = self.state["ledger"]
        if not ledger:
            return "ledger is empty. run /seed."
        rows = [f"- {name}: {bal:+.1f}" for name, bal in sorted(ledger.items(), key=lambda kv: -kv[1])]
        open_rows = [f"- {task}: {o['helper']} for {o['requester']} ({o['price']} credits, pending /done)" for task, o in self.state["open"].items()]
        text = "**ledger** (credits are favors, sums to zero)\n" + "\n".join(rows)
        if open_rows:
            text += "\n\n**open favors**\n" + "\n".join(open_rows)
        return text

    # ------------------------------------------------------------ /bailout

    async def bailout(self, requester: Agent, tasks: list[str]) -> str:
        tasks = list(dict.fromkeys(t.strip().lower() for t in tasks if t.strip()))  # dedupe, keep order
        already_open = [t for t in tasks if t in self.state["open"]]
        tasks = [t for t in tasks if t not in already_open]
        if not tasks:
            if already_open:
                return f"{', '.join(already_open)} already has an open favor. /done it first."
            return "which tasks? e.g. /bailout dishes, trash"

        # gate 1: does the human actually want to ask?
        answer = await self.ask(requester.discord_id, f"ask the house for cover on **{', '.join(tasks)}**? ✅/❌")
        if answer != "YES":
            return f"ok, not asking anyone. (your answer: {answer})"

        self._new_round()
        await self._post_and_log(
            requester.name,
            {"type": "request", "name": requester.name, "busy_band": busy_band(requester.busy), "tasks": tasks},
        )

        results = [f"skipped {t}, already has an open favor" for t in already_open]
        for task in tasks:
            results.append(await self._auction_task(requester, task))
        return "\n".join(results)

    async def _collect_bids(self, requester: Agent, task: str) -> list[Bid]:
        bids = []
        for helper in self.agents():
            if helper.name == requester.name:
                continue
            helper = await self._check_cap(helper, task)
            if helper is None:
                continue
            shared = helper.share([task], self.balance(helper.name))
            await self._post_and_log(
                helper.name,
                {"type": "bid", "name": shared["name"], "task": task, "bid": shared["bids"][task], "busy_band": shared["busy_band"]},
            )
            bids.append(Bid(name=shared["name"], cost=shared["bids"][task], balance=shared["ledger_balance"]))
        return bids

    async def _check_cap(self, helper: Agent, task: str) -> Optional[Agent]:
        """If the bid would be over the human's cap, ask before bidding. Returns None to sit out."""
        if not helper.bid_exceeds_cap(task):
            return helper
        answer = await self.ask(
            helper.discord_id,
            f"my bid for **{task}** would be {helper.bid(task)} credits, over your cap of {helper.cap}. bid anyway? ✅/❌ (or reply 'cap 7' to raise the cap)",
        )
        if answer.startswith("CAP "):
            # re-read in case the human changed busy/dislike while we waited on the DM
            helper = (self.agent(helper.name) or helper).with_cap(float(answer.split()[1]))
            self.save_agent(helper)
            return helper if not helper.bid_exceeds_cap(task) else None
        return helper if answer == "YES" else None

    async def _auction_task(self, requester: Agent, task: str) -> str:
        remaining = await self._collect_bids(requester, task)
        while remaining:
            result = run_auction(remaining)
            winner = self.agent(result.winner)
            # gate 2: the winner's human has to actually take it
            answer = await self.ask(winner.discord_id, f"you won **{task}** for {result.price} credits. take it? ✅/❌")
            if answer == "YES":
                self._open_favor(task, requester.name, winner.name, result.price)
                await self._post_and_log(
                    winner.name, {"type": "result", "name": winner.name, "task": task, "price": result.price}
                )
                return f"{task}: {winner.name} for {result.price} credits (settles on /done {task})"
            await self._post_and_log(winner.name, {"type": "declined", "name": winner.name, "task": task})
            remaining = [b for b in remaining if b.name != winner.name]
        return f"{task}: nobody could take it this time."

    def _open_favor(self, task: str, requester: str, helper: str, price: float) -> None:
        new_open = {**self.state["open"], task: {"requester": requester, "helper": helper, "price": price}}
        self._commit({**self.state, "open": new_open})

    # ------------------------------------------------------------ /done

    async def done(self, caller: Agent, task: str) -> str:
        task = task.strip().lower()
        favor = self.state["open"].get(task)
        if favor is None:
            return f"no open favor for {task}."
        if caller.name not in (favor["helper"], favor["requester"]):
            return f"only {favor['helper']} or {favor['requester']} can close {task}."
        new_ledger = settle(self.state["ledger"], favor["requester"], favor["helper"], favor["price"])
        new_open = {k: v for k, v in self.state["open"].items() if k != task}
        self._commit({**self.state, "ledger": new_ledger, "open": new_open})
        await self._post_and_log(
            favor["helper"], {"type": "settled", "name": favor["helper"], "task": task, "price": favor["price"]}
        )
        return f"{task} done. {favor['helper']} +{favor['price']}, {favor['requester']} -{favor['price']}.\n" + self.ledger_report()

    def open_favor(self, task: str) -> Optional[dict]:
        return self.state["open"].get(task.strip().lower())

    # ------------------------------------------------------------ payback

    async def advance(self, name: str, busy: int) -> str:
        agent = self.agent(name)
        if agent is None:
            return f"no agent called {name}. run /seed."
        agent = agent.with_busy(busy)
        self.save_agent(agent)
        debtors = payback_order(self.state["ledger"], agent.name, agent.busy)
        if not debtors:
            return f"{agent.name} busy -> {agent.busy}. no payback: needs busy >= 8 and a positive balance."
        return await self._payback(agent, debtors[0])

    async def _payback(self, creditor: Agent, debtor_name: str) -> str:
        debtor = self.agent(debtor_name)
        owed = abs(self.balance(debtor_name))
        await self._post_and_log(
            creditor.name,
            {"type": "payback", "name": creditor.name, "busy_band": busy_band(creditor.busy), "debtor": debtor.name, "owed": owed},
        )
        # gate 3, both humans
        a1 = await self.ask(creditor.discord_id, f"you're swamped and **{debtor.name}** owes {owed} credits. call in the favor? ✅/❌")
        if a1 != "YES":
            return f"{creditor.name} didn't want to call it in. ({a1})"
        a2 = await self.ask(debtor.discord_id, f"**{creditor.name}** is swamped and you owe {owed} credits. let your agent offer to take a task back? ✅/❌")
        if a2 != "YES":
            await self._post_and_log(debtor.name, {"type": "declined", "name": debtor.name, "task": "payback"})
            return f"{debtor.name} passed on paying back right now. ({a2})"

        # debtor's agent offers the task it dislikes least (private reasoning, only the pick is shared)
        free_tasks = [t for t in TASKS if t not in self.state["open"]]
        if not free_tasks:
            return f"every task already has an open favor. /done something first, then try again."
        task = min(free_tasks, key=debtor.bid)
        price = debtor.bid(task)
        self._open_favor(task, creditor.name, debtor.name, price)
        await self._post_and_log(debtor.name, {"type": "result", "name": debtor.name, "task": task, "price": price})
        return f"{debtor.name}'s agent offered to take **{task}** off {creditor.name}'s plate for {price} credits. settles on /done {task}."
