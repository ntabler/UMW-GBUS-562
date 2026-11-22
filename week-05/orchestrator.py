#!/usr/bin/env python3
"""Coordinator 2: Claude decides which specialists to use for one supplier email.

From the top of the repository:

    .venv/bin/python week-05/orchestrator.py \
        --email week-05/brookfield-bikes/supplier-emails/01-northgate-controller-delay.eml
    .venv/bin/python week-05/orchestrator.py --email <file.eml> --save northgate-orchestrator-run.txt

An agent (common.MODEL, claude-sonnet-5) gets the system prompt in
brookfield-bikes/prompts/orchestrator.txt and four tools:
  - run_supply_analyst      the supply analyst from specialists.py (JSON brief)
  - draft_supplier_reply    the supplier writer, then the review step
  - draft_customer_notice   the customer writer for one order ID, then the review step
  - finish                  a summary for a person, and whether a person must decide
It stops after 10 tool calls. Each choice it makes is printed.

Code still enforces the same rules as the pipeline: a customer notice needs a
checked brief that lists the order as at risk, and no notice is drafted while
the supplier's options are undecided. Each specialist runs at most once per
email (once per order for notices). Drafts are saved to
week-05/drafts/<email>-orchestrator-<time>/ and never sent.

At the end it prints the same summary as the pipeline, plus the orchestrator's
own summary and whether it says a person must decide.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from common import DRAFTS_DIR, Email, handoff, heading, read_prompt, run_cli, tool_loop
from specialists import Draft, Specialists, brief_decisions, print_summary

MAX_TOOL_CALLS = 10

NO_INPUT = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

TOOLS = [
    {
        "name": "run_supply_analyst",
        "description": (
            "Run the supply analyst on this email. It checks parts, purchase orders, and customer "
            "orders, and returns a JSON brief: the part, what changed, the orders at risk (with "
            "customer, units short, planned and expected ship days, late penalty), the supplier's "
            "options, decisions for a person, and questions the supplier asked. Run it before "
            "drafting anything that depends on the impact. It can run once per email."
        ),
        "input_schema": NO_INPUT,
        "strict": True,
    },
    {
        "name": "draft_supplier_reply",
        "description": (
            "Draft Brookfield's reply to the supplier, using the analyst's brief if it has been run "
            "(customer names and order numbers are removed first). The draft is reviewed, revised "
            "up to 2 times, and saved for a person to check; nothing is sent. The writer may decide "
            "no reply is needed. Returns whether the reviewer approved it and whether it is marked "
            "for a person. It can run once per email."
        ),
        "input_schema": NO_INPUT,
        "strict": True,
    },
    {
        "name": "draft_customer_notice",
        "description": (
            "Draft a notice to the customer of one order at risk. The order must be in the "
            "analyst's brief, so run the analyst first. Not allowed while the supplier's options "
            "are undecided: a person must choose an option before customers are told. The draft is "
            "reviewed and saved for a person to check; nothing is sent. One call per order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "An order ID from the brief, e.g. O-1043."}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "finish",
        "description": (
            "End the job. Give a short summary for Brookfield's purchasing manager of what was done "
            "and what still needs attention, and whether a person must make a decision."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "A few sentences for the purchasing manager."},
                "person_must_decide": {"type": "boolean",
                                       "description": "True if a person must make a decision before anything goes out."},
            },
            "required": ["summary", "person_must_decide"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def draft_result(draft: Draft) -> str:
    """What the orchestrator is told about a saved draft."""
    return json.dumps({
        "saved_as": draft.path.name,
        "to": draft.to,
        "reviewer_approved": draft.approved,
        "for_a_person": draft.for_person,
        "reasons_for_a_person": draft.person_reasons,
    }, indent=2)


class Orchestration:
    """The state of one run, and the four tools that change it."""

    def __init__(self, team: Specialists, email: Email):
        self.team = team
        self.email = email
        self.brief: dict | None = None
        self.analyst_ran = False
        self.supplier_reply_done = False
        self.supplier_reply_needed = True
        self.drafts: list[Draft] = []
        self.noticed: set[str] = set()
        self.finished: dict | None = None    # the finish tool's input

    async def run_tool(self, name: str, tool_input: dict) -> tuple[str, bool]:
        handoff("orchestrator", name.replace("_", " "), "choice",
                json.dumps(tool_input) if tool_input else None)
        if name == "run_supply_analyst":
            return await self.run_supply_analyst()
        if name == "draft_supplier_reply":
            return await self.draft_supplier_reply()
        if name == "draft_customer_notice":
            return await self.draft_customer_notice(str(tool_input.get("order_id", "")).strip().upper())
        if name == "finish":
            return self.finish(tool_input)
        return f"Unknown tool {name!r}.", True

    async def run_supply_analyst(self) -> tuple[str, bool]:
        if self.analyst_ran:
            return "The supply analyst has already run for this email. Use the brief you have.", True
        self.analyst_ran = True
        self.brief = await self.team.run_analyst(self.email)
        if self.brief is None:
            return ("The supply analyst produced no usable brief, so no customer notices can be drafted. "
                    "A person must work out the impact by hand."), True
        return json.dumps(self.brief, indent=2), False

    async def draft_supplier_reply(self) -> tuple[str, bool]:
        if self.supplier_reply_done:
            return "The supplier reply has already been drafted.", True
        self.supplier_reply_done = True
        draft = await self.team.prepare_supplier_reply(self.email, self.brief)
        if draft is None:
            self.supplier_reply_needed = False
            return "The supplier writer found that no reply is needed. Nothing was saved.", False
        self.drafts.insert(0, draft)
        return draft_result(draft), False

    async def draft_customer_notice(self, order_id: str) -> tuple[str, bool]:
        if self.brief is None:
            return "There is no checked brief yet. Run the supply analyst first.", True
        if self.brief["options"]:
            return ("The supplier offered options, and a person must choose one before any customer is "
                    "told. No notice was drafted."), True
        at_risk = [r["order_id"] for r in self.brief["orders_at_risk"]]
        if order_id not in at_risk:
            return (f"{order_id or '(none)'} is not an order at risk in the brief. "
                    f"Orders at risk: {', '.join(at_risk) or 'none'}."), True
        if order_id in self.noticed:
            return f"A notice for {order_id} has already been drafted.", True
        self.noticed.add(order_id)
        draft = await self.team.prepare_customer_notice(self.brief, order_id, self.email.path.name)
        self.drafts.append(draft)
        return draft_result(draft), False

    def finish(self, tool_input: dict) -> tuple[str, bool]:
        summary = str(tool_input.get("summary", "")).strip()
        if not summary:
            return "finish needs a summary.", True
        self.finished = {"summary": summary, "person_must_decide": bool(tool_input.get("person_must_decide"))}
        return "Done.", False

    def skipped(self, stopped: str) -> list[str]:
        """What the orchestrator did not do, so a person can see the gaps."""
        out = []
        if not self.analyst_ran:
            out.append("Supply analyst: the orchestrator did not run it.")
        if self.brief is not None:
            at_risk = [r["order_id"] for r in self.brief["orders_at_risk"]]
            missing = [o for o in at_risk if o not in self.noticed]
            if self.brief["options"] and at_risk:
                out.append("Customer notices: the supplier offered options, so no notice is drafted "
                           "until a person chooses one.")
            elif not at_risk:
                out.append("Customer notices: no orders at risk.")
            elif missing:
                out.append(f"Customer notices: none drafted for {', '.join(missing)}.")
        if not self.supplier_reply_done:
            out.append("Supplier reply: the orchestrator did not draft one.")
        elif not self.supplier_reply_needed:
            out.append("Supplier reply: the writer found that no reply is needed.")
        if self.finished is None:
            out.append(f"finish: never called. {stopped}")
        return out


async def run_orchestrator(client, data_dir: Path, email: Email) -> bool:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = DRAFTS_DIR / f"{email.path.stem}-orchestrator-{stamp}"
    team = Specialists(client, data_dir, out_dir, coordinator="orchestrator")
    print(f"Email:   {email.path}\nFrom:    {email.message.get('From', email.sender)}\nSubject: {email.subject}")

    heading("Orchestrator")
    run = Orchestration(team, email)
    messages = [{"role": "user", "content": (
        "Coordinate Brookfield's response to this supplier email. Call finish when you are done.\n\n"
        + email.as_text())}]
    result = await tool_loop(client, team.usage, "orchestrator", read_prompt(data_dir, "orchestrator"),
                             messages, TOOLS, run.run_tool, MAX_TOOL_CALLS,
                             is_done=lambda: run.finished is not None)
    if result.text:
        print(f"\n[orchestrator] {result.text}")
    print(f"\n[orchestrator] {result.stopped} {result.tool_calls} of {MAX_TOOL_CALLS} tool calls used.")

    decisions: list[str] = []
    if run.analyst_ran and run.brief is None:
        decisions.append("The supply analyst produced no usable brief: work out the impact of this email by hand.")
    if run.brief is not None:
        decisions += brief_decisions(run.brief)

    if run.finished is not None:
        extra = [f"Orchestrator's summary: {run.finished['summary']}",
                 f"Orchestrator says a person must decide: {'YES' if run.finished['person_must_decide'] else 'no'}"]
        if run.finished["person_must_decide"] and not decisions:
            decisions.append("The orchestrator says a person must decide; see its summary.")
    else:
        extra = ["Orchestrator's summary: none (it stopped without calling finish)"]
        decisions.append("The orchestrator stopped without finishing: check this email by hand.")

    print_summary(team, email, out_dir, run.drafts, decisions, run.skipped(result.stopped), extra=extra)
    return run.finished is not None and not (run.analyst_ran and run.brief is None)


if __name__ == "__main__":
    run_cli("Prepare Brookfield's response to one supplier email; Claude decides which specialists to use.",
            run_orchestrator)
