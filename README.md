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
- bonus: if a bid would go over your cap, your agent DMs you before bidding. While it
  waits, the floor sees "priya's agent is checking with them before bidding on trash."
  A ❌, a timeout, or an unreadable reply means "priya's agent passed on trash" and the
  auction carries on with the other bids. `/seed` sets everyone's cap to 20 so the
  scripted demo never trips this by accident; `/me cap 5` lowers it if you want to see it.

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
   (send messages, add reactions, and manage messages if you want the Cubbie bridge
   to clean up after itself).
3. Make a channel called `#agent-floor`.
4. `cp .env.example .env` and fill in `DISCORD_TOKEN` and `ANTHROPIC_API_KEY`.
   Setting `GUILD_ID` makes slash commands show up immediately instead of in ~an hour.
5. `python bot.py`
6. Run the tests: `python -m unittest -v`

Without an API key the bot still works: floor lines fall back to plain templates.
Every floor line is one event type (request, cubbie_request, bid, cap_check, passed, awarded, declined,
completed, payback_request, payback_done). The model is told the type and only its share() data, and
its line is checked before posting: no `$`/dollars/paid/money wording, no numbers that
aren't in the event, and an `awarded` line can't claim the task is finished. A line that
fails the check is replaced by the template and logged.
Confirmation DMs never use the model: tap ✅/❌ or type yes/no (or `cap 5`). You get
5 minutes to answer; every answer and timeout is logged with the user and message id.

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
- Jordan wins dishes (5.2 cost) but earns Priya's cost (9.0 credits). Jordan gets a DM:
  "you won dishes for 9.0 credits. take it?" -> ✅  (gate 2). Try ❌ once: Priya gets asked next.
  The floor says "jordan's taking dishes for 9.0 credits." Nothing is finished yet.
  If Priya says ❌ too, the floor says "nobody's taking dishes for maya this time: 2 agents bid,
  but every human passed." and Maya's DM says the same. The count is the only reason given:
  the auction never compares busy levels, so the line never claims it did.
- Same for trash.
```
/shared                                  # exactly what each agent sent, and what it kept
/done dishes                             # (as Jordan) floor: "jordan finished dishes for maya. +9.0 credits to jordan." then the ledger block
/ledger                                  # jordan +9.0, maya -9.0, priya 0.0  (sums to zero)
!advance jordan 9                        # jordan is now swamped and has credits
```
- `#agent-floor`, one line: "jordan's swamped. maya's agent is first in line to help." (Maya owes the most; the amount is never posted.)
- Jordan gets a DM "call in the favor?" and Maya gets "let your agent offer a task back?" (gate 3)
- Maya's agent picks the task Maya minds least and offers it back. Settles on `/done`.

## Cubbie bridge

A Cubbie Skill can start a bailout for you, so busy never has to be typed by hand.
Cubbie posts one line into `#agent-floor` through a Discord **webhook**, and the bot
picks it up:

```
bailout_request user=<discord_user_id> busy=<1-10> tasks=<task1,task2> source=cubbie
```

How it is kept safe (`bridge.py`, tested in `test_bridge.py`):

- **One trusted webhook.** The bot only acts on messages whose `webhook_id` equals
  `BRIDGE_WEBHOOK_ID` from `.env`. Anything else from any other webhook is ignored and
  logged. Leave `BRIDGE_WEBHOOK_ID` empty and the bridge is off.
- **Strict parse.** The line must match that exact shape: four keys in that order,
  numeric user id, integer busy 1-10, up to five letters-only task names (no digits, no
  `@`, so a task can't smuggle a number or a ping onto the floor), `source=cubbie`.
  Anything off (bad busy, unknown user, extra keys, a second line, a request for a user
  whose Cubbie bailout is still running) is ignored and logged with the reason.
- **Gate 1 is skipped.** The student already confirmed in Cubbie, so their agent does not
  DM them "ask the house?" again. Gate 2 (the winner's human) and the cap check still run.
- **The number never hits the floor.** The floor gets one line built from the busy *band*
  only, e.g. "Maya's Cubbie says: swamped this week (busy: high). Agents, bids on dishes, trash please."
  The raw webhook line is deleted right after parsing if the bot has **Manage Messages**
  in `#agent-floor` (it logs a warning if it doesn't). Then the normal auction and award
  flow runs, and the requester gets the result by DM.

Setup: in Discord, `#agent-floor` -> Edit channel -> Integrations -> Webhooks -> New
Webhook, copy the URL. The URL looks like `https://discord.com/api/webhooks/<ID>/<token>`;
put `<ID>` in `BRIDGE_WEBHOOK_ID`. Give the bot the **Manage Messages** permission on the
channel. The Cubbie Skill keeps the full URL (the token part is its secret). Whoever holds
that URL can start a bailout as any seeded roommate, so treat it like a password; the
`user=` field is trusted because the webhook is, not on its own.

### The ntfy relay (when the webhook is blocked)

Discord refuses webhook posts coming from Cubbie's servers, so the Skill does not post
to Discord at all. It publishes the same one line as plain text to a topic on
[ntfy.sh](https://ntfy.sh), and the bot polls that topic:

```
Cubbie Skill  --POST https://ntfy.sh/<topic>-->  ntfy.sh  <--GET /json?poll=1&since=<last id>--  bot (every 3s)
```

Set `RELAY_TOPIC` in `.env` to turn it on (empty = relay off; the webhook path keeps
working either way). Pick a long random name, e.g. `bailout-` plus 16 random letters and
digits (letters, digits, `-`, `_` only; the bot refuses to start on anything else), and
save the same name in Cubbie as `BAILOUT_RELAY_TOKEN`. The topic is the
secret: anyone who knows it can start a bailout as any seeded roommate, so the bot never
writes it to the log, and polling errors are reported by status or exception class only.

What the bot checks on every relay message (`relay.py`, tested in `test_relay.py` and
`test_bot_relay.py`), on top of the strict parse and known-user rules above:

- **Message events only.** ntfy `open`, `keepalive` and `poll_request` events are ignored.
- **No repeats.** Each ntfy message id is handled once; the last 1000 ids are remembered,
  and the next poll asks for messages `since=<last id>` so nothing is fetched twice.
  The first poll after a (re)start only marks what is already in the topic as seen, so a
  restart never runs a bailout the previous process already ran. A line published during
  the restart itself is lost rather than risked twice.
- **Nothing stale.** A message more than 5 minutes old is rejected, so a line that sat in
  ntfy's cache while the bot was down can't start a bailout later.
- **Same in-flight guard.** A second line for a user whose Cubbie bailout is still running
  is rejected, whichever path it came in on.

Every message is logged as `relay: received`, then `relay: parsed` or `relay: rejected`
with the reason, never the topic.

### Cubbie demo script (30 seconds)

Same three seeded players as above. From a terminal, play the Cubbie Skill through the
relay (or run `bailout-link/scripts/post.py`, which does exactly this):

```
export TOPIC="<the RELAY_TOPIC from .env>"
curl -s -d 'bailout_request user=<MAYA_DISCORD_ID> busy=9 tasks=dishes,trash source=cubbie' "https://ntfy.sh/$TOPIC"
```
- Within 3 seconds the bot log says `relay: received`, then `relay: parsed`.
- Maya gets **no** gate 1 DM. The floor says "Maya's Cubbie says: swamped this week (busy: high). Agents, bids on dishes, trash please." Only "high", never "9".
- Jordan's and Priya's agents bid, Jordan gets the gate 2 DM for dishes, same as a `/bailout`.
- Maya gets a DM with the result. `/shared` shows the `cubbie_request` event and that only the band left her agent.

Now show the rejections (watch the bot log for `relay: rejected`, nothing happens on the floor):
```
curl -s -d 'bailout_request user=<MAYA_DISCORD_ID> busy=42 tasks=dishes source=cubbie' "https://ntfy.sh/$TOPIC"   # bad busy
curl -s -d 'bailout_request user=1 busy=9 tasks=dishes source=cubbie' "https://ntfy.sh/$TOPIC"                   # unknown user
curl -s -d 'bailout_request user=<MAYA_DISCORD_ID> busy=9 tasks=dishes source=cubbie' "https://ntfy.sh/$TOPIC"    # again while the first is running: already running
```

The webhook route still works the same way with the Discord webhook URL:
```
export HOOK="https://discord.com/api/webhooks/<ID>/<token>"
curl -s -H 'Content-Type: application/json' -d \
  '{"content": "bailout_request user=<MAYA_DISCORD_ID> busy=9 tasks=dishes,trash source=cubbie"}' "$HOOK"
```
The raw line appears in `#agent-floor` for a blink and is deleted; everything after that is identical.

## What's next

- **Photo proof.** `/done <task>` takes an optional photo. Claude Sonnet looks at it
  and says DONE / NOT_DONE / UNSURE. On UNSURE the requester gets a DM to confirm
  instead of the model deciding. Works now, needs more testing with real messy kitchens.
- **Group-project tasks.** Same market, but the "roommates" are teammates and the
  tasks are "write the intro" / "make the slides".
- **Busy score from our Cubbie skill.** The bridge above takes a busy level from
  Cubbie today. Next is computing it inside Cubbie from calendar + assignment deadlines
  so nobody has to remember to update it.

## Files

- `market.py` - the auction, ledger and payback rule. Pure Python, no LLM.
- `agent.py` - one Agent per person, private profile, `share()`.
- `llm.py` - Claude calls: floor one-liners, reply parsing, photo check.
- `flows.py` - the coordination logic (bailout, done, payback, shared log), Discord-free.
- `gates.py` - DM ask with ✅/❌ (raw reaction events) or a typed yes/no in the same DM. No LLM.
- `bridge.py` - the Cubbie webhook line parser: trusted webhook id, strict format, known user.
- `relay.py` - the ntfy.sh relay: polls the topic, drops repeats and anything over 5 minutes old, then runs the same parser.
- `bailout-link/` - the Cubbie Skill that publishes the bridge line to the relay (the Cubbie side of the bridge). Independent of the bot; its own tests live in `bailout-link/tests/`.
- `store.py` - JSON state file.
- `bot.py` - Discord commands.
- `test_*.py` - unittest. Run `python -m unittest -v`. `test_bridge.py` covers the parser, `test_bot_bridge.py` the webhook listener, `test_relay.py` and `test_bot_relay.py` the ntfy relay.
