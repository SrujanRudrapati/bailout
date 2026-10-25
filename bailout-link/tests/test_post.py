"""Tests for scripts/post.py: one strict line to the Bailout webhook."""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import post  # noqa: E402

HOOK = "https://discord.com/api/webhooks/123456789012345678/abcDEF-ghi_jkl"


class BuildLineTest(unittest.TestCase):
    def test_exact_shape(self):
        line = post.build_line("987654321098765432", 7, "dishes,trash")
        self.assertEqual(line, "bailout_request user=987654321098765432 busy=7 tasks=dishes,trash source=cubbie")

    def test_tasks_are_lowercased_trimmed_and_deduplicated(self):
        line = post.build_line("42", 3, " Dishes , TRASH,dishes ")
        self.assertIn("tasks=dishes,trash ", line)

    def test_spaces_inside_a_task_become_dashes(self):
        line = post.build_line("42", 3, "take out trash, do dishes")
        self.assertIn("tasks=take-out-trash,do-dishes ", line)

    def test_rejects_user_id_that_is_not_digits(self):
        for bad in ["", "abc", "12a", "0123", "<@123>", "1" * 21]:
            with self.subTest(bad=bad), self.assertRaises(post.ArgumentError):
                post.build_line(bad, 5, "dishes")

    def test_rejects_busy_outside_1_to_10(self):
        for bad in [0, 11, -3]:
            with self.subTest(bad=bad), self.assertRaises(post.ArgumentError):
                post.build_line("42", bad, "dishes")

    def test_rejects_tasks_with_digits_mentions_or_nothing(self):
        for bad in ["", ",", "dishes,", "room2", "@everyone", "a" * 31, "a,b,c,d,e,f"]:
            with self.subTest(bad=bad), self.assertRaises(post.ArgumentError):
                post.build_line("42", 5, bad)


class WebhookUrlTest(unittest.TestCase):
    def test_accepts_discord_webhook_url(self):
        self.assertEqual(post.check_webhook_url(HOOK), HOOK)

    def test_rejects_other_hosts_and_plain_http(self):
        for bad in ["", "http://discord.com/api/webhooks/1/x", "https://example.com/hook",
                    "https://discord.com.evil.io/api/webhooks/1/x", "https://discord.com/channels/1"]:
            with self.subTest(bad=bad), self.assertRaises(post.ArgumentError):
                post.check_webhook_url(bad)


class SendTest(unittest.TestCase):
    def test_posts_json_content_to_the_webhook(self):
        seen = {}

        class Response:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self): return b""

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            seen["body"] = json.loads(request.data.decode())
            seen["type"] = request.get_header("Content-type")
            seen["timeout"] = timeout
            return Response()

        with mock.patch.object(post.request, "urlopen", fake_urlopen):
            post.send(HOOK, "bailout_request user=1 busy=2 tasks=a source=cubbie")
        self.assertEqual(seen["url"], HOOK)
        self.assertEqual(seen["body"], {"content": "bailout_request user=1 busy=2 tasks=a source=cubbie"})
        self.assertEqual(seen["type"], "application/json")
        self.assertGreater(seen["timeout"], 0)

    def test_http_error_becomes_post_error_without_the_url(self):
        def boom(request, timeout):
            raise post.error.HTTPError(HOOK, 401, "Unauthorized", {}, io.BytesIO(b'{"message":"Invalid Webhook Token"}'))

        with mock.patch.object(post.request, "urlopen", boom):
            with self.assertRaises(post.PostError) as caught:
                post.send(HOOK, "x")
        self.assertIn("401", str(caught.exception))
        self.assertNotIn(HOOK, str(caught.exception))

    def test_network_error_becomes_post_error(self):
        def boom(request, timeout):
            raise post.error.URLError("nodename nor servname provided")

        with mock.patch.object(post.request, "urlopen", boom):
            with self.assertRaises(post.PostError):
                post.send(HOOK, "x")


class CommandLineTest(unittest.TestCase):
    def run_main(self, argv, env):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
            code = post.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_missing_webhook_exits_3(self):
        code, _, err = self.run_main(["--user", "42", "--busy", "5", "--tasks", "dishes"], {})
        self.assertEqual(code, 3)
        self.assertIn("BAILOUT_WEBHOOK_URL", err)

    def test_bad_arguments_exit_2(self):
        code, _, err = self.run_main(["--user", "me", "--busy", "5", "--tasks", "dishes"], {"BAILOUT_WEBHOOK_URL": HOOK})
        self.assertEqual(code, 2)
        self.assertIn("user", err)

    def test_dry_run_prints_the_line_and_does_not_post(self):
        with mock.patch.object(post, "send", side_effect=AssertionError("should not post")):
            code, out, _ = self.run_main(["--user", "42", "--busy", "5", "--tasks", "dishes", "--dry-run"],
                                         {"BAILOUT_WEBHOOK_URL": HOOK})
        self.assertEqual(code, 0)
        self.assertIn("bailout_request user=42 busy=5 tasks=dishes source=cubbie", out)

    def test_dry_run_works_without_a_webhook(self):
        code, out, _ = self.run_main(["--user", "42", "--busy", "5", "--tasks", "dishes", "--dry-run"], {})
        self.assertEqual(code, 0)
        self.assertIn("busy=5", out)

    def test_success_prints_sent_and_exits_0(self):
        with mock.patch.object(post, "send", return_value=None):
            code, out, _ = self.run_main(["--user", "42", "--busy", "9", "--tasks", "dishes,trash"],
                                         {"BAILOUT_WEBHOOK_URL": HOOK})
        self.assertEqual(code, 0)
        self.assertIn("sent", out.lower())
        self.assertNotIn(HOOK, out)

    def test_post_failure_exits_4(self):
        with mock.patch.object(post, "send", side_effect=post.PostError("Discord said HTTP 404")):
            code, _, err = self.run_main(["--user", "42", "--busy", "9", "--tasks", "dishes"],
                                         {"BAILOUT_WEBHOOK_URL": HOOK})
        self.assertEqual(code, 4)
        self.assertIn("404", err)


if __name__ == "__main__":
    unittest.main()
