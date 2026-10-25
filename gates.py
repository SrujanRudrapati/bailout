"""The 'stop and ask the human' part, over Discord DMs.

ask_by_dm() sends a question, adds ✅/❌ reactions, and waits for either a
reaction or a typed reply. Typed replies go through llm.parse_reply(). If the
model says ESCALATE we ask again in plain words instead of guessing.

One gate per user at a time (asyncio.Lock), so a typed "yes" can only ever
answer the question that's actually in front of that person.
"""

import asyncio
import logging
from collections import defaultdict

import discord

from llm import parse_reply

log = logging.getLogger("bailout.gates")

DEFAULT_TIMEOUT = 180  # seconds a human has to answer
MAX_ATTEMPTS = 2

_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


async def ask_by_dm(bot: discord.Client, user_id: int, question: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    async with _user_locks[user_id]:
        return await _ask(bot, user_id, question, timeout)


async def _ask(bot: discord.Client, user_id: int, question: str, timeout: int) -> str:
    user = await bot.fetch_user(user_id)
    prompt_ids: set[int] = set()  # filled in after we send, read by the reaction check

    for attempt in range(MAX_ATTEMPTS):
        # register listeners BEFORE sending so a fast answer can't slip through the gap
        waiters = _listeners(bot, user, prompt_ids)
        if attempt == 0:
            prompt = await user.send(question)
            prompt_ids.add(prompt.id)
            await prompt.add_reaction("✅")
            await prompt.add_reaction("❌")
        else:
            await user.send("I couldn't tell what you meant. please reply **yes** or **no** (or 'cap 5' to set a limit).")

        answer = await _wait_for_answer(waiters, timeout)
        if answer == "TIMEOUT":
            await user.send("no answer, so I did nothing.")
            return "NO"
        if answer != "ESCALATE":
            await user.send(f"got it: {answer.lower()}")
            return answer

    await user.send("still not sure, so I'm not doing anything. you can run the command again.")
    return "ESCALATE"


def _listeners(bot: discord.Client, user: discord.User, prompt_ids: set[int]):
    def reaction_ok(reaction: discord.Reaction, reactor: discord.User) -> bool:
        return reaction.message.id in prompt_ids and reactor.id == user.id and str(reaction.emoji) in ("✅", "❌")

    def message_ok(message: discord.Message) -> bool:
        return message.author.id == user.id and isinstance(message.channel, discord.DMChannel)

    reaction_task = asyncio.ensure_future(bot.wait_for("reaction_add", check=reaction_ok))
    message_task = asyncio.ensure_future(bot.wait_for("message", check=message_ok))
    return reaction_task, message_task


async def _wait_for_answer(waiters, timeout: int) -> str:
    reaction_task, message_task = waiters
    done, pending = await asyncio.wait({reaction_task, message_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    if not done:
        return "TIMEOUT"
    if reaction_task in done:
        reaction, _ = reaction_task.result()
        return "YES" if str(reaction.emoji) == "✅" else "NO"
    message = message_task.result()
    # parse_reply may call Claude, keep it off the event loop
    return await asyncio.to_thread(parse_reply, message.content)
