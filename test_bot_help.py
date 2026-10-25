"""Tests for /help and /join in bot.py: every command is listed, and joining is private.

bot.py reads its env at import, so the env is patched before the import (same as test_bot_bridge.py).
"""

import logging
import os
import tempfile
import unittest

_state_dir = tempfile.TemporaryDirectory()
os.environ.update({"DISCORD_TOKEN": "test-token", "STATE_PATH": os.path.join(_state_dir.name, "state.json"), "ANTHROPIC_API_KEY": ""})
logging.getLogger("discord").setLevel(logging.ERROR)

import bot  # noqa: E402  (needs the env above)


def slash_commands() -> list[str]:
    names = []
    for command in bot.bot.tree.get_commands():
        subcommands = getattr(command, "commands", None)
        if subcommands:
            names.extend(f"/{command.name} {sub.name}" for sub in subcommands)
        else:
            names.append(f"/{command.name}")
    return names


class HelpTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        _state_dir.cleanup()

    def test_every_command_has_one_help_line(self):
        lines = bot.HELP_TEXT.splitlines()
        for name in [*slash_commands(), "!advance"]:
            matching = [line for line in lines if line.startswith(f"**{name}")]
            self.assertEqual(len(matching), 1, name)

    def test_help_has_no_extra_lines(self):
        self.assertEqual(len(bot.HELP_TEXT.splitlines()), len(slash_commands()) + 1)

    def test_join_and_help_are_registered(self):
        self.assertIn("/join", slash_commands())
        self.assertIn("/help", slash_commands())

    def test_welcome_tells_the_new_player_what_to_set(self):
        text = bot.JOIN_WELCOME.format(name="sam")
        for needed in ("sam", "/me busy", "/me dislike", "/me cap", "busy 5", "cap of 20"):
            self.assertIn(needed, text)


if __name__ == "__main__":
    unittest.main()
