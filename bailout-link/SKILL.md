---
name: bailout-link
description: Works out how busy the student is this week from Canvas and, with their ok, asks their roommates' Bailout agents on Discord to cover chores. Use whenever the student says they are drowning, swamped or buried, asks for help with chores (dishes, trash, cooking, cleaning), says "bailout" or "get me help with", or wants to ask the house for help. Also use when they mention roommates covering for them during a heavy week.
---

# Bailout Link

We live together and we each have a Bailout agent on our house Discord. When one of us is
slammed, our agent asks the other agents to cover our chores, they bid, lowest wins, and the
favor gets written down as credits. This Skill is the bridge from Cubbie to that: it looks at
what you have due, turns it into one number, asks you if that number can go out, and posts the
request. That's it.

## When to use

- "I'm drowning"
- "get me help with dishes and trash"
- "bailout dishes"
- "ask the house for help"
- "can someone cover trash this week, I have two midterms"

Don't use it for study plans, reminders, or anything about the chores themselves. It only
asks the house for cover.

## What leaves the phone

Exactly one line goes to Discord: the student's Discord user id, the busy number (1-10), the
chore names, and the word cubbie. No course names, no assignment names, no grades, no due
dates. Say that plainly when you ask for the ok, because the whole point of the house agents
is that nobody has to explain why they're busy.

## Steps

1. Figure out the chores. If the student named them ("dishes and trash"), use those. If they
   only said they're drowning, ask once: "what do you want covered? dishes, trash, cooking?"
   One word each, letters only, up to five. If a chore has a space in it ("take out trash")
   the script turns it into take-out-trash, that's fine.

2. Read the student's Canvas assignments, quizzes and exams due in the next 7 days, with
   their points (or weight, if that's what Canvas shows). If Canvas is not connected, text
   "connect Canvas first so I can see how busy your week is, then say bailout again" and
   stop. Don't guess a busy number.

3. Write what Canvas returned to a file as a JSON list. Each item: `name`, `due` (ISO 8601
   date-time), `points` or `weight`, `type` (assignment, quiz or exam), and `submitted` if
   Canvas says it's already turned in. Nothing due is an empty list `[]`, which is fine and
   gives busy 1. Then run, from this Skill's folder:

   ```bash
   python3 scripts/busy.py --now "<the student's local date and time, ISO 8601>" --input <that file>
   ```

   Pass `--now` because the sandbox clock may be in another time zone. The first line of
   output is `busy=X`. Exit code 2 means the file was unreadable; fix it and run again, don't
   make a number up. To see it work without Canvas: `python3 scripts/busy.py --sample`.

4. Ask before anything leaves. Text exactly:
   "you're busy X/10 this week. share just that with your house's bailout agents? nothing else leaves."
   Only go on if they say yes. Anything else ("hmm", "not now", no reply): text "ok, nothing
   sent" and stop.

5. Get their Discord user id. If you remember it from before, use it. If not, ask once:
   "what's your Discord user id? (Discord > settings > advanced > developer mode, then
   right-click your name > copy user id. it's a long number)" and remember it for next time.
   It's digits only; if what they send isn't, ask again once.

6. Post the request. The house's webhook URL is saved in Cubbie as `BAILOUT_HOOK_TOKEN`
   (see Key below). Run, from this Skill's folder:

   ```bash
   BAILOUT_HOOK_TOKEN="$BAILOUT_HOOK_TOKEN" python3 scripts/post.py --user <id> --busy <X> --tasks <dishes,trash>
   ```

   It posts the one line `bailout_request user=<id> busy=<X> tasks=<tasks> source=cubbie`
   and prints a short result. Exit code 0 means it went.

7. Text: "sent. your roommates' agents are bidding now. check #agent-floor."

## Key

This Skill needs the house's Bailout webhook URL, saved in Cubbie as `BAILOUT_HOOK_TOKEN`.
Whoever runs the house bot makes it in Discord: #agent-floor > Edit channel > Integrations >
Webhooks > New Webhook > Copy URL (how-to: https://support.discord.com/hc/en-us/articles/228383668),
and shares it with the roommates. If it isn't saved yet, send the student the secure page to
save it. Never ask them to paste it in the chat, it's a password for posting as the house.
The script only ever sends it to discord.com.

## When something fails

| What happened | What to text |
| --- | --- |
| Canvas not connected | "connect Canvas first so I can see how busy your week is, then say bailout again" |
| `post.py` exit 3 (webhook not set or not a Discord webhook URL) | "the house webhook isn't set up yet. ask whoever runs the bailout bot for the webhook URL, then save it when I send the secure link" |
| `post.py` exit 4 (post failed) | "couldn't reach Discord right now, nothing was sent. try again in a bit?" Try once more if they say yes, not more. |
| `post.py` exit 2 (bad id or chore names) | fix what the error says (digits-only id, letters-only chores, max five) and run again |
| The student says no at step 4 | "ok, nothing sent" |

Never send without the yes at step 4, and never send anything other than that one line.

## Replies

Short, plain text, lowercase like a text from a roommate. One idea per message. No
Markdown, no tables. The three fixed lines above (the ask, the "sent", the failures) are
what the student should see; don't pad them with the assignment list or the math.

## Files

- `scripts/busy.py` - busy level from the Canvas items. Standard library only.
- `scripts/post.py` - posts the one line to the webhook. Standard library only.
- `assets/sample_canvas.json` - a fake week for `--sample`.
- `tests/` - `python3 -m unittest discover -s tests` from this folder.
