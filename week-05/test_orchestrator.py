"""Tests for orchestrator.py with the model and the specialists replaced by stand-ins. No API calls.

    .venv/bin/python -m unittest week-05/test_orchestrator.py -v      (from the top of the repository)
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import orchestrator  # noqa: E402
import specialists  # noqa: E402
import supply_data  # noqa: E402
from test_specialists import BRIEF, fake_email  # noqa: E402


class FakeTeam(specialists.Specialists):
    """Specialists that return a fixed brief and save drafts without calling the model."""

    def __init__(self, out_dir: Path, brief: dict | None = BRIEF):
        super().__init__(client=None, data_dir=supply_data.DEFAULT_DATA_DIR, out_dir=out_dir,
                         coordinator="orchestrator")
        self.fixed_brief = copy.deepcopy(brief)
        self.calls: list[str] = []

    async def run_analyst(self, email):
        self.calls.append("analyst")
        return copy.deepcopy(self.fixed_brief)

    async def prepare_supplier_reply(self, email, brief):
        self.calls.append("supplier")
        draft = specialists.Draft("supplier", email.sender, "Re: x", "text", approved=True)
        return self._save(draft, "supplier-reply.txt", "test", "FINAL DRAFT TO SUPPLIER")

    async def prepare_customer_notice(self, brief, order_id, source=""):
        self.calls.append(order_id)
        draft = specialists.Draft("customer", "c@example.com", "x", "text", approved=True)
        return self._save(draft, f"notice-{order_id}.txt", source, f"FINAL DRAFT TO CUSTOMER {order_id}")


def run(coro):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        value = asyncio.run(coro)
    return value, out.getvalue()


class ToolRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.team = FakeTeam(Path(self.tmp.name))
        self.run = orchestrator.Orchestration(self.team, fake_email())

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, name, tool_input=None):
        return run(self.run.run_tool(name, tool_input or {}))[0]

    def test_notice_needs_brief(self):
        text, is_error = self.call("draft_customer_notice", {"order_id": "O-1043"})
        self.assertTrue(is_error)
        self.assertIn("Run the supply analyst first", text)
        self.assertEqual(self.team.calls, [])

    def test_notice_only_for_orders_at_risk(self):
        self.call("run_supply_analyst")
        text, is_error = self.call("draft_customer_notice", {"order_id": "O-9999"})
        self.assertTrue(is_error)
        self.assertIn("O-1043, O-1044", text)

    def test_notice_once_per_order(self):
        self.call("run_supply_analyst")
        self.assertFalse(self.call("draft_customer_notice", {"order_id": "o-1043"})[1])
        self.assertTrue(self.call("draft_customer_notice", {"order_id": "O-1043"})[1])
        self.assertEqual(self.team.calls, ["analyst", "O-1043"])

    def test_no_notice_while_options_undecided(self):
        brief = copy.deepcopy(BRIEF)
        brief["options"] = [{"name": "Option A", "order_ids": ["O-1043"]}]
        self.team.fixed_brief = brief
        self.call("run_supply_analyst")
        text, is_error = self.call("draft_customer_notice", {"order_id": "O-1043"})
        self.assertTrue(is_error)
        self.assertIn("choose one", text)

    def test_analyst_and_supplier_reply_run_once(self):
        self.call("run_supply_analyst")
        self.assertTrue(self.call("run_supply_analyst")[1])
        self.call("draft_supplier_reply")
        self.assertTrue(self.call("draft_supplier_reply")[1])
        self.assertEqual(self.team.calls, ["analyst", "supplier"])

    def test_skipped_says_options_when_no_order_listed_at_risk(self):
        # Like Marlow: each option's orders are under options, and orders_at_risk is empty.
        brief = copy.deepcopy(BRIEF)
        brief["orders_at_risk"] = []
        brief["options"] = [{"name": "Option A", "order_ids": ["O-1043"]}, {"name": "Option B", "order_ids": []}]
        self.team.fixed_brief = brief
        self.call("run_supply_analyst")
        skipped = self.run.skipped("")
        self.assertTrue(any("supplier offered options" in s for s in skipped))
        self.assertNotIn("Customer notices: no orders at risk.", skipped)

    def test_skipped_lists_missing_notices(self):
        self.call("run_supply_analyst")
        self.call("draft_customer_notice", {"order_id": "O-1043"})
        skipped = self.run.skipped("")
        self.assertIn("Customer notices: none drafted for O-1044.", skipped)
        self.assertIn("Supplier reply: the orchestrator did not draft one.", skipped)


def tool_use(name, tool_input=None, n=0):
    return SimpleNamespace(type="tool_use", id=f"t{n}", name=name, input=tool_input or {})


class ScriptedClient:
    """A client whose responses are a fixed list of tool requests, one per turn."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.messages = SimpleNamespace(create=self.create)

    async def create(self, **kwargs):
        blocks = self.turns.pop(0) if self.turns else [SimpleNamespace(type="text", text="done")]
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return SimpleNamespace(content=blocks, stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(orchestrator, "DRAFTS_DIR", Path(self.tmp.name))
        self.patch.start()
        self.team = None

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def orchestrate(self, turns):
        def make_team(client, data_dir, out_dir, coordinator):
            self.team = FakeTeam(out_dir)
            return self.team

        with mock.patch.object(orchestrator, "Specialists", make_team):
            return run(orchestrator.run_orchestrator(ScriptedClient(turns), supply_data.DEFAULT_DATA_DIR,
                                                     fake_email()))

    def test_full_run_and_summary(self):
        ok, out = self.orchestrate([
            [tool_use("run_supply_analyst", n=1)],
            [tool_use("draft_customer_notice", {"order_id": "O-1043"}, 2),
             tool_use("draft_customer_notice", {"order_id": "O-1044"}, 3)],
            [tool_use("draft_supplier_reply", n=4)],
            [tool_use("finish", {"summary": "Two notices and a reply.", "person_must_decide": True}, 5)],
        ])
        self.assertTrue(ok)
        self.assertEqual(self.team.calls, ["analyst", "O-1043", "O-1044", "supplier"])
        self.assertIn("Orchestrator's summary: Two notices and a reply.", out)
        self.assertIn("Orchestrator says a person must decide: YES", out)
        self.assertIn("Drafts saved (3)", out)
        self.assertIn("  - Tell Main Street Bikes?", out)
        self.assertTrue((self.team.out_dir / "summary.txt").is_file())

    def test_stops_after_ten_tool_calls(self):
        turns = [[tool_use("draft_customer_notice", {"order_id": "O-9999"}, n)] for n in range(15)]
        ok, out = self.orchestrate(turns)
        self.assertFalse(ok)
        self.assertIn("[orchestrator step 10]", out)
        self.assertNotIn("[orchestrator step 11]", out)
        self.assertIn("Reached the limit of 10 tool calls", out)
        self.assertIn("stopped without finishing", out)

    def test_stopping_without_finish_is_reported(self):
        ok, out = self.orchestrate([[SimpleNamespace(type="text", text="Nothing to do.")]])
        self.assertFalse(ok)
        self.assertIn("finish: never called", out)
        self.assertIn("Supply analyst: the orchestrator did not run it.", out)


EMAILS = supply_data.DEFAULT_DATA_DIR / "supplier-emails"


class ReadingCheckTests(unittest.TestCase):
    def sentences(self, name):
        from common import read_email
        return specialists.schedule_sentences(read_email(EMAILS / name).body)

    def test_northgate_timing_sentence_is_shown(self):
        found = self.sentences("01-northgate-controller-delay.eml")
        self.assertIn("The remaining 280 will follow three weeks later.", found)
        self.assertIn("We can ship 120 of the 400 controllers on the scheduled date.", found)
        self.assertFalse(any("sorry" in s for s in found))
        self.assertFalse(any("Karen Walsh" in s for s in found))

    def test_kendall_options_and_deadline_are_shown(self):
        found = self.sentences("02-kendall-tire-options.eml")
        self.assertTrue(any(s.startswith("Option 1:") and "two weeks later" in s for s in found))
        self.assertTrue(any(s.startswith("Option 2:") for s in found))
        self.assertIn("Please tell us which option you prefer within two business days.", found)

    def test_summary_shows_both_sides(self):
        from common import read_email
        email = read_email(EMAILS / "01-northgate-controller-delay.eml")
        lines = specialists.reading_check_lines(email, BRIEF)
        self.assertIn('    "The remaining 280 will follow three weeks later."', lines)
        self.assertEqual(lines[-1], f"  Analyst's reading: {BRIEF['what_changed']}")
        self.assertEqual(specialists.reading_check_lines(email, None)[-1],
                         "  Analyst's reading: none (no checked brief)")


if __name__ == "__main__":
    unittest.main()
