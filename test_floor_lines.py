"""Tests for floor-line templates and the LLM output validator in llm.py."""

import os
import unittest
from unittest import mock

from llm import FLOOR_EVENT_TYPES, _template_line, floor_message, validate_line

AWARDED = {"type": "awarded", "name": "jordan", "task": "dishes", "price": 9.0}
COMPLETED = {"type": "completed", "name": "jordan", "task": "dishes", "price": 9.0, "requester": "maya"}


class ValidatorTests(unittest.TestCase):
    def test_accepts_clean_awarded_line(self):
        self.assertTrue(validate_line("Jordan's taking dishes for 9.0 credits.", AWARDED))

    def test_rejects_dollar_sign(self):
        self.assertFalse(validate_line("Jordan's taking dishes for $9.00.", AWARDED))

    def test_rejects_money_words_case_insensitive(self):
        for word in ("Dollars", "PAID", "money", "dollar"):
            self.assertFalse(validate_line(f"Jordan takes dishes, {word} 9.0 credits", AWARDED), word)

    def test_rejects_number_not_in_event(self):
        self.assertFalse(validate_line("Jordan's taking dishes for 9.0 credits by 5pm.", AWARDED))

    def test_accepts_number_in_different_decimal_form(self):
        self.assertTrue(validate_line("Jordan's taking dishes for 9 credits.", AWARDED))

    def test_accepts_numbers_nested_in_event_data(self):
        event = {"type": "bid", "name": "priya", "task": "trash", "bid": 7.5, "busy_band": "medium"}
        self.assertTrue(validate_line("priya can do trash for 7.5 credits.", event))

    def test_awarded_must_not_claim_completion(self):
        for word in ("finished", "done", "completed", "Finished"):
            self.assertFalse(validate_line(f"Jordan {word} dishes for 9.0 credits.", AWARDED), word)

    def test_completed_may_say_finished(self):
        self.assertTrue(validate_line("Jordan finished dishes for Maya. +9.0 credits to Jordan.", COMPLETED))

    def test_rejects_empty_line(self):
        self.assertFalse(validate_line("", AWARDED))
        self.assertFalse(validate_line("   ", AWARDED))

    def test_rejects_multiline(self):
        self.assertFalse(validate_line("Jordan's taking dishes.\nfor 9.0 credits", AWARDED))


class TemplateTests(unittest.TestCase):
    def test_every_event_type_has_a_template(self):
        self.assertEqual(
            set(FLOOR_EVENT_TYPES),
            {"request", "cubbie_request", "bid", "cap_check", "passed", "awarded", "declined", "completed", "payback_request", "payback_done"},
        )

    def test_cubbie_request_template_shows_only_the_band(self):
        event = {"type": "cubbie_request", "name": "maya", "busy_band": "high", "tasks": ["dishes", "trash"]}
        self.assertEqual(_template_line(event), "Maya's Cubbie says: swamped this week (busy: high). Agents, bids on dishes, trash please.")

    def test_cubbie_request_rejects_a_busy_number(self):
        event = {"type": "cubbie_request", "name": "maya", "busy_band": "high", "tasks": ["dishes"]}
        self.assertFalse(validate_line("Maya's Cubbie says: swamped this week (busy: 9). Agents, bids please.", event))

    def test_cap_check_template_says_the_agent_is_checking(self):
        event = {"type": "cap_check", "name": "priya", "task": "trash"}
        self.assertEqual(_template_line(event), "priya's agent is checking with them before bidding on trash.")

    def test_passed_template(self):
        event = {"type": "passed", "name": "priya", "task": "trash"}
        self.assertEqual(_template_line(event), "priya's agent passed on trash.")

    def test_awarded_template(self):
        self.assertEqual(_template_line(AWARDED), "jordan's taking dishes for 9.0 credits.")

    def test_completed_template(self):
        self.assertEqual(_template_line(COMPLETED), "jordan finished dishes for maya. +9.0 credits to jordan.")

    def test_no_ledger_event_type(self):
        self.assertNotIn("ledger", FLOOR_EVENT_TYPES)

    def test_payback_request_template_is_a_friendly_offer_not_a_demand(self):
        event = {"type": "payback_request", "name": "jordan", "busy_band": "high", "debtor": "maya"}
        self.assertEqual(_template_line(event), "jordan's swamped. maya's agent is first in line to help.")

    def test_payback_request_rejects_demand_wording(self):
        event = {"type": "payback_request", "name": "jordan", "busy_band": "high", "debtor": "maya"}
        self.assertTrue(validate_line("jordan's swamped. maya's agent is first in line to help.", event))
        for bad in (
            "maya, you owe them credits. time to pay it back.",
            "maya owes jordan a favor.",
            "jordan is calling in maya's debt.",
            "maya must pay back now.",
            "jordan's swamped. maya, you owe them 19.5 credits.",
        ):
            self.assertFalse(validate_line(bad, event), bad)

    def test_payback_done_template(self):
        event = {"type": "payback_done", "name": "maya", "task": "trash", "price": 5.0, "creditor": "jordan"}
        self.assertEqual(_template_line(event), "maya's taking trash off jordan's plate for 5.0 credits. paying back.")

    def test_every_template_passes_the_validator(self):
        events = [
            {"type": "request", "name": "maya", "busy_band": "high", "tasks": ["dishes", "trash"]},
            {"type": "cubbie_request", "name": "maya", "busy_band": "high", "tasks": ["dishes", "trash"]},
            {"type": "bid", "name": "jordan", "task": "dishes", "bid": 5.2, "busy_band": "low"},
            {"type": "cap_check", "name": "priya", "task": "trash"},
            {"type": "passed", "name": "priya", "task": "trash"},
            AWARDED,
            {"type": "declined", "name": "jordan", "task": "dishes"},
            COMPLETED,
            {"type": "payback_request", "name": "jordan", "busy_band": "high", "debtor": "maya"},
            {"type": "payback_done", "name": "maya", "task": "trash", "price": 5.0, "creditor": "jordan"},
        ]
        for event in events:
            self.assertTrue(validate_line(_template_line(event), event), event["type"])


class FloorMessageValidationTests(unittest.TestCase):
    def _fake_client(self, text):
        block = mock.Mock(type="text", text=text)
        response = mock.Mock(stop_reason="end_turn", content=[block])
        client = mock.Mock()
        client.messages.create.return_value = response
        return client

    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"})
    def test_bad_llm_line_falls_back_to_template_and_logs(self):
        with mock.patch("llm._client", return_value=self._fake_client("Jordan finished dishes for $9.00")):
            with self.assertLogs("bailout.llm", level="WARNING") as captured:
                line = floor_message(AWARDED)
        self.assertEqual(line, _template_line(AWARDED))
        self.assertTrue(any("validation" in msg for msg in captured.output))

    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"})
    def test_good_llm_line_is_used(self):
        with mock.patch("llm._client", return_value=self._fake_client("Jordan's on dishes for 9.0 credits.")):
            self.assertEqual(floor_message(AWARDED), "Jordan's on dishes for 9.0 credits.")

    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"})
    def test_llm_is_told_the_event_type(self):
        client = self._fake_client("Jordan's on dishes for 9.0 credits.")
        with mock.patch("llm._client", return_value=client):
            floor_message(AWARDED)
        sent = client.messages.create.call_args.kwargs
        self.assertIn("awarded", sent["messages"][0]["content"])
        self.assertIn("credits", sent["system"])


if __name__ == "__main__":
    unittest.main()
