"""Tests for relay.py: the ntfy.sh relay that carries the Cubbie bridge line when Discord blocks the webhook."""

import asyncio
import json
import unittest

from bridge import BridgeError
import relay

MAYA = 42
NOW = 1_700_000_000.0
VALID = f"bailout_request user={MAYA} busy=9 tasks=dishes,trash source=cubbie"
TOPIC = "bailout-house-k3j9x"


def find_user(user_id):
    return object() if user_id == MAYA else None


def event(message=VALID, id="m1", time=NOW, kind="message"):
    return {"id": id, "time": time, "event": kind, "topic": TOPIC, "message": message}


class RelayUrlTest(unittest.TestCase):
    def test_first_poll_asks_for_the_last_60_seconds(self):
        self.assertEqual(relay.relay_url(TOPIC, None), f"https://ntfy.sh/{TOPIC}/json?poll=1&since=60s")

    def test_later_polls_continue_after_the_last_id(self):
        self.assertEqual(relay.relay_url(TOPIC, "abc123"), f"https://ntfy.sh/{TOPIC}/json?poll=1&since=abc123")


class ParseFeedTest(unittest.TestCase):
    def test_reads_one_json_object_per_line_and_skips_blank_lines(self):
        text = json.dumps(event(id="a")) + "\n\n" + json.dumps(event(id="b")) + "\n"
        self.assertEqual([e["id"] for e in relay.parse_relay_feed(text)], ["a", "b"])

    def test_skips_lines_that_are_not_json_objects(self):
        text = "not json\n" + json.dumps(event(id="a")) + "\n[1,2]\n"
        self.assertEqual([e["id"] for e in relay.parse_relay_feed(text)], ["a"])

    def test_empty_body_is_no_events(self):
        self.assertEqual(relay.parse_relay_feed(""), [])


class InboxAcceptTest(unittest.TestCase):
    def setUp(self):
        self.inbox = relay.RelayInbox()

    def test_fresh_valid_message_becomes_a_bridge_request(self):
        request = self.inbox.accept(event(), find_user, now=NOW)
        self.assertEqual((request.user_id, request.busy, request.tasks), (MAYA, 9, ("dishes", "trash")))

    def test_same_id_twice_is_rejected_as_a_duplicate(self):
        self.inbox.accept(event(id="m1"), find_user, now=NOW)
        with self.assertRaises(BridgeError) as caught:
            self.inbox.accept(event(id="m1"), find_user, now=NOW + 1)
        self.assertIn("duplicate", str(caught.exception))

    def test_different_id_with_same_text_is_accepted(self):
        self.inbox.accept(event(id="m1"), find_user, now=NOW)
        self.inbox.accept(event(id="m2"), find_user, now=NOW)

    def test_message_older_than_five_minutes_is_rejected_as_stale(self):
        with self.assertRaises(BridgeError) as caught:
            self.inbox.accept(event(time=NOW - relay.RELAY_STALE_SECONDS - 1), find_user, now=NOW)
        self.assertIn("stale", str(caught.exception))

    def test_message_just_inside_five_minutes_is_accepted(self):
        self.inbox.accept(event(time=NOW - relay.RELAY_STALE_SECONDS + 1), find_user, now=NOW)

    def test_message_without_a_timestamp_is_rejected(self):
        bad = event()
        del bad["time"]
        with self.assertRaises(BridgeError):
            self.inbox.accept(bad, find_user, now=NOW)

    def test_message_without_an_id_is_rejected(self):
        bad = event()
        del bad["id"]
        with self.assertRaises(BridgeError):
            self.inbox.accept(bad, find_user, now=NOW)

    def test_keepalive_and_open_events_are_rejected(self):
        for kind in ["keepalive", "open", "poll_request"]:
            with self.subTest(kind=kind), self.assertRaises(BridgeError):
                self.inbox.accept(event(id=kind, kind=kind), find_user, now=NOW)

    def test_unknown_user_is_rejected(self):
        with self.assertRaises(BridgeError) as caught:
            self.inbox.accept(event(message=VALID.replace(str(MAYA), "99")), find_user, now=NOW)
        self.assertIn("unknown user", str(caught.exception))

    def test_bad_line_is_rejected_with_the_parser_reason(self):
        with self.assertRaises(BridgeError) as caught:
            self.inbox.accept(event(message=VALID.replace("busy=9", "busy=42")), find_user, now=NOW)
        self.assertIn("busy", str(caught.exception))

    def test_rejected_message_still_counts_as_seen(self):
        with self.assertRaises(BridgeError):
            self.inbox.accept(event(id="bad", message="hello"), find_user, now=NOW)
        with self.assertRaises(BridgeError) as caught:
            self.inbox.accept(event(id="bad"), find_user, now=NOW)
        self.assertIn("duplicate", str(caught.exception))

    def test_since_follows_the_newest_id_even_when_rejected(self):
        self.assertIsNone(self.inbox.last_id)
        self.inbox.accept(event(id="m1"), find_user, now=NOW)
        self.assertEqual(self.inbox.last_id, "m1")
        with self.assertRaises(BridgeError):
            self.inbox.accept(event(id="m2", message="junk"), find_user, now=NOW)
        self.assertEqual(self.inbox.last_id, "m2")

    def test_seen_ids_are_bounded(self):
        inbox = relay.RelayInbox(max_seen=3)
        for i in range(5):
            inbox.accept(event(id=f"m{i}"), find_user, now=NOW)
        inbox.accept(event(id="m0"), find_user, now=NOW)  # fell out of the window, so no longer a duplicate
        with self.assertRaises(BridgeError):
            inbox.accept(event(id="m4"), find_user, now=NOW)


class FakeResponse:
    def __init__(self, text, status=200):
        self._text, self.status = text, status
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): return False
    def raise_for_status(self):
        if self.status >= 400:
            raise relay.aiohttp.ClientResponseError(None, (), status=self.status)
    async def text(self): return self._text


class FakeSession:
    def __init__(self, text, status=200):
        self.text, self.status, self.urls = text, status, []
    def get(self, url, **kwargs):
        self.urls.append(url)
        return FakeResponse(self.text, self.status)


class FetchTest(unittest.TestCase):
    def test_fetches_the_topic_feed_and_returns_events(self):
        session = FakeSession(json.dumps(event(id="a")) + "\n")
        events = asyncio.run(relay.fetch_relay_events(session, TOPIC, None))
        self.assertEqual(session.urls, [relay.relay_url(TOPIC, None)])
        self.assertEqual([e["id"] for e in events], ["a"])

    def test_http_error_raises_without_the_topic_in_the_message(self):
        session = FakeSession("", status=429)
        with self.assertRaises(relay.RelayError) as caught:
            asyncio.run(relay.fetch_relay_events(session, TOPIC, None))
        self.assertIn("429", str(caught.exception))
        self.assertNotIn(TOPIC, str(caught.exception))


class ScrubTest(unittest.TestCase):
    def test_scrub_replaces_the_topic(self):
        self.assertEqual(relay.scrub(f"GET https://ntfy.sh/{TOPIC}/json failed", TOPIC), "GET https://ntfy.sh/<topic>/json failed")


if __name__ == "__main__":
    unittest.main()
