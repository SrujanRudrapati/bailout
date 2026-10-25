"""Tests for the parts of llm.py that don't need an API key."""

import os
import unittest
from unittest import mock

from llm import _normalize_reply, _template_line, floor_message, parse_reply, verify_photo


class ParseReplyOfflineTests(unittest.TestCase):
    def test_obvious_yes(self):
        self.assertEqual(parse_reply("yes"), "YES")
        self.assertEqual(parse_reply(" ✅ "), "YES")

    def test_obvious_no(self):
        self.assertEqual(parse_reply("nope"), "NO")

    def test_short_cap(self):
        self.assertEqual(parse_reply("cap 5"), "CAP 5")

    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})
    def test_unclear_without_key_escalates(self):
        self.assertEqual(parse_reply("depends, what time is it due?"), "ESCALATE")

    def test_normalize_handles_model_output(self):
        self.assertEqual(_normalize_reply(" cap 7 "), "CAP 7")
        self.assertEqual(_normalize_reply("maybe"), "ESCALATE")


class FloorFallbackTests(unittest.TestCase):
    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})
    def test_floor_message_without_key_uses_template(self):
        line = floor_message({"type": "bid", "name": "jordan", "task": "dishes", "bid": 5.2, "busy_band": "low"})
        self.assertIn("Jordan", line)
        self.assertIn("5.2", line)

    def test_template_only_uses_given_fields(self):
        line = _template_line({"type": "request", "name": "maya", "busy_band": "high", "tasks": ["dishes"]})
        self.assertNotIn("midterm", line)


class PhotoFallbackTests(unittest.TestCase):
    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})
    def test_verify_photo_without_key_is_unsure(self):
        self.assertEqual(verify_photo(b"fake", "image/png", "dishes"), "UNSURE")


if __name__ == "__main__":
    unittest.main()
