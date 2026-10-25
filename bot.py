"""Bailout - the Discord bot.

Slash commands: /seed, /me busy|dislike|cap, /bailout, /done, /ledger, /shared
Prefix command:  !advance <name> <busy>   (demo: trigger the payback rule)

All the logic lives in flows.py. This file just maps Discord to it.
"""

import asyncio
import logging
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from flows import Hub
from gates import ask_by_dm
from llm import floor_message, verify_photo

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("bailout.bot")

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = os.environ.get("GUILD_ID")  # optional, makes slash commands show up instantly
FLOOR_CHANNEL = os.environ.get("FLOOR_CHANNEL", "agent-floor")
STATE_PATH = os.environ.get("STATE_PATH", "state.json")

if not DISCORD_TOKEN:
    raise SystemExit("DISCORD_TOKEN is missing. copy .env.example to .env and fill it in.")

intents = discord.Intents.default()
intents.message_content = True  # for !advance and typed DM replies
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------------------------------------------- wiring


def floor_channel() -> Optional[discord.TextChannel]:
    return discord.utils.get(bot.get_all_channels(), name=FLOOR_CHANNEL)


async def post_event(event: dict) -> None:
    """Turn a share()-shaped event into one line and post it on #agent-floor."""
    channel = floor_channel()
    if channel is None:
        log.warning("no #%s channel, event not posted: %s", FLOOR_CHANNEL, event)
        return
    line = await asyncio.to_thread(floor_message, event)
    await channel.send(line)


async def ask(discord_id: int, question: str) -> str:
    return await ask_by_dm(bot, discord_id, question)


hub = Hub(STATE_PATH, ask=ask, post=post_event)


@bot.event
async def on_ready():
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()
    log.info("logged in as %s, floor channel = #%s", bot.user, FLOOR_CHANNEL)


def my_agent(interaction: discord.Interaction):
    return hub.agent_for_user(interaction.user.id)


# ---------------------------------------------------------------- /seed


@bot.tree.command(name="seed", description="load the three demo roommates")
@app_commands.describe(maya="who plays Maya (busy 9)", jordan="who plays Jordan (busy 3)", priya="who plays Priya (busy 5)")
async def seed(interaction: discord.Interaction, maya: discord.User, jordan: discord.User, priya: discord.User):
    agents = hub.seed({"maya": maya.id, "jordan": jordan.id, "priya": priya.id})
    names = ", ".join(f"{a.name} -> {interaction.guild.get_member(a.discord_id) or a.discord_id}" for a in agents)
    await interaction.response.send_message(f"seeded: {names}. ledger reset to zero.")


# ---------------------------------------------------------------- /me


me = app_commands.Group(name="me", description="tune your agent's private profile (only you see these replies)")


@me.command(name="busy", description="how swamped are you, 1-10")
async def me_busy(interaction: discord.Interaction, level: app_commands.Range[int, 1, 10]):
    agent = my_agent(interaction)
    if agent is None:
        return await interaction.response.send_message("you don't have an agent yet. run /seed.", ephemeral=True)
    hub.save_agent(agent.with_busy(level))
    await interaction.response.send_message(f"noted, busy = {level}. others only ever see the band.", ephemeral=True)


@me.command(name="dislike", description="how much you hate a task, 1-10")
async def me_dislike(interaction: discord.Interaction, task: str, level: app_commands.Range[int, 1, 10]):
    agent = my_agent(interaction)
    if agent is None:
        return await interaction.response.send_message("you don't have an agent yet. run /seed.", ephemeral=True)
    hub.save_agent(agent.with_dislike(task, level))
    await interaction.response.send_message(f"noted, {task.lower()} dislike = {level}. this stays private.", ephemeral=True)


@me.command(name="cap", description="max credits your agent may bid without asking you")
async def me_cap(interaction: discord.Interaction, credits: app_commands.Range[float, 0.0, 100.0]):
    agent = my_agent(interaction)
    if agent is None:
        return await interaction.response.send_message("you don't have an agent yet. run /seed.", ephemeral=True)
    hub.save_agent(agent.with_cap(credits))
    await interaction.response.send_message(f"noted, cap = {credits}. I'll DM you before bidding above that.", ephemeral=True)


bot.tree.add_command(me)


# ---------------------------------------------------------------- /bailout


@bot.tree.command(name="bailout", description="ask the house to cover some chores")
@app_commands.describe(tasks="comma separated, e.g. dishes, trash")
async def bailout(interaction: discord.Interaction, tasks: str):
    agent = my_agent(interaction)
    if agent is None:
        return await interaction.response.send_message("you don't have an agent yet. run /seed.", ephemeral=True)
    task_list = tasks.split(",")
    await interaction.response.send_message(f"ok, your agent is DMing you to confirm before it asks anyone.", ephemeral=True)

    async def run():
        summary = await hub.bailout(agent, task_list)
        await interaction.followup.send(summary, ephemeral=True)

    bot.loop.create_task(run())


# ---------------------------------------------------------------- /done


@bot.tree.command(name="done", description="mark a favor done so credits settle")
@app_commands.describe(task="the task", photo="optional photo proof")
async def done(interaction: discord.Interaction, task: str, photo: Optional[discord.Attachment] = None):
    agent = my_agent(interaction)
    if agent is None:
        return await interaction.response.send_message("you don't have an agent yet. run /seed.", ephemeral=True)
    await interaction.response.defer()
    if photo is not None:
        verdict = await check_photo(photo, task)
        if verdict == "NOT_DONE":
            return await interaction.followup.send(f"the photo doesn't look like {task} is done. not settling yet.")
        if verdict == "UNSURE":
            # the model isn't sure, so ask the person who asked for the favor instead of guessing
            favor = hub.open_favor(task)
            if favor is None:
                return await interaction.followup.send(f"no open favor for {task}.")
            requester = hub.agent(favor["requester"])
            answer = await ask(requester.discord_id, f"I couldn't tell from the photo. did **{favor['helper']}** finish **{task}**? ✅/❌")
            if answer != "YES":
                return await interaction.followup.send(f"{requester.name} didn't confirm {task}, not settling. ({answer})")
    summary = await hub.done(agent, task)
    await interaction.followup.send(summary)


async def check_photo(photo: discord.Attachment, task: str) -> str:
    """DONE / NOT_DONE / UNSURE. Anything that isn't an image counts as UNSURE."""
    media_type = photo.content_type or ""
    if not media_type.startswith("image/"):
        return "UNSURE"
    image_bytes = await photo.read()
    return await asyncio.to_thread(verify_photo, image_bytes, media_type.split(";")[0], task)


# ---------------------------------------------------------------- /ledger and /shared


@bot.tree.command(name="ledger", description="who owes who")
async def ledger(interaction: discord.Interaction):
    await interaction.response.send_message(hub.ledger_report())


@bot.tree.command(name="shared", description="exactly what each agent sent last round, and what stayed private")
async def shared(interaction: discord.Interaction):
    await interaction.response.send_message(hub.shared_report())


# ---------------------------------------------------------------- !advance (demo)


@bot.command(name="advance", help="demo: !advance jordan 9 sets busy and triggers payback")
async def advance(ctx: commands.Context, name: str, busy: int):
    await ctx.send(f"setting {name}'s busy to {busy}...")
    summary = await hub.advance(name, busy)
    await ctx.send(summary)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN, log_handler=None)
