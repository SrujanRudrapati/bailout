"""The 'stop and ask the human' part, over Discord DMs.

ask_by_dm() sends a question, adds ✅/❌ reactions, and waits for either a
reaction or a typed reply. Typed replies go through llm.parse_reply(). If the
model says ESCALATE we ask again in plain words instead of guessing.
"""

import asyncio
import logging

import discord

from llm import parse_reply

log = logging.getLogger("bailout.gates")

DEFAULT_TIMEOUT = 180  # seconds a human has to answer
MAX_ATTEMPTS = 2


async def ask_by_dm(bot: discord.Client, user_id: int, question: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    user = await bot.fetch_user(user_id)
    prompt = await user.send(question)
    await prompt.add_reaction("✅")
    await prompt.add_reaction("❌")

    for attempt in range(MAX_ATTEMPTS):
        answer = await _wait_for_answer(bot, user, prompt, timeout)
        if answer == "TIMEOUT":
            await user.send("no answer, so I did nothing.")
            return "NO"
        if answer != "ESCALATE":
            await user.send(f"got it: {answer.lower()}")
            return answer
        if attempt + 1 < MAX_ATTEMPTS:
            await user.send("I couldn't tell what you meant. please reply **yes** or **no** (or 'cap 5' to set a limit).")

    await user.send("still not sure, so I'm not doing anything. you can run the command again.")
    return "ESCALATE"


async def _wait_for_answer(bot: discord.Client, user: discord.User, prompt: discord.Message, timeout: int) -> str:
    def reaction_ok(reaction: discord.Reaction, reactor: discord.User) -> bool:
        return reaction.message.id == prompt.id and reactor.id == user.id and str(reaction.emoji) in ("✅", "❌")

    def message_ok(message: discord.Message) -> bool:
        return message.author.id == user.id and isinstance(message.channel, discord.DMChannel)

    reaction_task = asyncio.ensure_future(bot.wait_for("reaction_add", check=reaction_ok))
    message_task = asyncio.ensure_future(bot.wait_for("message", check=message_ok))
    done, pending = await asyncio.wait({reaction_task, message_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    if not done:
        return "TIMEOUT"

    finished = done.pop()
    if finished is reaction_task:
        reaction, _ = finished.result()
        return "YES" if str(reaction.emoji) == "✅" else "NO"
    message = finished.result()
    # parse_reply may call Claude, keep it off the event loop
    return await asyncio.to_thread(parse_reply, message.content)
