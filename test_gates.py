"""Tests for the offline parts of gates.py: typed-reply keyword matching and emoji normalization."""

import unittest

from gates import emoji_answer, parse_confirmation


class ParseConfirmationTests(unittest.TestCase):
    def test_yes_keywords(self):
        for text in ("yes", "y", "ya", "yep", "ok", "sure", "YES", " Yes! ", "okay", "✅"):
            self.assertEqual(parse_confirmation(text), "YES", text)

    def test_no_keywords(self):
        for text in ("no", "n", "nope", "NO", " No. ", "❌"):
            self.assertEqual(parse_confirmation(text), "NO", text)

    def test_cap_reply_keeps_the_number(self):
        self.assertEqual(parse_confirmation("cap 7"), "CAP 7")
        self.assertEqual(parse_confirmation("Cap 2.5 please"), "CAP 2.5")

    def test_anything_else_is_unrecognized(self):
        for text in ("", "   ", "maybe", "yes but only if jordan does trash", "nah not really sure"):
            self.assertIsNone(parse_confirmation(text), text)


class EmojiAnswerTests(unittest.TestCase):
    def test_check_and_cross(self):
        self.assertEqual(emoji_answer("✅"), "YES")
        self.assertEqual(emoji_answer("❌"), "NO")

    def test_variation_selector_is_ignored(self):
        self.assertEqual(emoji_answer("✅️"), "YES")
        self.assertEqual(emoji_answer("❌️"), "NO")

    def test_other_emoji_is_not_an_answer(self):
        self.assertIsNone(emoji_answer("👍"))
        self.assertIsNone(emoji_answer(None))


if __name__ == "__main__":
    unittest.main()
