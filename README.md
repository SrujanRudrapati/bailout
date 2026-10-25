# Bailout

Every roommate has their own agent. When you're swamped, yours asks the others' to cover your
chores, the cheapest bid wins, and the favor is written down as credits so it comes back around.

**Plain code decides who wins and what it costs. The language model only writes the messages.**

Built for Track 2 (Agent-to-Agent Coordination) of the Cubbie Hackathon. Everything happens in
Discord: agents talk to each other in `#agent-floor`, and each agent talks to its own human in DMs.

## Who sees what

| thing | your agent | other agents | the floor |
|---|---|---|---|
| exact busy level (1-10) | keeps it, prices bids with it | never | never, only the band |
| dislikes, per chore (1-10) | keeps them, turns them into bids | never | never |
| cap (max bid before asking you) | uses it before every bid | never | never. it hears "an agent is checking with its human", no name |
| reasons ("two midterms") | keeps them, never sends them anywhere | never | never |
| who declined | tells *you*, by DM, when it was your request ("jordan passed, so priya has it") | never | never. it hears "one agent passed on dishes. next agent is up." |
| busy band (low / medium / high) | shares it | see it | sees it |
| bids (credits, per task asked) | shares them | see them | sees them |
| balance | shares it (it breaks ties: the most-helped helps next) | see it | `/ledger` prints it. the payback line never says the amount |

`Agent.share()` is the only way data leaves an agent, and it returns exactly four fields: `name`,
`busy_band`, `bids`, `ledger_balance`. `/shared` prints what each agent sent last round and
the list of what stayed private. Lines the auction posts on nobody's behalf (cap checks,
sit-outs, declines) show up under `auction` with no name in them.

How the code decides (`market.py`, no LLM): each helper's private cost is
`dislike * (1 + busy/10)`, the lowest cost wins, and the winner is paid the *second* lowest
cost (a Vickrey reverse auction, so nobody gains by lying about how much they hate dishes).
If nobody bids, the reason is picked by code too, from the bid count and the busy bands
already on the floor: "nobody's less busy than Maya this week" or "every agent's bid was
over its human's limit". Never a number.

What the model does: Claude Haiku turns each event into one casual line for the floor. It is
told the event type and only the share() data, and the line is checked before posting: no
`$`/dollars/paid/money wording, no number that isn't in the event, no name on an anonymous
event, an `awarded` line can't claim the task is finished, a payback line can't demand. A
line that fails the check is replaced by the plain template and logged. Without an API key
the templates are used for everything. Confirmation DMs never use the model.

Three places the agents stop and ask the human, all by DM, all with ✅/❌ or a typed yes/no:
- gate 1: before your agent asks the house for anything ("ask the house for cover on dishes?")
- gate 2: before the winner is committed ("you won dishes for 9.0 credits. take it?")
- gate 3: before a favor is called in, both the creditor and the debtor get asked
- and if a bid would go over your cap, your agent DMs you before bidding. A ❌, a timeout,
  or an unreadable reply means it sits out, and the auction carries on with the other bids.

## Setup for a new roommate

1. In the house server, run `/join`. You get a private reply: your agent starts with busy 5,
   every chore at dislike 5, a cap of 20 credits and a balance of 0.
2. Make it bid the way you would: `/me busy 7`, `/me dislike dishes 8`, `/me cap 12`. The
   replies are private, and the numbers stay with your agent.
3. Allow DMs from the bot. Every "are you sure?" comes by DM.
4. Optional, the Cubbie bridge: add the `bailout-link/` Skill to your Cubbie and save the
   house's relay topic as `BAILOUT_RELAY_TOKEN` (ask whoever runs the bot). Then "I'm
   drowning, cover dishes and trash" in Cubbie starts a bailout for you, busy level included,
   and you skip gate 1 because you already said yes in Cubbie.

`/help` lists every command in one line each.

## Setup for a new house

1. Python 3.12+, then `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. Make a Discord app at https://discord.com/developers with a bot user. Turn on the **Message
   Content** intent. Invite it with the `applications.commands` + `bot` scopes and the send
   messages, add reactions and (optional, for the webhook bridge) manage messages permissions.
3. Make a channel called `#agent-floor`.
4. `cp .env.example .env` and fill in `DISCORD_TOKEN` and `ANTHROPIC_API_KEY` (leave the key
   empty and the floor uses plain templates). `GUILD_ID` makes slash commands show up right
   away instead of in ~an hour. `RELAY_TOPIC` turns on the Cubbie bridge (below).
   `SEED_*_ID` are only for the scripted demo.
5. `python bot.py`
6. Run the tests: `python -m unittest -v` here, and `python -m unittest discover -s tests` in
   `bailout-link/`.

Everyone then runs `/join`. The auction, ledger, payback rule and bridge work for any number of
roommates; `/seed` is only there for the three-person demo below, and it keeps anyone who joined.

## Cubbie bridge

A Cubbie Skill (`bailout-link/`) works out how busy you are from Canvas, asks you once, and
publishes exactly one line:

```
bailout_request user=<discord_user_id> busy=<1-10> tasks=<task1,task2> source=cubbie
```

Discord refuses webhook posts coming from Cubbie's servers, so the line travels through a
topic on [ntfy.sh](https://ntfy.sh) instead. The Skill POSTs it as plain text to
`https://ntfy.sh/<topic>`, and the bot polls that topic every 3 seconds. The topic name is the
house's secret: pick a long random one (`bailout-` plus 16 random letters and digits), put it
in `.env` as `RELAY_TOPIC`, and save the same name in Cubbie as `BAILOUT_RELAY_TOKEN`. Anyone
who knows it can start a bailout as any roommate, so the bot never writes it to the log.

What the bot checks on every relay message (`relay.py`, `bridge.py`):
- it is a `message` event, with an id the bot hasn't handled yet (the last 1000 are remembered,
  and the first poll after a restart only marks what is already there as seen, so a restart
  never replays a bailout)
- it is under 5 minutes old, so a line that sat in ntfy's cache can't start a bailout later
- it matches that exact shape: numeric user id, integer busy 1-10, up to five letters-only
  task names (no digits, no `@`), `source=cubbie`
- the user has an agent (`/join`), and doesn't already have a Cubbie bailout running

Then gate 1 is skipped (you already confirmed in Cubbie), your busy level is saved on your
agent, and the floor gets one line built from the band only: "Maya's Cubbie says: swamped this
week (busy: high). Agents, bids on dishes, trash please." Every task in the line is auctioned,
gate 2 and the cap check still run, and the result comes back to you by DM.

The webhook route still exists for servers where it works: set `BRIDGE_WEBHOOK_ID` to the id
from a `#agent-floor` webhook URL and the bot accepts the same line from that webhook only,
deleting the raw message right after parsing.

## 60-second demo script

Three people (or three accounts) in the server, DMs from the bot open.

```
/seed maya:@A jordan:@B priya:@C        # the three scripted profiles, every balance at zero
                                         # (or just /seed, if SEED_*_ID are set in .env)
/ledger                                  # everyone at 0.0
```
As Maya (busy 9, two midterms):
```
/bailout dishes, trash
```
- Maya gets a DM: "ask the house for cover on dishes, trash?" -> ✅ (gate 1)
- `#agent-floor`: "Maya is high on time. anyone able to cover dishes, trash?" Only "high", never "9" or "midterms".
- Jordan's and Priya's agents each post one bid per task: "Jordan can do dishes for 5.2 credits (busy: low)."
- Jordan wins dishes (cost 5.2) but is paid Priya's cost. Jordan gets a DM: "you won dishes for
  9.0 credits. take it?" -> ✅ (gate 2). The floor says "Jordan's taking dishes for 9.0 credits."
  Nothing is finished yet.
- Try ❌ once instead: the floor says "one agent passed on dishes. next agent is up." and
  Priya gets the DM. Only Maya's own reply says "dishes: jordan passed, so priya has it for 9.0 credits".
- If Priya says ❌ too: "nobody's taking dishes for Maya this time: 2 agents bid, but every human passed."
- Same for trash. To see the cap check, run `/me cap 5` as Priya first: the floor says "an agent
  is checking with its human before bidding on trash." while Priya's DM waits, and if she says ❌,
  "one agent is sitting out trash." Nobody on the floor is told it was Priya.
```
/shared                                  # exactly what each agent sent, and what it kept
/done dishes                             # as Jordan. floor: "Jordan finished dishes for Maya. +9.0 credits to Jordan." reply: the ledger
/ledger                                  # jordan +9.0, maya -9.0, priya 0.0 (sums to zero)
!advance jordan 9                        # jordan is now swamped and has credits
```
- `#agent-floor`, one line: "Jordan's swamped. Maya's agent is first in line to help." (Maya owes the most; the amount is never posted.)
- Jordan gets a DM "call in the favor?" and Maya gets "let your agent offer a task back?" (gate 3)
- Maya's agent picks the task Maya minds least and offers it back: "Maya's taking trash off
  Jordan's plate for ... credits. paying back." Settles on `/done`. The result of `!advance` goes to Jordan's DM.

Thirty more seconds, the Cubbie bridge. From a terminal, play the Skill through the relay
(or run `bailout-link/scripts/post.py`, which does exactly this):
```
export TOPIC="<the RELAY_TOPIC from .env>"
curl -s -d 'bailout_request user=<MAYA_DISCORD_ID> busy=9 tasks=dishes,trash source=cubbie' "https://ntfy.sh/$TOPIC"
```
- Within 3 seconds the bot log says `relay: received`, then `relay: parsed`. Maya gets **no**
  gate 1 DM. The floor says "Maya's Cubbie says: swamped this week (busy: high). Agents, bids on
  dishes, trash please." Both tasks are auctioned, Jordan gets the gate 2 DM, Maya gets the result by DM.
- Then the rejections, in the bot log only (`relay: rejected`), nothing on the floor:
```
curl -s -d 'bailout_request user=<MAYA_DISCORD_ID> busy=42 tasks=dishes source=cubbie' "https://ntfy.sh/$TOPIC"   # bad busy
curl -s -d 'bailout_request user=1 busy=9 tasks=dishes source=cubbie' "https://ntfy.sh/$TOPIC"                   # unknown user
```

## Files

- `market.py` - the auction, ledger and payback rule. Pure Python, no LLM.
- `agent.py` - one Agent per person, private profile, `share()`.
- `flows.py` - the coordination logic (join, bailout, done, payback, shared log), Discord-free.
- `llm.py` - Claude calls: floor one-liners and their checks, reply parsing, photo check.
- `gates.py` - DM ask with ✅/❌ or a typed yes/no in the same DM. No LLM.
- `bridge.py` - the Cubbie line parser: strict format, known user.
- `relay.py` - the ntfy.sh relay: polls the topic, drops repeats and anything over 5 minutes old.
- `bailout-link/` - the Cubbie Skill (busy score from Canvas, one line to the relay). Its own tests live in `bailout-link/tests/`.
- `store.py` - JSON state file. `bot.py` - Discord commands. `test_*.py` - `python -m unittest -v`.
