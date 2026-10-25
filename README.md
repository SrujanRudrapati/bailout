# Bailout

Roommate chore coverage, but every roommate has their own agent. Built for Track 2
(Agent-to-Agent Coordination) of the Cubbie Hackathon.

## What it does

When one of us is swamped, our agent asks the other agents to cover our chores.
The other agents bid privately, the lowest bid wins, and the favor is written down
as credits on a ledger. Credits aren't money, they're IOUs. Later, when the helper
is the one drowning, their agent goes to whoever owes them first.

Everything happens in Discord: agents talk to each other in `#agent-floor`, and each
agent talks to its own human in DMs.

## The three judging points (and where to see them)

**1. Conflicting constraints.** Three people, three different levels of busy, three
different sets of chores they hate. The market resolves it with a Vickrey reverse
auction (`market.py`): each helper's private cost is `dislike * (1 + busy/10)`, the
lowest cost wins, and the winner is paid the *second* lowest cost. That means
nobody gains by lying about how much they hate dishes. Ties go to whoever has the
lowest ledger balance, so the person who's been helped the most helps next.
*Watch for it:* the bids on `#agent-floor` after `/bailout`, and the price the winner
actually gets (it's not their own bid).

**2. What agents share vs. keep private.** `Agent.share()` is the only way data
leaves an agent, and it returns exactly four fields. Raw dislike scores, reasons
("two midterms"), and availability never hit the floor and are never sent to Claude.
The floor only ever sees a busy *band* (low/medium/high), not the number.
*Watch for it:* `/shared` prints exactly which fields each agent sent last round and
a list of what stayed private. `test_agent.py` and `test_flows.py` check this.

**3. When agents stop and ask the human.** Three gates, all over DM, all with ✅/❌:
- gate 1: before your agent asks the house for anything ("ask for cover on dishes?")
- gate 2: before the winner is committed ("you won dishes for 9.0 credits. take it?")
- gate 3: before a favor is called in, both the creditor and the debtor get asked
- bonus: if a bid would go over your cap, your agent DMs you before bidding

You can also just type a reply instead of reacting. Claude Haiku reads it and
returns YES / NO / CAP n / ESCALATE. On ESCALATE the agent doesn't guess, it asks
again in plain words. *Watch for it:* the DMs during the demo, and reply something
vague like "hmm depends" to see the escalation.

## What agents share vs. keep

| shared on the floor (and with the LLM) | kept inside the agent |
|---|---|
| `name` | exact busy number (1-10) |
| `busy_band` (low / medium / high) | dislike score per task |
| `bids` (credits, per task asked) | reasons ("two midterms") |
| `ledger_balance` | availability |
| | cap (the agent uses it, nobody else sees it) |

## Setup

1. Python 3.12+, then `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. Make a Discord bot at https://discord.com/developers, turn on the **Message Content**
   intent, invite it to your server with `applications.commands` + `bot` scopes
   (send messages, add reactions).
3. Make a channel called `#agent-floor`.
4. `cp .env.example .env` and fill in `DISCORD_TOKEN` and `ANTHROPIC_API_KEY`.
   Setting `GUILD_ID` makes slash commands show up immediately instead of in ~an hour.
5. `python bot.py`
6. Run the tests: `python -m unittest -v`

Without an API key the bot still works: floor lines fall back to plain templates and
typed DM replies only count if they're an obvious yes/no.

## 60-second demo script

Three people (or three accounts) in the server. Everyone has DMs from the bot open.

```
/seed maya:@A jordan:@B priya:@C        # loads the three profiles, ledger at zero
                                         # (or just /seed, if SEED_*_ID are set in .env)
/ledger                                  # everyone at 0.0
```
As Maya (busy 9, two midterms):
```
/bailout dishes, trash
```
- Maya gets a DM: "ask the house for cover on dishes, trash?" -> ✅  (gate 1)
- `#agent-floor`: Maya's agent asks for cover. Only "high" shows up, never "9" or "midterms".
- Jordan's and Priya's agents each post one bid per task (credits + busy band).
- Jordan wins dishes (5.2 cost) but is paid Priya's cost (9.0). Jordan gets a DM:
  "you won dishes for 9.0 credits. take it?" -> ✅  (gate 2). Try ❌ once: Priya gets asked next.
- Same for trash.
```
/shared                                  # exactly what each agent sent, and what it kept
/done dishes                             # (as Jordan) credits settle, ledger posts to floor
/ledger                                  # jordan +9.0, maya -9.0, priya 0.0  (sums to zero)
!advance jordan 9                        # jordan is now swamped and has credits
```
- `#agent-floor`: Jordan's agent calls in the favor from the biggest debtor (Maya).
- Jordan gets a DM "call in the favor?" and Maya gets "let your agent offer a task back?" (gate 3)
- Maya's agent picks the task Maya minds least and offers it back. Settles on `/done`.

## What's next

- **Photo proof.** `/done <task>` takes an optional photo. Claude Sonnet looks at it
  and says DONE / NOT_DONE / UNSURE. On UNSURE the requester gets a DM to confirm
  instead of the model deciding. Works now, needs more testing with real messy kitchens.
- **Group-project tasks.** Same market, but the "roommates" are teammates and the
  tasks are "write the intro" / "make the slides".
- **Busy score from our Cubbie skill.** Right now busy is set by hand with `/me busy`.
  The plan is to compute it from calendar + assignment deadlines so nobody has to
  remember to update it.

## Files

- `market.py` - the auction, ledger and payback rule. Pure Python, no LLM.
- `agent.py` - one Agent per person, private profile, `share()`.
- `llm.py` - Claude calls: floor one-liners, reply parsing, photo check.
- `flows.py` - the coordination logic (bailout, done, payback, shared log), Discord-free.
- `gates.py` - DM ask with ✅/❌ or typed reply.
- `store.py` - JSON state file.
- `bot.py` - Discord commands.
- `test_*.py` - unittest. Run `python -m unittest -v`.
