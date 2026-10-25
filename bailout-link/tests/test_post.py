"""Tests for scripts/post.py: one strict line published to the house's ntfy.sh relay topic."""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import post  # noqa: E402

TOPIC = "bailout-house-k3j9xQ_7"
URL = f"https://ntfy.sh/{TOPIC}"
LINE = "bailout_request user=1 busy=2 tasks=a source=cubbie"


class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def read(self): return b'{"id":"abc","event":"message"}'


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


class RelayTopicTest(unittest.TestCase):
    def test_accepts_a_plain_topic_name(self):
        self.assertEqual(post.check_topic(TOPIC), TOPIC)

    def test_trims_surrounding_whitespace(self):
        self.assertEqual(post.check_topic(f"  {TOPIC}\n"), TOPIC)

    def test_rejects_empty_urls_slashes_and_odd_characters(self):
        for bad in ["", URL, "ntfy.sh/topic", "a/b", "a b", "a?b", "é", "x" * 65]:
            with self.subTest(bad=bad), self.assertRaises(post.ArgumentError):
                post.check_topic(bad)

    def test_url_is_the_topic_under_ntfy_sh(self):
        self.assertEqual(post.relay_url(TOPIC), URL)


class SendTest(unittest.TestCase):
    def test_posts_the_line_as_plain_text_to_the_topic(self):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            seen["body"] = request.data
            seen["type"] = request.get_header("Content-type")
            seen["timeout"] = timeout
            return Response()

        with mock.patch.object(post.request, "urlopen", fake_urlopen):
            post.send(TOPIC, LINE)
        self.assertEqual(seen["url"], URL)
        self.assertEqual(seen["body"], LINE.encode())
        self.assertTrue(seen["type"].startswith("text/plain"))
        self.assertGreater(seen["timeout"], 0)

    def test_sends_a_bailout_link_user_agent(self):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["agent"] = request.get_header("User-agent")
            return Response()

        with mock.patch.object(post.request, "urlopen", fake_urlopen):
            post.send(TOPIC, "x")
        self.assertEqual(seen["agent"], "BailoutLink (https://github.com/SrujanRudrapati/bailout, 1.0)")

    def test_http_error_includes_status_and_start_of_body(self):
        body = b'{"code":42901,"error":"limit reached"}' + b"x" * 500

        def boom(request, timeout):
            raise post.error.HTTPError(URL, 429, "Too Many Requests", {}, io.BytesIO(body))

        with mock.patch.object(post.request, "urlopen", boom):
            with self.assertRaises(post.PostError) as caught:
                post.send(TOPIC, "x")
        message = str(caught.exception)
        self.assertIn("429", message)
        self.assertIn("limit reached", message)
        self.assertIn("x" * (200 - len(b'{"code":42901,"error":"limit reached"}')), message)
        self.assertNotIn("x" * 201, message)

    def test_http_error_body_never_echoes_the_topic(self):
        def boom(request, timeout):
            raise post.error.HTTPError(URL, 400, "Bad Request", {}, io.BytesIO(f"bad topic {TOPIC} at {URL}".encode()))

        with mock.patch.object(post.request, "urlopen", boom):
            with self.assertRaises(post.PostError) as caught:
                post.send(TOPIC, "x")
        self.assertNotIn(TOPIC, str(caught.exception))
        self.assertIn("400", str(caught.exception))

    def test_http_error_with_unreadable_body_still_reports_the_status(self):
        def boom(request, timeout):
            raise post.error.HTTPError(URL, 503, "Unavailable", {}, None)

        with mock.patch.object(post.request, "urlopen", boom):
            with self.assertRaises(post.PostError) as caught:
                post.send(TOPIC, "x")
        self.assertIn("503", str(caught.exception))

    def test_network_error_becomes_post_error_without_the_topic(self):
        def boom(request, timeout):
            raise post.error.URLError(f"nodename nor servname provided for {URL}")

        with mock.patch.object(post.request, "urlopen", boom):
            with self.assertRaises(post.PostError) as caught:
                post.send(TOPIC, "x")
        self.assertNotIn(TOPIC, str(caught.exception))


class CommandLineTest(unittest.TestCase):
    def run_main(self, argv, env):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
            code = post.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_missing_topic_exits_3(self):
        code, _, err = self.run_main(["--user", "42", "--busy", "5", "--tasks", "dishes"], {})
        self.assertEqual(code, 3)
        self.assertIn("BAILOUT_RELAY_TOKEN", err)

    def test_topic_that_is_a_url_exits_3_without_echoing_it(self):
        code, _, err = self.run_main(["--user", "42", "--busy", "5", "--tasks", "dishes"], {"BAILOUT_RELAY_TOKEN": URL})
        self.assertEqual(code, 3)
        self.assertNotIn(TOPIC, err)

    def test_bad_arguments_exit_2(self):
        code, _, err = self.run_main(["--user", "me", "--busy", "5", "--tasks", "dishes"], {"BAILOUT_RELAY_TOKEN": TOPIC})
        self.assertEqual(code, 2)
        self.assertIn("user", err)

    def test_dry_run_prints_the_line_and_does_not_post(self):
        with mock.patch.object(post, "send", side_effect=AssertionError("should not post")):
            code, out, _ = self.run_main(["--user", "42", "--busy", "5", "--tasks", "dishes", "--dry-run"],
                                         {"BAILOUT_RELAY_TOKEN": TOPIC})
        self.assertEqual(code, 0)
        self.assertIn("bailout_request user=42 busy=5 tasks=dishes source=cubbie", out)

    def test_dry_run_works_without_a_topic(self):
        code, out, _ = self.run_main(["--user", "42", "--busy", "5", "--tasks", "dishes", "--dry-run"], {})
        self.assertEqual(code, 0)
        self.assertIn("busy=5", out)

    def test_success_prints_sent_and_exits_0(self):
        with mock.patch.object(post, "send", return_value=None) as send:
            code, out, _ = self.run_main(["--user", "42", "--busy", "9", "--tasks", "dishes,trash"],
                                         {"BAILOUT_RELAY_TOKEN": TOPIC})
        self.assertEqual(code, 0)
        self.assertEqual(send.call_args.args[0], TOPIC)
        self.assertIn("sent", out.lower())
        self.assertNotIn(TOPIC, out)

    def test_post_failure_exits_4(self):
        with mock.patch.object(post, "send", side_effect=post.PostError("ntfy answered HTTP 404")):
            code, _, err = self.run_main(["--user", "42", "--busy", "9", "--tasks", "dishes"],
                                         {"BAILOUT_RELAY_TOKEN": TOPIC})
        self.assertEqual(code, 4)
        self.assertIn("404", err)


if __name__ == "__main__":
    unittest.main()
