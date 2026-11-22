"""Tests for specialists.py with the model calls replaced by stand-ins. No API calls.

    .venv/bin/python -m unittest week-05/test_specialists.py -v      (from the top of the repository)
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import specialists  # noqa: E402
import supply_data  # noqa: E402
from common import Email  # noqa: E402

BRIEF = {
    "part_id": "MC-500",
    "what_changed": "120 arrive on day 5 and 280 on day 26. City of Ashford Bike Share (O-1043) is hit hardest.",
    "orders_at_risk": [
        {"order_id": "O-1043", "customer": "City of Ashford Bike Share", "units_short": 10,
         "ship_day": 14, "ready_day": 26, "late_penalty": True},
        {"order_id": "O-1044", "customer": "Main Street Bikes", "units_short": 30,
         "ship_day": 18, "ready_day": 26, "late_penalty": False},
    ],
    "options": [],
    "decisions_for_a_person": ["Tell Main Street Bikes?"],
    "supplier_questions": [],
}


def fake_email() -> Email:
    message = {"From": "Karen Walsh <k.walsh@northgatedrive.example>", "Subject": "PO-7781"}
    return Email(Path("01-test.eml"), message, "k.walsh@northgatedrive.example", "body")


class Scripted(specialists.Specialists):
    """Specialists whose writer and reviewer return scripted answers."""

    def __init__(self, out_dir: Path, drafts: list[str], verdicts: list[str]):
        super().__init__(client=None, data_dir=supply_data.DEFAULT_DATA_DIR, out_dir=out_dir, coordinator="test")
        self.drafts, self.verdicts = list(drafts), list(verdicts)
        self.revise_inputs = []

    async def write_supplier_reply(self, email, brief, revise=None):
        self.revise_inputs.append(revise)
        return self.drafts.pop(0)

    async def write_customer_notice(self, order, revise=None):
        self.revise_inputs.append(revise)
        return self.drafts.pop(0)

    async def review(self, draft, facts, recipient, email=None):
        text = self.verdicts.pop(0)

        async def fake_ask(*args, **kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])

        real = specialists.ask
        specialists.ask = fake_ask
        try:
            return await specialists.Specialists.review(self, draft, facts, recipient, email)
        finally:
            specialists.ask = real


def run(coro):
    with contextlib.redirect_stdout(io.StringIO()):
        return asyncio.run(coro)


class ReviewStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_revises_at_most_twice(self):
        team = Scripted(self.out, ["d1", "d2", "d3"], ["REVISE\n1. fix a", "REVISE\n2. fix b", "REVISE\n3. fix c"])
        draft = run(team.prepare_supplier_reply(fake_email(), BRIEF))
        self.assertEqual(draft.text, "d3")
        self.assertEqual(team.revise_inputs, [None, ("d1", "1. fix a"), ("d2", "2. fix b")])
        self.assertFalse(draft.approved)
        self.assertTrue(draft.for_person)
        self.assertIn("For a person: YES", (self.out / "supplier-reply.txt").read_text())

    def test_approved_after_one_revision(self):
        team = Scripted(self.out, ["d1", "d2"], ["REVISE\nfix", "APPROVE"])
        draft = run(team.prepare_supplier_reply(fake_email(), BRIEF))
        self.assertEqual((draft.text, draft.approved, draft.for_person), ("d2", True, False))

    def test_last_verdict_wins_approve_then_revise(self):
        team = Scripted(self.out, ["d1", "d2"], ["APPROVE\n\nWait, let me recheck.\n\nREVISE\n1. Rule 2", "APPROVE"])
        run(team.prepare_supplier_reply(fake_email(), BRIEF))
        self.assertEqual(team.revise_inputs[1], ("d1", "1. Rule 2"))

    def test_last_verdict_wins_revise_then_approve(self):
        said = "REVISE\n\n1. Names the customer...\n\nWait, rule 4 allows that. Re-checking all rules:\n\nAPPROVE"
        team = Scripted(self.out, ["d1"], [said])
        draft = run(team.prepare_customer_notice(BRIEF, "O-1044"))
        self.assertEqual((draft.approved, draft.for_person, len(team.revise_inputs)), (True, False, 1))

    def test_no_verdict_counts_as_revise(self):
        team = Scripted(self.out, ["d1", "d2"], ["Looks fine to me.", "**APPROVE**"])
        draft = run(team.prepare_supplier_reply(fake_email(), BRIEF))
        self.assertTrue(draft.approved)
        self.assertEqual(len(team.revise_inputs), 2)

    def test_customer_name_in_supplier_draft(self):
        team = Scripted(self.out, ["We are delaying Main Street Bikes."], ["APPROVE"])
        draft = run(team.prepare_supplier_reply(fake_email(), BRIEF))
        self.assertTrue(draft.approved)
        self.assertTrue(draft.for_person)
        self.assertIn("Main Street Bikes", draft.person_reasons[0])

    def test_supplier_name_in_customer_notice(self):
        team = Scripted(self.out, ["Northgate Drive Systems is late."], ["APPROVE"])
        draft = run(team.prepare_customer_notice(BRIEF, "O-1044"))
        self.assertEqual(draft.to, "owner@mainstreetbikes.example")
        self.assertTrue(any("Northgate Drive Systems" in r for r in draft.person_reasons))

    def test_late_penalty_marks_notice(self):
        team = Scripted(self.out, ["Your order is late."], ["APPROVE"])
        draft = run(team.prepare_customer_notice(BRIEF, "O-1043"))
        self.assertEqual(draft.person_reasons, ["O-1043 has a late penalty."])
        clean = run(Scripted(self.out, ["Your order is late."], ["APPROVE"]).prepare_customer_notice(BRIEF, "O-1044"))
        self.assertFalse(clean.for_person)

    def test_no_reply_needed_saves_nothing(self):
        team = Scripted(self.out, ["NO REPLY NEEDED"], [])
        self.assertIsNone(run(team.prepare_supplier_reply(fake_email(), BRIEF)))
        self.assertFalse((self.out / "supplier-reply.txt").exists())


class FactsTests(unittest.TestCase):
    def setUp(self):
        self.team = specialists.Specialists(None, supply_data.DEFAULT_DATA_DIR, Path("."))

    def test_supplier_brief_has_no_customer_names_or_orders(self):
        text = str(self.team.supplier_brief(BRIEF))
        for row in self.team.data.orders:
            self.assertNotIn(row["customer"], text)
            self.assertNotIn(row["order_id"], text)
        self.assertIn("'units_short': 10", text)
        self.assertIn("'days_late': 12", text)

    def test_order_facts_are_one_order_only(self):
        facts = self.team.order_facts(BRIEF, "O-1043")
        self.assertEqual(facts["days_later_than_planned"], 12)
        self.assertNotIn("late_penalty", facts)
        self.assertNotIn("Main Street", str(facts))

    def test_check_brief_needs_tool_numbers(self):
        data = supply_data.load()
        runs = [data.coverage("MC-500", [{"qty": 120, "arrives_in_days": 5}, {"qty": 280, "arrives_in_days": 26}])]
        good = {**BRIEF, "orders_at_risk": [dict(r) for r in BRIEF["orders_at_risk"]]}
        self.assertEqual(specialists.check_brief(good, data, runs), [])
        made_up = {**BRIEF, "orders_at_risk": [{**BRIEF["orders_at_risk"][0], "units_short": 15}]}
        self.assertTrue(specialists.check_brief(made_up, data, runs))
        self.assertTrue(specialists.check_brief(BRIEF, data, []))   # no check_coverage call at all


if __name__ == "__main__":
    unittest.main()
