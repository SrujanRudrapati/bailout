"""Tests for the ntfy relay side of bot.py: events from relay.py start the same Cubbie bailout as a webhook line.

bot.py reads its env at import, so the env is patched before the import (same as test_bot_bridge.py).
"""

import asyncio
import logging
import os
import tempfile
import time
import unittest
from unittest import mock

MAYA = 42
_state_dir = tempfile.TemporaryDirectory()
os.environ.update(
    {
        "DISCORD_TOKEN": "test-token",
        "BRIDGE_WEBHOOK_ID": "555",
        "STATE_PATH": os.path.join(_state_dir.name, "state.json"),
        "ANTHROPIC_API_KEY": "",
    }
)
logging.getLogger("discord").setLevel(logging.ERROR)

import bot  # noqa: E402  (needs the env above)
import relay  # noqa: E402
from flows import Hub  # noqa: E402

TOPIC = "bailout-house-k3j9x"
VALID = f"bailout_request user={MAYA} busy=9 tasks=dishes,trash source=cubbie"


def event(message=VALID, id="m1", age=0):
    return {"id": id, "time": time.time() - age, "event": "message", "topic": TOPIC, "message": message}


def fresh_hub() -> Hub:
    """A Hub on this module's own state file: the bot module's import-time one may belong to another test module."""
    hub = Hub(os.path.join(_state_dir.name, "relay-state.json"), ask=bot.ask, post=bot.post_event)
    hub.seed({"maya": MAYA, "jordan": 43, "priya": 44})
    return hub


class RelayHandlerTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        _state_dir.cleanup()

    def setUp(self):
        bot._bridge_in_flight.clear()
        self.inbox = relay.RelayInbox()
        self.bailout = mock.AsyncMock(return_value="dishes: jordan for 9.0 credits")
        self.dm = mock.AsyncMock()
        hub = fresh_hub()
        self.patches = [
            mock.patch.object(bot, "hub", hub),
            mock.patch.object(hub, "cubbie_bailout", self.bailout),
            mock.patch.object(bot, "dm_user", self.dm),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def handle(self, *events):
        async def go():
            for one in events:
                bot.handle_relay_event(self.inbox, one)
            await asyncio.gather(*bot._background)

        asyncio.run(go())

    def test_valid_event_runs_the_bailout_and_dms_the_requester(self):
        with self.assertLogs("bailout.bot", level="INFO") as captured:
            self.handle(event())
        agent, busy, tasks = self.bailout.await_args.args
        self.assertEqual((agent.name, busy, tasks), ("maya", 9, ["dishes", "trash"]))
        self.dm.assert_awaited_once()
        self.assertEqual(self.dm.await_args.args[0], MAYA)
        self.assertEqual(bot._bridge_in_flight, set())
        joined = "\n".join(captured.output)
        self.assertIn("relay: received", joined)
        self.assertIn("relay: parsed", joined)
        self.assertNotIn(TOPIC, joined)

    def test_duplicate_id_runs_once(self):
        with self.assertLogs("bailout.bot", level="INFO") as captured:
            self.handle(event(id="m1"), event(id="m1"))
        self.bailout.assert_awaited_once()
        self.assertTrue(any("relay: rejected" in line and "duplicate" in line for line in captured.output))

    def test_stale_event_is_rejected(self):
        with self.assertLogs("bailout.bot", level="INFO") as captured:
            self.handle(event(age=relay.RELAY_STALE_SECONDS + 5))
        self.bailout.assert_not_awaited()
        self.assertTrue(any("relay: rejected" in line and "stale" in line for line in captured.output))

    def test_unknown_user_is_rejected(self):
        with self.assertLogs("bailout.bot", level="INFO") as captured:
            self.handle(event(message=VALID.replace(str(MAYA), "99")))
        self.bailout.assert_not_awaited()
        self.assertTrue(any("relay: rejected" in line and "unknown user" in line for line in captured.output))

    def test_bad_line_is_rejected_and_logged_with_the_reason(self):
        with self.assertLogs("bailout.bot", level="INFO") as captured:
            self.handle(event(message=VALID.replace("busy=9", "busy=42")))
        self.bailout.assert_not_awaited()
        self.assertTrue(any("relay: rejected" in line and "busy" in line for line in captured.output))

    def test_user_with_a_bailout_already_running_is_rejected(self):
        bot._bridge_in_flight.add(MAYA)
        self.handle(event())
        self.bailout.assert_not_awaited()


class PollOnceTests(unittest.TestCase):
    def setUp(self):
        bot._bridge_in_flight.clear()
        self.inbox = relay.RelayInbox()

    def test_poll_failure_is_logged_without_the_topic_and_does_not_raise(self):
        async def failing(session, topic, last_id):
            raise relay.RelayError("ntfy answered HTTP 429")

        with mock.patch.object(relay, "fetch_relay_events", failing):
            with self.assertLogs("bailout.bot", level="WARNING") as captured:
                asyncio.run(bot.poll_relay_once(object(), TOPIC, self.inbox))
        joined = "\n".join(captured.output)
        self.assertIn("429", joined)
        self.assertNotIn(TOPIC, joined)

    def test_poll_hands_each_event_to_the_handler(self):
        seen = []

        async def two_events(session, topic, last_id):
            return [event(id="a"), event(id="b")]

        with mock.patch.object(relay, "fetch_relay_events", two_events), mock.patch.object(bot, "handle_relay_event", lambda inbox, e: seen.append(e["id"])):
            asyncio.run(bot.poll_relay_once(object(), TOPIC, self.inbox))
        self.assertEqual(seen, ["a", "b"])

    def test_poll_continues_from_the_inbox_last_id(self):
        asked = []

        async def record(session, topic, last_id):
            asked.append(last_id)
            return []

        self.inbox.last_id = "m9"
        with mock.patch.object(relay, "fetch_relay_events", record):
            asyncio.run(bot.poll_relay_once(object(), TOPIC, self.inbox))
        self.assertEqual(asked, ["m9"])


if __name__ == "__main__":
    unittest.main()
