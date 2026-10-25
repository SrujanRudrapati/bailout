"""Tests for floor-line templates and the LLM output validator in llm.py."""

import os
import unittest
from unittest import mock

from llm import FLOOR_EVENT_TYPES, _template_line, capitalize_names, floor_message, validate_line

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
            {"request", "cubbie_request", "bid", "cap_check", "passed", "awarded", "declined", "unfilled", "completed", "payback_request", "payback_done"},
        )

    def test_cubbie_request_template_shows_only_the_band(self):
        event = {"type": "cubbie_request", "name": "maya", "busy_band": "high", "tasks": ["dishes", "trash"]}
        self.assertEqual(_template_line(event), "Maya's Cubbie says: swamped this week (busy: high). Agents, bids on dishes, trash please.")

    def test_cubbie_request_rejects_a_busy_number(self):
        event = {"type": "cubbie_request", "name": "maya", "busy_band": "high", "tasks": ["dishes"]}
        self.assertFalse(validate_line("Maya's Cubbie says: swamped this week (busy: 9). Agents, bids please.", event))

    def test_cap_check_template_names_nobody(self):
        event = {"type": "cap_check", "task": "trash"}
        self.assertEqual(_template_line(event), "an agent is checking with its human before bidding on trash.")

    def test_passed_template_names_nobody(self):
        event = {"type": "passed", "task": "trash"}
        self.assertEqual(_template_line(event), "one agent is sitting out trash.")

    def test_declined_template_says_next_agent_is_up(self):
        self.assertEqual(_template_line({"type": "declined", "task": "dishes", "next": True}), "one agent passed on dishes. next agent is up.")
        self.assertEqual(_template_line({"type": "declined", "task": "dishes", "next": False}), "one agent passed on dishes.")
        self.assertEqual(_template_line({"type": "declined", "task": "payback", "next": False}), "one agent passed on payback.")

    def test_anonymous_events_reject_a_line_that_names_a_roommate(self):
        names = ["maya", "jordan", "priya"]
        for event in ({"type": "declined", "task": "dishes", "next": True}, {"type": "passed", "task": "dishes"}, {"type": "cap_check", "task": "dishes"}):
            self.assertFalse(validate_line("Jordan passed on dishes.", event, names), event["type"])
            self.assertFalse(validate_line("jordan's agent is checking.", event, names), event["type"])
            self.assertTrue(validate_line("one agent passed on dishes.", event, names), event["type"])
        # a named event may of course name its own roommate
        self.assertTrue(validate_line("Jordan's taking dishes for 9.0 credits.", AWARDED, names))

    def test_awarded_template(self):
        self.assertEqual(_template_line(AWARDED), "Jordan's taking dishes for 9.0 credits.")

    def test_completed_template(self):
        self.assertEqual(_template_line(COMPLETED), "Jordan finished dishes for Maya. +9.0 credits to Jordan.")

    def test_unfilled_template_echoes_the_reason_the_code_chose(self):
        event = {"type": "unfilled", "name": "maya", "task": "dishes", "bids": 2, "reason": "2 agents bid, but every human passed."}
        self.assertEqual(_template_line(event), "nobody's taking dishes for Maya this time: 2 agents bid, but every human passed.")

    def test_unfilled_template_capitalizes_the_name_inside_the_reason(self):
        event = {"type": "unfilled", "name": "maya", "task": "dishes", "bids": 0, "reason": "nobody's less busy than maya this week."}
        self.assertEqual(_template_line(event), "nobody's taking dishes for Maya this time: nobody's less busy than Maya this week.")

    def test_unfilled_rejects_a_busy_claim_the_code_did_not_make(self):
        # the reason here is about the humans passing, so a busy comparison is an invention
        event = {"type": "unfilled", "name": "maya", "task": "dishes", "bids": 2, "reason": "2 agents bid, but every human passed."}
        self.assertFalse(validate_line("nobody's less busy than Maya this week.", event))

    def test_unfilled_allows_the_busy_reason_the_code_chose_but_never_a_number(self):
        event = {"type": "unfilled", "name": "maya", "task": "dishes", "bids": 0, "reason": "nobody's less busy than Maya this week."}
        self.assertTrue(validate_line("nobody's taking dishes for Maya this time: nobody's less busy than Maya this week.", event))
        self.assertFalse(validate_line("nobody's less busy than Maya (busy 2) this week.", event))

    def test_no_ledger_event_type(self):
        self.assertNotIn("ledger", FLOOR_EVENT_TYPES)

    def test_payback_request_template_is_a_friendly_offer_not_a_demand(self):
        event = {"type": "payback_request", "name": "jordan", "busy_band": "high", "debtor": "maya"}
        self.assertEqual(_template_line(event), "Jordan's swamped. Maya's agent is first in line to help.")

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
        self.assertEqual(_template_line(event), "Maya's taking trash off Jordan's plate for 5.0 credits. paying back.")

    def test_every_template_passes_the_validator(self):
        events = [
            {"type": "request", "name": "maya", "busy_band": "high", "tasks": ["dishes", "trash"]},
            {"type": "cubbie_request", "name": "maya", "busy_band": "high", "tasks": ["dishes", "trash"]},
            {"type": "bid", "name": "jordan", "task": "dishes", "bid": 5.2, "busy_band": "low"},
            {"type": "cap_check", "task": "trash"},
            {"type": "passed", "task": "trash"},
            AWARDED,
            {"type": "declined", "task": "dishes", "next": True},
            {"type": "declined", "task": "dishes", "next": False},
            {"type": "unfilled", "name": "maya", "task": "dishes", "bids": 2, "reason": "2 agents bid, but every human passed."},
            {"type": "unfilled", "name": "maya", "task": "dishes", "bids": 0, "reason": "nobody's less busy than Maya this week."},
            COMPLETED,
            {"type": "payback_request", "name": "jordan", "busy_band": "high", "debtor": "maya"},
            {"type": "payback_done", "name": "maya", "task": "trash", "price": 5.0, "creditor": "jordan"},
        ]
        for event in events:
            self.assertTrue(validate_line(_template_line(event), event, ["maya", "jordan", "priya"]), event["type"])


class CapitalizedNameTests(unittest.TestCase):
    """Names are stored lowercase; every floor line shows them capitalized."""

    def test_every_template_capitalizes_every_name_it_uses(self):
        events = [
            {"type": "request", "name": "maya", "busy_band": "high", "tasks": ["dishes", "trash"]},
            {"type": "bid", "name": "jordan", "task": "dishes", "bid": 5.2, "busy_band": "low"},
            AWARDED,
            COMPLETED,
            {"type": "payback_request", "name": "jordan", "busy_band": "high", "debtor": "maya"},
            {"type": "payback_done", "name": "maya", "task": "trash", "price": 5.0, "creditor": "jordan"},
        ]
        for event in events:
            line = _template_line(event)
            for name in ("maya", "jordan", "priya"):
                self.assertNotRegex(line, rf"\b{name}\b", event["type"])

    def test_capitalize_names_fixes_every_name_field(self):
        event = {"type": "completed", "name": "jordan", "task": "dishes", "price": 9.0, "requester": "maya"}
        self.assertEqual(capitalize_names("jordan did dishes for maya. jordan rocks.", event), "Jordan did dishes for Maya. Jordan rocks.")

    def test_capitalize_names_leaves_other_words_alone(self):
        event = {"type": "awarded", "name": "max", "task": "dishes", "price": 9.0}
        self.assertEqual(capitalize_names("max takes dishes, maximum effort.", event), "Max takes dishes, maximum effort.")


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
    def test_llm_line_gets_its_names_capitalized(self):
        with mock.patch("llm._client", return_value=self._fake_client("jordan's on dishes for 9.0 credits.")):
            self.assertEqual(floor_message(AWARDED), "Jordan's on dishes for 9.0 credits.")

    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"})
    def test_llm_line_naming_who_declined_falls_back_to_the_template(self):
        event = {"type": "declined", "task": "dishes", "next": True}
        with mock.patch("llm._client", return_value=self._fake_client("Jordan passed on dishes, Priya is up.")):
            with self.assertLogs("bailout.llm", level="WARNING"):
                line = floor_message(event, names=["maya", "jordan", "priya"])
        self.assertEqual(line, "one agent passed on dishes. next agent is up.")

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
