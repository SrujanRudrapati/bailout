"""Bailout - the Discord bot.

Slash commands: /join, /me busy|dislike|cap, /bailout, /done, /ledger, /shared, /help, /seed (demo)
Prefix command:  !advance <name> <busy>   (demo: trigger the payback rule)
Cubbie bridge:   a webhook line in #agent-floor (see bridge.py) starts a bailout with gate 1 skipped
ntfy relay:      the same line published to an ntfy.sh topic (see relay.py), for when Discord blocks the webhook

All the logic lives in flows.py. This file just maps Discord to it.
"""

import asyncio
import logging
import os
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import relay
from bridge import BridgeError, BridgeRequest, accept_bridge_message, is_bridge_webhook
from flows import Hub
from gates import ask_by_dm
from llm import floor_message, verify_photo
from seed_ids import SEED_ENV_VARS, resolve_seed_ids

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("bailout.bot")

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = os.environ.get("GUILD_ID")  # optional, makes slash commands show up instantly
FLOOR_CHANNEL = os.environ.get("FLOOR_CHANNEL", "agent-floor")
STATE_PATH = os.environ.get("STATE_PATH", "state.json")
BRIDGE_WEBHOOK_ID = os.environ.get("BRIDGE_WEBHOOK_ID", "").strip() or None  # the only webhook the bridge trusts
RELAY_TOPIC = os.environ.get("RELAY_TOPIC", "").strip() or None  # ntfy.sh topic the bot polls; it is a secret, never logged

if not DISCORD_TOKEN:
    raise SystemExit("DISCORD_TOKEN is missing. copy .env.example to .env and fill it in.")
if BRIDGE_WEBHOOK_ID is not None and not BRIDGE_WEBHOOK_ID.isdigit():
    raise SystemExit("BRIDGE_WEBHOOK_ID must be the numeric id from the webhook URL (or left empty to turn the bridge off).")
if RELAY_TOPIC is not None and not relay.is_topic_name(RELAY_TOPIC):
    # the value is not echoed: it is the relay secret
    raise SystemExit("RELAY_TOPIC must be a bare ntfy topic name (letters, digits, - or _, up to 64), not a URL (or left empty to turn the relay off).")

intents = discord.Intents.default()
intents.message_content = True  # for !advance and typed DM replies
intents.dm_messages = True  # typed yes/no replies in DMs
intents.reactions = True  # ✅/❌ on confirmation DMs
intents.dm_reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)
_background: set[asyncio.Task] = set()  # keep references so tasks aren't garbage collected mid-await
_bridge_in_flight: set[int] = set()  # discord ids with a Cubbie bailout running, so a webhook retry can't start a second round
NO_MENTIONS = discord.AllowedMentions.none()  # nothing the bot posts should ever ping anyone
_synced = False


# ---------------------------------------------------------------- wiring


def floor_channel() -> Optional[discord.TextChannel]:
    return discord.utils.get(bot.get_all_channels(), name=FLOOR_CHANNEL)


async def post_event(event: dict) -> None:
    """Turn a share()-shaped event into one line and post it on #agent-floor."""
    channel = floor_channel()
    if channel is None:
        log.warning("no #%s channel, event not posted: %s", FLOOR_CHANNEL, event)
        return
    names = [agent.name for agent in hub.agents()]  # so a line about an anonymous event can be checked for naming someone
    line = await asyncio.to_thread(floor_message, event, names)
    await channel.send(line, allowed_mentions=NO_MENTIONS)


async def ask(discord_id: int, question: str) -> str:
    return await ask_by_dm(bot, discord_id, question)


hub = Hub(STATE_PATH, ask=ask, post=post_event)


@bot.event
async def on_ready():
    global _synced
    if _synced:
        return  # on_ready fires again on reconnects, don't re-sync every time
    _synced = True
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()
    log.info("logged in as %s, floor channel = #%s", bot.user, FLOOR_CHANNEL)
    if RELAY_TOPIC:
        run_in_background(relay_loop(RELAY_TOPIC))
        log.info("relay: polling ntfy every %ss", relay.RELAY_POLL_SECONDS)


def my_agent(interaction: discord.Interaction):
    return hub.agent_for_user(interaction.user.id)


# ---------------------------------------------------------------- /join and /help

JOIN_WELCOME = (
    "you're in as **{name}**. your agent starts with busy 5, every chore at dislike 5, a cap of 20 credits and a balance of 0.\n"
    "set `/me busy`, `/me dislike <task>` and `/me cap` so it bids the way you would. "
    "those numbers stay with your agent: the floor only ever sees your busy band and your bids."
)

HELP_TEXT = "\n".join(
    [
        "**/join** - get your own chore agent (busy 5, every chore at dislike 5, cap 20, balance 0). the reply is private.",
        "**/me busy** `<1-10>` - how swamped you are. only the band (low / medium / high) ever leaves your agent.",
        "**/me dislike** `<task> <1-10>` - how much you hate a chore. stays private, sets what your agent bids.",
        "**/me cap** `<credits>` - the most your agent may bid without DMing you first.",
        "**/bailout** `<tasks>` - ask the house to cover chores, e.g. `/bailout dishes, trash`. your agent DMs you before asking.",
        "**/done** `<task> [photo]` - mark a favor finished so the credits settle.",
        "**/ledger** - who's owed what, and the favors still open.",
        "**/shared** - exactly what each agent sent to the floor last round, and what stayed private.",
        "**/seed** `[maya jordan priya]` - demo only: load the three scripted roommates. anyone who /join-ed stays.",
        "**/help** - this list.",
        "**!advance** `<name> <busy>` - demo only: set someone's busy and trigger the payback rule.",
    ]
)


@bot.tree.command(name="join", description="get your own chore agent (busy 5, every chore at 5, cap 20)")
async def join(interaction: discord.Interaction):
    agent, created = hub.join(interaction.user.id, interaction.user.display_name)
    if not created:
        return await interaction.response.send_message(
            f"you're already in as **{agent.name}**. `/me busy`, `/me dislike` and `/me cap` tune your agent.", ephemeral=True
        )
    log.info("join: user %s is now agent %s", interaction.user.id, agent.name)
    await interaction.response.send_message(JOIN_WELCOME.format(name=agent.name), ephemeral=True)


@bot.tree.command(name="help", description="every Bailout command, one line each")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(HELP_TEXT, ephemeral=True)


# ---------------------------------------------------------------- /seed


@bot.tree.command(name="seed", description="demo: load the three scripted roommates (anyone who joined stays)")
@app_commands.describe(
    maya="who plays Maya (busy 9); defaults to SEED_MAYA_ID",
    jordan="who plays Jordan (busy 3); defaults to SEED_JORDAN_ID",
    priya="who plays Priya (busy 5); defaults to SEED_PRIYA_ID",
)
async def seed(
    interaction: discord.Interaction,
    maya: Optional[discord.User] = None,
    jordan: Optional[discord.User] = None,
    priya: Optional[discord.User] = None,
):
    mentions = {"maya": maya, "jordan": jordan, "priya": priya}
    ids, missing = resolve_seed_ids({name: user.id if user else None for name, user in mentions.items()}, os.environ)
    if missing:
        hint = ", ".join(f"{name} (or set {SEED_ENV_VARS[name]})" for name in missing)
        return await interaction.response.send_message(f"/seed needs: {hint}.", ephemeral=True)
    await interaction.response.defer()
    labels = {name: await seed_label(mentions[name], discord_id) for name, discord_id in ids.items()}
    agents = hub.seed(ids)
    names = ", ".join(f"{a.name} -> {labels[a.name]}" for a in agents)
    await interaction.followup.send(f"seeded: {names}. everyone's ledger reset to zero.")


async def seed_label(mentioned: Optional[discord.User], discord_id: int) -> str:
    """Display name for a seeded player, looked up by id when it came from env."""
    if mentioned is not None:
        return mentioned.display_name
    try:
        return (await bot.fetch_user(discord_id)).display_name
    except discord.DiscordException as exc:
        log.warning("could not fetch user %s for /seed label: %s", discord_id, exc)
        return str(discord_id)


# ---------------------------------------------------------------- /me


me = app_commands.Group(name="me", description="tune your agent's private profile (only you see these replies)")


@me.command(name="busy", description="how swamped are you, 1-10")
async def me_busy(interaction: discord.Interaction, level: app_commands.Range[int, 1, 10]):
    agent = my_agent(interaction)
    if agent is None:
        return await interaction.response.send_message("you don't have an agent yet. run /join.", ephemeral=True)
    hub.save_agent(agent.with_busy(level))
    await interaction.response.send_message(f"noted, busy = {level}. others only ever see the band.", ephemeral=True)


@me.command(name="dislike", description="how much you hate a task, 1-10")
async def me_dislike(interaction: discord.Interaction, task: str, level: app_commands.Range[int, 1, 10]):
    agent = my_agent(interaction)
    if agent is None:
        return await interaction.response.send_message("you don't have an agent yet. run /join.", ephemeral=True)
    hub.save_agent(agent.with_dislike(task, level))
    await interaction.response.send_message(f"noted, {task.lower()} dislike = {level}. this stays private.", ephemeral=True)


@me.command(name="cap", description="max credits your agent may bid without asking you")
async def me_cap(interaction: discord.Interaction, credits: app_commands.Range[float, 0.0, 100.0]):
    agent = my_agent(interaction)
    if agent is None:
        return await interaction.response.send_message("you don't have an agent yet. run /join.", ephemeral=True)
    hub.save_agent(agent.with_cap(credits))
    await interaction.response.send_message(f"noted, cap = {credits}. I'll DM you before bidding above that.", ephemeral=True)


bot.tree.add_command(me)


# ---------------------------------------------------------------- /bailout


@bot.tree.command(name="bailout", description="ask the house to cover some chores")
@app_commands.describe(tasks="comma separated, e.g. dishes, trash")
async def bailout(interaction: discord.Interaction, tasks: str):
    agent = my_agent(interaction)
    if agent is None:
        return await interaction.response.send_message("you don't have an agent yet. run /join.", ephemeral=True)
    task_list = tasks.split(",")
    await interaction.response.send_message(f"ok, your agent is DMing you to confirm before it asks anyone.", ephemeral=True)

    async def run():
        try:
            summary = await hub.bailout(agent, task_list)
        except discord.Forbidden:
            summary = "I couldn't DM someone (DMs closed?) or can't post in the floor channel. nothing was changed."
            log.exception("bailout failed")
        except Exception:
            summary = "something went wrong mid-bailout, check the bot logs."
            log.exception("bailout failed")
        await report_back(interaction, agent.discord_id, summary)

    run_in_background(run())


def run_in_background(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def report_back(interaction: discord.Interaction, discord_id: int, text: str) -> None:
    """Ephemeral followup if the interaction is still alive (15 min), else DM, else log."""
    try:
        await interaction.followup.send(text, ephemeral=True)
        return
    except discord.HTTPException:
        pass
    await dm_user(discord_id, text)


# ---------------------------------------------------------------- cubbie bridge


@bot.listen("on_message")
async def on_bridge_message(message: discord.Message):
    """A Cubbie Skill posts one line through the bridge webhook. Only that webhook is trusted.

    The raw line (with the exact busy number) is never echoed: the floor only ever
    gets the normal request line with the busy band, and the webhook message is
    deleted if the bot may manage messages.
    """
    trusted = int(BRIDGE_WEBHOOK_ID) if BRIDGE_WEBHOOK_ID else None
    if not is_bridge_webhook(message.webhook_id, trusted):
        if message.webhook_id is not None:
            log.debug("webhook message %s from webhook %s is not the bridge, ignoring", message.id, message.webhook_id)
        return
    # from here on the message is the bridge's, so it always gets cleaned up
    await delete_bridge_message(message)
    try:
        if getattr(message.channel, "name", None) != FLOOR_CHANNEL:
            raise BridgeError(f"posted in #{message.channel}, not #{FLOOR_CHANNEL}")
        request = accept_bridge_message(message.webhook_id, message.content, trusted, hub.agent_for_user)
        if request.user_id in _bridge_in_flight:
            raise BridgeError(f"a cubbie bailout for user {request.user_id} is already running")
    except BridgeError as why:
        # the rejected line goes to the bot log only (never the floor), truncated so a bad actor can't flood it
        log.info("ignoring bridge message %s: %s (content %r)", message.id, why, message.content[:120])
        return
    start_cubbie_bailout(request, f"message {message.id}")


def start_cubbie_bailout(request: BridgeRequest, origin: str) -> None:
    """Run the gate-1-skipped bailout for a bridge request (webhook or relay) in the background."""
    agent = hub.agent_for_user(request.user_id)
    log.info("cubbie bridge: bailout for %s on %s (%s)", agent.name, ", ".join(request.tasks), origin)
    _bridge_in_flight.add(request.user_id)

    async def run():
        try:
            summary = await hub.cubbie_bailout(agent, request.busy, list(request.tasks))
        except discord.Forbidden:
            summary = "I couldn't DM someone (DMs closed?) or can't post in the floor channel. nothing was changed."
            log.exception("cubbie bailout failed")
        except Exception:
            summary = "something went wrong mid-bailout, check the bot logs."
            log.exception("cubbie bailout failed")
        finally:
            _bridge_in_flight.discard(request.user_id)
        await dm_user(agent.discord_id, f"your Cubbie asked the house for cover.\n{summary}")

    run_in_background(run())


async def delete_bridge_message(message: discord.Message) -> None:
    """Remove the raw webhook line so the exact busy number never sits on the floor."""
    me = message.guild.me if message.guild else None
    if me is None or not message.channel.permissions_for(me).manage_messages:
        log.warning("no Manage Messages permission in #%s, raw bridge message %s stays visible", message.channel, message.id)
        return
    try:
        await message.delete()
    except discord.HTTPException as exc:
        log.warning("could not delete bridge message %s: %s", message.id, exc)


# ---------------------------------------------------------------- ntfy relay


async def relay_loop(topic: str) -> None:
    """Poll the ntfy topic until the bot closes. One failed poll is logged and retried next tick."""
    inbox = relay.RelayInbox()
    async with aiohttp.ClientSession() as session:
        while not bot.is_closed():
            await poll_relay_once(session, topic, inbox)
            await asyncio.sleep(relay.RELAY_POLL_SECONDS)


async def poll_relay_once(session: aiohttp.ClientSession, topic: str, inbox: relay.RelayInbox) -> None:
    """One poll. Nothing raised here may escape: a dead loop would stop the relay, and a stray traceback could name the topic."""
    try:
        events = await relay.fetch_relay_events(session, topic, inbox.last_id)
        if not inbox.primed:
            inbox.prime(events)  # whatever was already there belongs to the previous run of the bot
            log.info("relay: primed, skipped %d message(s) already in the topic", len(events))
            return
        for event in events:
            handle_relay_event(inbox, event)
    except relay.RelayError as why:
        log.warning("relay: poll failed: %s", relay.scrub(str(why), topic))
    except Exception as exc:  # noqa: BLE001  (class name only, the message could carry the URL)
        log.warning("relay: poll failed: unexpected %s", type(exc).__name__)


def handle_relay_event(inbox: relay.RelayInbox, event: dict) -> None:
    """One ntfy event: same checks as a webhook line, plus duplicate and stale filtering. Never logs the topic."""
    message_id = event.get("id")
    log.info("relay: received message %s", message_id)
    try:
        request = inbox.accept(event, hub.agent_for_user)
        if request.user_id in _bridge_in_flight:
            raise BridgeError(f"a cubbie bailout for user {request.user_id} is already running")
    except BridgeError as why:
        content = str(event.get("message", ""))[:120]
        log.info("relay: rejected message %s: %s (content %r)", message_id, why, content)
        return
    log.info("relay: parsed message %s for user %s", message_id, request.user_id)
    start_cubbie_bailout(request, f"relay message {message_id}")


async def dm_user(discord_id: int, text: str) -> None:
    try:
        user = await bot.fetch_user(discord_id)
        await user.send(text, allowed_mentions=NO_MENTIONS)
    except discord.HTTPException:
        log.error("could not deliver result to %s: %s", discord_id, text)


# ---------------------------------------------------------------- /done


@bot.tree.command(name="done", description="mark a favor done so credits settle")
@app_commands.describe(task="the task", photo="optional photo proof")
async def done(interaction: discord.Interaction, task: str, photo: Optional[discord.Attachment] = None):
    agent = my_agent(interaction)
    if agent is None:
        return await interaction.response.send_message("you don't have an agent yet. run /join.", ephemeral=True)
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
    agent = hub.agent(name)
    if agent is None:
        return await ctx.send(f"no agent called {name}. /join first, or /seed for the demo.")
    # the result can say who passed on paying back, so it goes to the creditor's DM, not the channel
    await ctx.send(f"setting {name}'s busy to {busy}... the result goes to their DMs.")
    summary = await hub.advance(name, busy)
    await dm_user(agent.discord_id, summary)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN, log_handler=None)
