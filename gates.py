"""The 'stop and ask the human' part, over Discord DMs.

ask_by_dm() sends a question, adds ✅/❌ reactions, and waits for either a
reaction on that exact message or a typed reply in that same DM. Everything
is plain matching, no LLM: ✅/❌ by emoji name, typed replies by keyword
(yes/no/cap N). Anything we can't read gets one plain re-ask, then ESCALATE.

Reactions are read from the raw gateway event (on_raw_reaction_add), not the
cached-message one, because DM messages the bot just sent are often not in
discord.py's message cache, so the cached event never fires.

One gate per user at a time (asyncio.Lock), so a typed "yes" can only ever
answer the question that's actually in front of that person.
"""

import asyncio
import logging
import re
from collections import defaultdict
from typing import Optional

import discord

log = logging.getLogger("bailout.gates")

DEFAULT_TIMEOUT = 300  # seconds a human has to answer
MAX_ATTEMPTS = 2
YES_EMOJI = "✅"
NO_EMOJI = "❌"
VARIATION_SELECTOR = "️"

_YES_WORDS = frozenset({"yes", "y", "ya", "yep", "yeah", "yup", "ok", "okay", "sure", "do it", "go", YES_EMOJI, "👍"})
_NO_WORDS = frozenset({"no", "n", "nope", "nah", "pass", "skip", NO_EMOJI, "👎"})
_CAP_RE = re.compile(r"\bcap\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)
_TRAILING_PUNCT = ".!,"

_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


# ---------------------------------------------------------------- pure matching


def emoji_answer(emoji_name: Optional[str]) -> Optional[str]:
    """'YES' for ✅, 'NO' for ❌ (with or without the variation selector), else None."""
    if not emoji_name:
        return None
    name = emoji_name.replace(VARIATION_SELECTOR, "")
    if name == YES_EMOJI:
        return "YES"
    if name == NO_EMOJI:
        return "NO"
    return None


def parse_confirmation(text: str) -> Optional[str]:
    """Plain keyword matching for a typed DM reply: 'YES', 'NO', 'CAP <n>' or None."""
    cleaned = text.strip().lower().replace(VARIATION_SELECTOR, "").rstrip(_TRAILING_PUNCT).strip()
    if cleaned in _YES_WORDS:
        return "YES"
    if cleaned in _NO_WORDS:
        return "NO"
    cap = _CAP_RE.search(cleaned)
    if cap:
        return f"CAP {cap.group(1)}"
    return None


# ---------------------------------------------------------------- the gate


async def ask_by_dm(bot: discord.Client, user_id: int, question: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    async with _user_locks[user_id]:
        return await _ask(bot, user_id, question, timeout)


async def _ask(bot: discord.Client, user_id: int, question: str, timeout: int) -> str:
    user = await bot.fetch_user(user_id)
    prompt_ids: set[int] = set()  # filled in after we send, read by the reaction check
    channel_id: Optional[int] = None

    for attempt in range(MAX_ATTEMPTS):
        # register listeners BEFORE sending so a fast answer can't slip through the gap
        waiters = _listeners(bot, user_id, prompt_ids, lambda: channel_id)
        if attempt == 0:
            prompt = await user.send(question)
            prompt_ids.add(prompt.id)
            channel_id = prompt.channel.id
            await prompt.add_reaction(YES_EMOJI)
            await prompt.add_reaction(NO_EMOJI)
            log.info("confirmation sent to user %s, message %s: %s", user_id, prompt.id, question)
        else:
            retry = await user.send("I couldn't tell what you meant. please reply **yes** or **no** (or 'cap 5' to set a limit).")
            log.info("confirmation re-asked to user %s, message %s", user_id, retry.id)

        answer = await _wait_for_answer(waiters, timeout, user_id, prompt_ids)
        if answer == "TIMEOUT":
            await user.send("no answer, so I did nothing.")
            return "NO"
        if answer != "ESCALATE":
            await user.send(f"got it: {answer.lower()}")
            return answer

    log.info("confirmation from user %s unreadable twice, escalating (messages %s)", user_id, sorted(prompt_ids))
    await user.send("still not sure, so I'm not doing anything. you can run the command again.")
    return "ESCALATE"


def _listeners(bot: discord.Client, user_id: int, prompt_ids: set[int], channel_id):
    """Start waiting for a raw reaction on the prompt or a typed reply in the same DM.

    channel_id is a callable because the DM channel isn't known until the first send.
    """

    def reaction_ok(payload: discord.RawReactionActionEvent) -> bool:
        if payload.message_id not in prompt_ids:
            return False
        if bot.user is not None and payload.user_id == bot.user.id:
            return False  # our own ✅/❌ we just added
        if payload.user_id != user_id:
            log.info("reaction on message %s from user %s, expected %s, ignoring", payload.message_id, payload.user_id, user_id)
            return False
        return emoji_answer(payload.emoji.name) is not None

    def message_ok(message: discord.Message) -> bool:
        return message.author.id == user_id and message.guild is None and message.channel.id == channel_id()

    reaction_task = asyncio.ensure_future(bot.wait_for("raw_reaction_add", check=reaction_ok))
    message_task = asyncio.ensure_future(bot.wait_for("message", check=message_ok))
    return reaction_task, message_task


async def _wait_for_answer(waiters, timeout: int, user_id: int, prompt_ids: set[int]) -> str:
    reaction_task, message_task = waiters
    done, pending = await asyncio.wait({reaction_task, message_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    if not done:
        log.warning("confirmation timed out after %ss for user %s, messages %s", timeout, user_id, sorted(prompt_ids))
        return "TIMEOUT"
    if reaction_task in done:
        payload = reaction_task.result()
        answer = emoji_answer(payload.emoji.name)
        log.info("confirmation received from user %s via reaction %r on message %s -> %s", user_id, payload.emoji.name, payload.message_id, answer)
        return answer
    message = message_task.result()
    answer = parse_confirmation(message.content)
    log.info("confirmation received from user %s via typed reply %r (message %s) -> %s", user_id, message.content, message.id, answer or "ESCALATE")
    return answer or "ESCALATE"
