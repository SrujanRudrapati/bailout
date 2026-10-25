"""Tests for the on_message bridge listener in bot.py, with a fake Discord message.

bot.py reads its env at import, so the env is patched before the import.
"""

import asyncio
import logging
import os
import tempfile
import unittest
from unittest import mock

TRUSTED = 555
MAYA = 42
_state_dir = tempfile.TemporaryDirectory()
os.environ.update(
    {
        "DISCORD_TOKEN": "test-token",
        "BRIDGE_WEBHOOK_ID": str(TRUSTED),
        "STATE_PATH": os.path.join(_state_dir.name, "state.json"),
        "ANTHROPIC_API_KEY": "",
    }
)
logging.getLogger("discord").setLevel(logging.ERROR)

import bot  # noqa: E402  (needs the env above)

VALID = f"bailout_request user={MAYA} busy=9 tasks=dishes,trash source=cubbie"


def fake_message(webhook_id, content, channel="agent-floor", can_manage=True):
    message = mock.Mock()
    message.id = 1001
    message.webhook_id = webhook_id
    message.content = content
    message.channel.name = channel
    message.channel.permissions_for.return_value = mock.Mock(manage_messages=can_manage)
    message.guild.me = object()
    message.delete = mock.AsyncMock()
    return message


class BridgeListenerTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        _state_dir.cleanup()

    def setUp(self):
        bot.hub.seed({"maya": MAYA, "jordan": 43, "priya": 44})
        bot._bridge_in_flight.clear()
        self.bailout = mock.AsyncMock(return_value="dishes: jordan for 9.0 credits")
        self.dm = mock.AsyncMock()
        self.patches = [mock.patch.object(bot.hub, "cubbie_bailout", self.bailout), mock.patch.object(bot, "dm_user", self.dm)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def handle(self, message):
        async def go():
            await bot.on_bridge_message(message)
            await asyncio.gather(*bot._background)

        asyncio.run(go())

    def test_valid_line_from_trusted_webhook_runs_the_bailout_and_deletes_the_line(self):
        message = fake_message(TRUSTED, VALID)
        self.handle(message)
        message.delete.assert_awaited_once()
        agent, busy, tasks = self.bailout.await_args.args
        self.assertEqual((agent.name, busy, tasks), ("maya", 9, ["dishes", "trash"]))
        self.dm.assert_awaited_once()
        self.assertEqual(self.dm.await_args.args[0], MAYA)
        self.assertEqual(bot._bridge_in_flight, set())

    def test_other_webhook_is_ignored_and_not_deleted(self):
        message = fake_message(TRUSTED + 1, VALID)
        self.handle(message)
        message.delete.assert_not_awaited()
        self.bailout.assert_not_awaited()

    def test_human_message_is_ignored(self):
        message = fake_message(None, VALID)
        self.handle(message)
        message.delete.assert_not_awaited()
        self.bailout.assert_not_awaited()

    def test_bad_line_from_trusted_webhook_is_deleted_logged_and_not_run(self):
        message = fake_message(TRUSTED, f"bailout_request user={MAYA} busy=42 tasks=dishes source=cubbie")
        with self.assertLogs("bailout.bot", level="INFO") as captured:
            self.handle(message)
        message.delete.assert_awaited_once()
        self.bailout.assert_not_awaited()
        self.assertTrue(any("ignoring bridge message" in line for line in captured.output))

    def test_unknown_user_is_not_run(self):
        message = fake_message(TRUSTED, "bailout_request user=99 busy=9 tasks=dishes source=cubbie")
        self.handle(message)
        self.bailout.assert_not_awaited()

    def test_wrong_channel_is_not_run(self):
        message = fake_message(TRUSTED, VALID, channel="general")
        self.handle(message)
        message.delete.assert_awaited_once()
        self.bailout.assert_not_awaited()

    def test_without_manage_messages_the_line_stays_but_the_bailout_still_runs(self):
        message = fake_message(TRUSTED, VALID, can_manage=False)
        with self.assertLogs("bailout.bot", level="WARNING") as captured:
            self.handle(message)
        message.delete.assert_not_awaited()
        self.bailout.assert_awaited_once()
        self.assertTrue(any("Manage Messages" in line for line in captured.output))

    def test_duplicate_while_running_is_ignored(self):
        bot._bridge_in_flight.add(MAYA)
        message = fake_message(TRUSTED, VALID)
        self.handle(message)
        self.bailout.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
