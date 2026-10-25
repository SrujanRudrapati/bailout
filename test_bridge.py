"""Tests for the Cubbie bridge parser in bridge.py. No Discord, no LLM."""

import unittest

from bridge import BridgeError, BridgeRequest, accept_bridge_message, parse_bridge_line

TRUSTED = 555
KNOWN_USERS = {42: "maya", 43: "jordan"}
VALID = "bailout_request user=42 busy=9 tasks=dishes,trash source=cubbie"


def find_user(discord_id: int):
    return KNOWN_USERS.get(discord_id)


class ParseBridgeLineTests(unittest.TestCase):
    def test_valid_line(self):
        self.assertEqual(
            parse_bridge_line(VALID),
            BridgeRequest(user_id=42, busy=9, tasks=("dishes", "trash"), source="cubbie"),
        )

    def test_surrounding_whitespace_and_task_case_are_normalized(self):
        request = parse_bridge_line("  bailout_request user=42 busy=1 tasks=Dishes,TRASH source=cubbie \n")
        self.assertEqual(request.busy, 1)
        self.assertEqual(request.tasks, ("dishes", "trash"))

    def test_bad_busy_value(self):
        for busy in ("0", "11", "-3", "high", "9.5", "", "09", "²", "٩", "1e1"):
            with self.assertRaises(BridgeError, msg=busy):
                parse_bridge_line(f"bailout_request user=42 busy={busy} tasks=dishes source=cubbie")

    def test_bad_user_id(self):
        for user in ("maya", "<@42>", "", "4a2", "042", "²", "٤٢", "1" * 21):
            with self.assertRaises(BridgeError, msg=user):
                parse_bridge_line(f"bailout_request user={user} busy=9 tasks=dishes source=cubbie")

    def test_empty_tasks_rejected(self):
        for tasks in ("", ",", "dishes,,trash", ",dishes"):
            with self.assertRaises(BridgeError, msg=tasks):
                parse_bridge_line(f"bailout_request user=42 busy=9 tasks={tasks} source=cubbie")

    def test_tasks_are_letters_only(self):
        for tasks in ("@everyone", "<@42>", "dishes9", "9", "exam.prep", "dishes;trash", "a" * 31, "ünicode"):
            with self.assertRaises(BridgeError, msg=tasks):
                parse_bridge_line(f"bailout_request user=42 busy=9 tasks={tasks} source=cubbie")
        self.assertEqual(parse_bridge_line("bailout_request user=42 busy=9 tasks=take-out_trash source=cubbie").tasks, ("take-out_trash",))

    def test_too_many_tasks_rejected(self):
        tasks = ",".join(f"t{chr(97 + i)}" for i in range(6))
        with self.assertRaises(BridgeError):
            parse_bridge_line(f"bailout_request user=42 busy=9 tasks={tasks} source=cubbie")

    def test_duplicate_tasks_collapse(self):
        self.assertEqual(parse_bridge_line("bailout_request user=42 busy=9 tasks=dishes,Dishes source=cubbie").tasks, ("dishes",))

    def test_long_line_and_carriage_return_rejected(self):
        with self.assertRaises(BridgeError):
            parse_bridge_line(VALID + " " * 400)
        with self.assertRaises(BridgeError):
            parse_bridge_line("bailout_request user=42 busy=9\r tasks=dishes source=cubbie")

    def test_wrong_source_rejected(self):
        with self.assertRaises(BridgeError):
            parse_bridge_line("bailout_request user=42 busy=9 tasks=dishes source=slack")

    def test_anything_else_rejected(self):
        for line in (
            "",
            "hello floor",
            "bailout_request",
            "bailout_request user=42 busy=9 tasks=dishes",  # missing source
            "bailout_request busy=9 user=42 tasks=dishes source=cubbie",  # wrong order
            "bailout_request user=42 busy=9 tasks=dishes source=cubbie extra=1",
            "bailout_request user=42 busy=9 tasks=dishes source=cubbie\nbailout_request user=43 busy=9 tasks=dishes source=cubbie",
            "BAILOUT_REQUEST user=42 busy=9 tasks=dishes source=cubbie",
        ):
            with self.assertRaises(BridgeError, msg=repr(line)):
                parse_bridge_line(line)

    def test_error_message_never_contains_the_busy_number(self):
        try:
            parse_bridge_line("bailout_request user=42 busy=77 tasks=dishes source=cubbie")
        except BridgeError as err:
            self.assertNotIn("77", str(err))
        else:
            self.fail("expected BridgeError")


class AcceptBridgeMessageTests(unittest.TestCase):
    def test_valid_message_from_trusted_webhook(self):
        request = accept_bridge_message(TRUSTED, VALID, TRUSTED, find_user)
        self.assertEqual(request.user_id, 42)
        self.assertEqual(request.busy, 9)

    def test_wrong_webhook_id_rejected(self):
        with self.assertRaises(BridgeError) as caught:
            accept_bridge_message(556, VALID, TRUSTED, find_user)
        self.assertIn("webhook", str(caught.exception))

    def test_missing_webhook_id_rejected(self):
        with self.assertRaises(BridgeError):
            accept_bridge_message(None, VALID, TRUSTED, find_user)

    def test_no_trusted_webhook_configured_rejects_everything(self):
        with self.assertRaises(BridgeError):
            accept_bridge_message(TRUSTED, VALID, None, find_user)

    def test_webhook_id_compared_as_number_not_string_prefix(self):
        with self.assertRaises(BridgeError):
            accept_bridge_message(5550, VALID, TRUSTED, find_user)

    def test_bad_busy_value_rejected(self):
        with self.assertRaises(BridgeError):
            accept_bridge_message(TRUSTED, "bailout_request user=42 busy=12 tasks=dishes source=cubbie", TRUSTED, find_user)

    def test_unknown_user_rejected(self):
        with self.assertRaises(BridgeError) as caught:
            accept_bridge_message(TRUSTED, "bailout_request user=99 busy=9 tasks=dishes source=cubbie", TRUSTED, find_user)
        self.assertIn("user", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
