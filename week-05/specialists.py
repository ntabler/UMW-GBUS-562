"""The four specialists, shared by both coordinators, for any company folder.

On its own, this file runs just the supply analyst and prints its brief:

    .venv/bin/python week-05/specialists.py --org week-05/copperline-coffee \
        --email week-05/copperline-coffee/supplier-emails/01-danforth-pump-delay.eml

In code:

    team = Specialists(client, data_dir, out_dir)
    brief  = await team.run_analyst(email)                    # agent + supply MCP tools
    text   = await team.write_supplier_reply(email, brief)    # one call; customer names removed first
    text   = await team.write_customer_notice(order)          # one call; one order's facts only
    ok, why = await team.review(draft, facts, recipient)      # one call; APPROVE or REVISE

    draft = await team.prepare_supplier_reply(email, brief)   # the review step, then save
    draft = await team.prepare_customer_notice(brief, "O-1043")

data_dir is the company folder (--org): its five CSV files and prompts/. Each
specialist reads its system prompt from <data_dir>/prompts/ on every call.
The model is common.MODEL (claude-sonnet-5).

The review step: write, review, and on REVISE send the reasons back to the
writer, at most 2 times. Then code checks that no customer name is in a supplier
draft and no supplier name is in a customer draft. A draft is marked for a
person if the reviewer never approved it, the code check fails, or (for a
customer notice) the order has a late penalty. Drafts are only saved to files
in week-05/drafts/. Nothing can be sent.

Every handoff (what goes from one step to the next) and the tokens each call
uses are printed; usage_lines() gives the totals by specialist. The analyst's
brief is printed in full, and after each review step the final draft is printed
once, between a FINAL DRAFT TO SUPPLIER (or TO CUSTOMER <order id>) line and a
"Marked for a person: yes/no" line.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import supply_data
from common import (REPO_DIR, WEEK_DIR, AgentError, Email, Usage, ask, handoff, heading,
                    mcp_result_text, read_prompt, relative, response_text, server_errlog, tool_loop)

SERVER_SCRIPT = WEEK_DIR / "supply_mcp_server.py"
ANALYST_MAX_TOOL_CALLS = 10
MAX_REVISIONS = 2
NO_REPLY = "NO REPLY NEEDED"

BRIEF_FIELDS = ["part_id", "what_changed", "orders_at_risk", "options",
                "decisions_for_a_person", "supplier_questions"]
RISK_FIELDS = ["order_id", "customer", "units_short", "ship_day", "ready_day", "late_penalty"]


@dataclass
class Draft:
    recipient: str            # "supplier" or "customer"
    to: str
    subject: str
    text: str
    approved: bool            # the reviewer's last verdict was APPROVE
    reviews: list[str] = field(default_factory=list)
    code_problems: list[str] = field(default_factory=list)
    person_reasons: list[str] = field(default_factory=list)
    path: Path | None = None

    @property
    def for_person(self) -> bool:
        return bool(self.person_reasons)


# --- Checking the analyst's brief --------------------------------------------------------


def extract_json(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise ValueError(f"The brief is not valid JSON ({error.msg}).") from None
    if not isinstance(value, dict):
        raise ValueError("The brief must be a JSON object.")
    return value


def check_brief(brief: dict, data: supply_data.SupplyData, coverage_runs: list[dict]) -> list[str]:
    """Problems with the brief: its fields, and order numbers that no check_coverage call returned."""
    if sorted(brief) != sorted(BRIEF_FIELDS):
        return [f"The brief must have exactly these fields: {', '.join(BRIEF_FIELDS)}."]
    try:
        part_id = data.part(str(brief["part_id"]))["part_id"]
    except supply_data.DataError as error:
        return [str(error)]
    problems = []
    if not isinstance(brief["what_changed"], str) or not brief["what_changed"].strip():
        problems.append("what_changed must be one sentence of text.")
    for name in ["orders_at_risk", "options", "decisions_for_a_person", "supplier_questions"]:
        if not isinstance(brief[name], list):
            problems.append(f"{name} must be a list (empty if none).")
    if problems:
        return problems

    uses_part = {o["order_id"] for o in data.orders_for_part(part_id)}
    seen: dict[str, list[dict]] = {}   # every at-risk result from this run's check_coverage calls
    for run in coverage_runs:
        if run.get("part_id") == part_id:
            for r in run.get("orders_at_risk", []):
                seen.setdefault(r["order_id"], []).append(r)

    for entry in brief["orders_at_risk"]:
        if not isinstance(entry, dict) or sorted(entry) != sorted(RISK_FIELDS):
            problems.append(f"Each orders_at_risk item needs exactly: {', '.join(RISK_FIELDS)}.")
            continue
        oid = str(entry["order_id"])
        if oid not in uses_part:
            problems.append(f"{oid} is not an open order that uses {part_id}.")
            continue
        row = data.order(oid)
        if entry["customer"] != row["customer"]:
            problems.append(f"{oid}'s customer is {row['customer']!r}, not {entry['customer']!r}.")
        numbers = (entry["units_short"], entry["ship_day"], entry["ready_day"])
        if not any(numbers == (r["units_short"], r["ship_day"], r["ready_day"]) for r in seen.get(oid, [])):
            problems.append(f"{oid}: units_short, ship_day, and ready_day must be copied from a "
                            "check_coverage result in this run; these don't match any.")
        entry["late_penalty"] = row["late_penalty"]   # always the value in orders.csv
    for option in brief["options"]:
        if not isinstance(option, dict) or not isinstance(option.get("name"), str) \
                or not isinstance(option.get("order_ids"), list):
            problems.append("Each option needs a name and a list of order_ids.")
            continue
        for oid in option["order_ids"]:
            if str(oid) not in seen:
                problems.append(f"Option {option['name']!r}: no check_coverage result in this run put {oid} at risk.")
    brief["part_id"] = part_id
    return problems


# --- The specialists --------------------------------------------------------------------------


class Specialists:
    def __init__(self, client, data_dir: Path, out_dir: Path, coordinator: str = "coordinator"):
        self.client = client
        self.data_dir = data_dir
        self.data = supply_data.load(data_dir)
        self.company = self.data.company
        self.out_dir = out_dir
        self.coordinator = coordinator
        self.usage = Usage()

    def customer_names(self) -> list[str]:
        return sorted({row["customer"] for row in self.data.orders}, key=len, reverse=True)

    def supplier_names(self) -> list[str]:
        names = {row["supplier"] for row in self.data.suppliers} | {row["contact_name"] for row in self.data.suppliers}
        names |= {row["supplier"] for row in self.data.parts.values()}
        return sorted(names, key=len, reverse=True)

    # --- 1. Supply analyst ---

    async def run_analyst(self, email: Email) -> dict | None:
        """An agent with the supply MCP tools. Returns its checked JSON brief, or None."""
        heading("Supply analyst")
        handoff(self.coordinator, "supply analyst", f"supplier email {email.path.name}", email.as_text())
        system = read_prompt(self.data_dir, "analyst")
        coverage_runs: list[dict] = []
        brief = None
        problems: list[str] = []
        server = StdioServerParameters(command=sys.executable, cwd=str(REPO_DIR),
                                       args=[str(SERVER_SCRIPT), "--org", str(self.data_dir)])
        with server_errlog() as errlog:
            async with stdio_client(server, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                             for t in (await session.list_tools()).tools]

                    async def run_tool(name: str, tool_input: dict) -> tuple[str, bool]:
                        try:
                            text, is_error = mcp_result_text(await session.call_tool(name, tool_input))
                        except Exception as error:  # the server crashed or timed out
                            return f"The tool call failed: {error}", True
                        if name == "check_coverage" and not is_error:
                            try:
                                coverage_runs.append(json.loads(text))
                            except json.JSONDecodeError:
                                pass
                        return text, is_error

                    messages = [{"role": "user", "content": (
                        f"Work out how this supplier email affects {self.company}'s customer orders. Today is "
                        "day 0; get_part shows when each purchase order is scheduled to arrive, in days "
                        "from today. A week is 7 days.\n\n" + email.as_text())}]
                    calls = 0
                    for attempt in (1, 2):   # one chance to fix a brief the code checks reject
                        result = await tool_loop(self.client, self.usage, "supply analyst", system, messages,
                                                 tools, run_tool, ANALYST_MAX_TOOL_CALLS, calls_so_far=calls)
                        calls = result.tool_calls
                        if not result.text:
                            problems = [result.stopped]
                            break
                        try:
                            candidate = extract_json(result.text)
                            problems = check_brief(candidate, self.data, coverage_runs)
                        except ValueError as error:
                            candidate, problems = None, [str(error)]
                        if not problems:
                            brief = candidate
                            break
                        print("\nCode checks rejected the brief:\n  - " + "\n  - ".join(problems))
                        if attempt == 1:
                            handoff("code checks", "supply analyst", "problems to fix", "\n".join(problems))
                            messages.append({"role": "user", "content": (
                                "Your brief has these problems:\n- " + "\n- ".join(problems) +
                                "\nFix them, using the tools if needed, and send the full corrected JSON brief.")})

        if brief is None:
            handoff("supply analyst", self.coordinator, "no usable brief", "\n".join(problems))
            return None
        handoff("supply analyst", self.coordinator, f"brief ({len(brief['orders_at_risk'])} order(s) at risk, "
                f"{calls} tool call(s))")
        print("\nANALYST'S BRIEF\n" + json.dumps(brief, indent=2))
        sys.stdout.flush()
        return brief

    # --- 2. Supplier writer ---

    def supplier_brief(self, brief: dict | None) -> dict:
        """The brief with every customer name removed, and order numbers too (they
        identify customers' orders). Units short and days late stay."""
        if brief is None:
            return {"note": "No supply analysis is available. State no impact numbers."}
        clean = copy.deepcopy(brief)
        clean["orders_at_risk"] = [
            {"units_short": r["units_short"], "ship_day": r["ship_day"], "ready_day": r["ready_day"],
             "days_late": None if r["ready_day"] is None else r["ready_day"] - r["ship_day"]}
            for r in brief["orders_at_risk"]]
        clean["options"] = [{"name": o["name"], "orders_at_risk": len(o["order_ids"])} for o in brief["options"]]
        ids = [row["order_id"] for row in self.data.orders]

        def scrub(value):
            if isinstance(value, str):
                for name in self.customer_names() + ids:
                    value = re.sub(re.escape(name), "[a customer]" if name not in ids else "[an order]",
                                   value, flags=re.I)
                return value
            if isinstance(value, list):
                return [scrub(v) for v in value]
            if isinstance(value, dict):
                return {k: scrub(v) for k, v in value.items()}
            return value

        return scrub(clean)

    async def write_supplier_reply(self, email: Email, brief: dict | None,
                                   revise: tuple[str, str] | None = None) -> str:
        """One model call. revise is (the previous draft, the reviewer's reasons)."""
        facts = self.supplier_brief(brief)
        content = (f"Draft {self.company}'s reply to this supplier email, using the supply analyst's brief.\n\n"
                   f"{email.as_text()}\n\n<brief>\n{json.dumps(facts, indent=2)}\n</brief>")
        if revise:
            content += f"\n\nYour previous draft:\n<draft>\n{revise[0]}\n</draft>\n\nA reviewer asked for changes:\n{revise[1]}\n\nWrite the full revised draft."
        return response_text(await ask(self.client, self.usage, "supplier writer",
                                       read_prompt(self.data_dir, "supplier-writer"),
                                       [{"role": "user", "content": content}], effort="low"))

    # --- 3. Customer writer ---

    def order_facts(self, brief: dict, order_id: str) -> dict:
        """The facts of one at-risk order, built by code from the brief and orders.csv."""
        entry = next((r for r in brief["orders_at_risk"] if r["order_id"] == order_id), None)
        if entry is None:
            raise AgentError(f"{order_id} is not in the brief's orders_at_risk.")
        row = self.data.order(order_id)
        return {
            "customer": row["customer"],
            "order_id": row["order_id"],
            "product": row["product"],
            "quantity": row["quantity"],
            "planned_ship_day": entry["ship_day"],
            "expected_ship_day": entry["ready_day"],
            "days_later_than_planned": None if entry["ready_day"] is None else entry["ready_day"] - entry["ship_day"],
            "days_are_counted_from": "today",
        }

    async def write_customer_notice(self, order: dict, revise: tuple[str, str] | None = None) -> str:
        """One model call, given only that one order's facts (from order_facts)."""
        content = f"Draft a notice to this customer about their order.\n\n<facts>\n{json.dumps(order, indent=2)}\n</facts>"
        if revise:
            content += f"\n\nYour previous draft:\n<draft>\n{revise[0]}\n</draft>\n\nA reviewer asked for changes:\n{revise[1]}\n\nWrite the full revised draft."
        return response_text(await ask(self.client, self.usage, "customer writer",
                                       read_prompt(self.data_dir, "customer-writer"),
                                       [{"role": "user", "content": content}], effort="low"))

    # --- 4. Reviewer ---

    async def review(self, draft: str, facts: dict, recipient: str, email: Email | None = None) -> tuple[bool, str]:
        """One model call. Returns (True, reasons) for APPROVE, (False, reasons) for REVISE.
        For a supplier draft, pass the supplier's email so deadlines it gave can be checked."""
        content = f"Recipient: a {recipient}.\n\n"
        if email is not None:
            content += f"The supplier's email:\n{email.as_text()}\n\n"
        content += f"<facts>\n{json.dumps(facts, indent=2)}\n</facts>\n\n<draft>\n{draft}\n</draft>"
        verdict = response_text(await ask(self.client, self.usage, "reviewer",
                                          read_prompt(self.data_dir, "reviewer"),
                                          [{"role": "user", "content": content}]))
        # The verdict is a line that is exactly APPROVE or REVISE. A reviewer can
        # change its mind part-way, in either direction ("APPROVE ... wait ...
        # REVISE" or "REVISE ... re-checking ... APPROVE"), so its LAST verdict
        # line is its conclusion. For REVISE, the reasons are the text after it,
        # or the whole answer if nothing follows.
        lines = verdict.strip().splitlines()
        marks = [(i, line.strip().strip("*#: ").upper()) for i, line in enumerate(lines)]
        marks = [(i, m) for i, m in marks if m in {"APPROVE", "REVISE"}]
        if not marks:
            return False, f"The reviewer gave no APPROVE or REVISE line. It said: {verdict.strip()[:500]}"
        last, conclusion = marks[-1]
        if conclusion == "APPROVE":
            return True, "\n".join(lines[last + 1:]).strip()
        reasons = "\n".join(lines[last + 1:]).strip() or "\n".join(lines[:last]).strip()
        return False, reasons or "(no reasons given)"

    # --- The review step ---

    async def _review_step(self, recipient: str, writer: str, write, facts: dict,
                           email: Email | None = None) -> Draft | None:
        handoff(self.coordinator, writer, "facts to write from", json.dumps(facts, indent=2))
        text = await write(None)
        reviews: list[str] = []
        approved = False
        for round_number in range(MAX_REVISIONS + 1):
            print(f"\n[{writer}, draft {round_number + 1}]\n{text}")
            if recipient == "supplier" and text.strip().upper().startswith(NO_REPLY):
                handoff(writer, self.coordinator, NO_REPLY)
                return None
            handoff(writer, "reviewer", f"draft {round_number + 1}")
            approved, reasons = await self.review(text, facts, recipient, email)
            reviews.append(f"Draft {round_number + 1}: {'APPROVE' if approved else 'REVISE'}" +
                           (f"\n{reasons}" if reasons else ""))
            handoff("reviewer", writer if not approved else self.coordinator,
                    "APPROVE" if approved else "REVISE", reasons)
            if approved or round_number == MAX_REVISIONS:
                break
            text = await write((text, reasons))

        names = self.customer_names() if recipient == "supplier" else self.supplier_names()
        kind = "customer" if recipient == "supplier" else "supplier"
        code_problems = [f"names the {kind} {n!r}" for n in names if n.lower() in text.lower()]
        print(f"\n[code check] {'passed' if not code_problems else '; '.join(code_problems)}")
        return Draft(recipient, "", "", text, approved, reviews, code_problems)

    @staticmethod
    def print_final(label: str, text: str, for_person: bool) -> None:
        print(f"\n{label}\n{text.strip()}\nMarked for a person: {'yes' if for_person else 'no'}")
        sys.stdout.flush()

    def _save(self, draft: Draft, filename: str, source: str, label: str) -> Draft:
        if not draft.approved:
            draft.person_reasons.append(f"The reviewer did not approve it after {MAX_REVISIONS} revisions.")
        draft.person_reasons += [f"Code check: it {p}." for p in draft.code_problems]
        self.out_dir.mkdir(parents=True, exist_ok=True)
        draft.path = self.out_dir / filename
        reasons = "\n".join(f"  - {r}" for r in draft.person_reasons) or "  (none)"
        reviews = "\n".join("  " + line for r in draft.reviews for line in r.splitlines())
        draft.path.write_text(
            "DRAFT - NOT SENT\n"
            f"To: {draft.to}\nSubject: {draft.subject}\n"
            f"For a person: {'YES' if draft.for_person else 'no'}\n{reasons}\n"
            f"Source email: {source}\nCoordinator: {self.coordinator}\n\nReviews:\n{reviews}\n"
            "\n-----\n" + draft.text.strip() + "\n",
            encoding="utf-8")
        print(f"\nSaved{' (FOR A PERSON)' if draft.for_person else ''}: {relative(draft.path)}")
        self.print_final(label, draft.text, draft.for_person)
        return draft

    async def prepare_supplier_reply(self, email: Email, brief: dict | None) -> Draft | None:
        """Write, review, check, and save the supplier reply. None if no reply is needed."""
        heading("Supplier reply")
        facts = self.supplier_brief(brief)
        draft = await self._review_step("supplier", "supplier writer",
                                        lambda revise: self.write_supplier_reply(email, brief, revise), facts, email)
        if draft is None:
            self.print_final("FINAL DRAFT TO SUPPLIER", NO_REPLY, False)
            return None
        draft.to, draft.subject = email.sender, email.reply_subject
        return self._save(draft, "supplier-reply.txt", email.path.name, "FINAL DRAFT TO SUPPLIER")

    async def prepare_customer_notice(self, brief: dict, order_id: str, source: str = "") -> Draft:
        """Write, review, check, and save the notice for one at-risk order."""
        heading(f"Customer notice: {order_id}")
        facts = self.order_facts(brief, order_id)
        draft = await self._review_step("customer", "customer writer",
                                        lambda revise: self.write_customer_notice(facts, revise), facts)
        row = self.data.order(order_id)
        draft.to, draft.subject = row["contact_email"], f"Update on your order {order_id}"
        if row["late_penalty"]:
            draft.person_reasons.append(f"{order_id} has a late penalty.")
        return self._save(draft, f"notice-{order_id}.txt", source, f"FINAL DRAFT TO CUSTOMER {order_id}")

    def usage_lines(self) -> list[str]:
        return self.usage.lines()


# --- What both coordinators report -----------------------------------------------------------


def brief_decisions(brief: dict) -> list[str]:
    """The decisions for a person that come from a checked brief: the analyst's own,
    the supplier's questions, and a choice between the supplier's options."""
    decisions = list(brief["decisions_for_a_person"])
    decisions += [f"Supplier asked: {q}" for q in brief["supplier_questions"]]
    for option in brief["options"]:
        ids = ", ".join(option["order_ids"]) or "no orders"
        decisions.append(f"Choose an option. {option['name']} puts at risk: {ids}.")
    return decisions


SCHEDULE_WORDS = re.compile(
    r"\d|\b(scheduled|later|earlier|after|before|weeks?|days?|months?|date|today|tomorrow"
    r"|ship\w*|arriv\w*|deliver\w*|delay\w*|follow\w*)\b", re.I)
MAX_SCHEDULE_SENTENCES = 8


def schedule_sentences(body: str) -> list[str]:
    """The supplier's own sentences that give a quantity or a time, found by a
    text search (no model), so a person can check the analyst's reading of them."""
    out = []
    for paragraph in re.split(r"\n\s*\n", body):
        text = " ".join(paragraph.split())
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if sentence.endswith((".", "!", "?", ":")) and SCHEDULE_WORDS.search(sentence):
                out.append(sentence)
    return out[:MAX_SCHEDULE_SENTENCES]


def reading_check_lines(email: Email, brief: dict | None) -> list[str]:
    """The supplier's words beside the analyst's reading of them."""
    lines = ["Check the analyst's reading against the supplier's own words:", "  Supplier wrote:"]
    sentences = schedule_sentences(email.body)
    lines += [f'    "{s}"' for s in sentences] or ["    (no sentences with quantities or dates)"]
    reading = brief["what_changed"] if brief is not None else "none (no checked brief)"
    lines.append(f"  Analyst's reading: {reading}")
    return lines


def print_summary(team: Specialists, email: Email, out_dir: Path, drafts: list[Draft],
                  decisions: list[str], skipped: list[str], brief: dict | None,
                  extra: list[str] | None = None, save: bool = True) -> str:
    """Print the end-of-run summary and, if save, write it to <out_dir>/summary.txt.
    extra is lines that one coordinator adds after its name."""
    heading("Summary")
    lines = [f"Email: {email.path.name} ({email.subject})", f"Coordinator: {team.coordinator}"]
    lines += (extra or []) + [""] + reading_check_lines(email, brief) + [""]
    lines.append(f"Drafts saved ({len(drafts)}), in {relative(out_dir)}/:" if drafts else "Drafts saved: none")
    for d in drafts:
        lines.append(f"  - {d.path.name}  to {d.to}  "
                     f"({'FOR A PERSON' if d.for_person else 'reviewer approved'})")
    marked = [d for d in drafts if d.for_person]
    lines += ["", f"Drafts marked for a person ({len(marked)}):" if marked else "Drafts marked for a person: none"]
    for d in marked:
        lines += [f"  - {d.path.name}: {reason}" for reason in d.person_reasons]
    lines += ["", f"Decisions for a person ({len(decisions)}):" if decisions else "Decisions for a person: none"]
    lines += [f"  - {d}" for d in decisions]
    if skipped:
        lines += ["", "Skipped:"] + [f"  - {s}" for s in skipped]
    lines += ["", "Tokens:"] + [f"  {u}" for u in team.usage_lines()]
    text = "\n".join(lines)
    print(text)
    if save:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "summary.txt").write_text(text + "\n", encoding="utf-8")
    return text


# --- Command line: the supply analyst alone ----------------------------------------------


async def _analyst_only(client, data_dir: Path, email: Email) -> bool:
    team = Specialists(client, data_dir, out_dir=data_dir, coordinator="specialists.py")
    print(f"Company: {team.company} ({relative(data_dir)})")
    print("Columns found: " + "; ".join(team.data.column_lines()))
    brief = await team.run_analyst(email)
    print("\nTokens:\n" + "\n".join(f"  {u}" for u in team.usage_lines()))
    return brief is not None


if __name__ == "__main__":
    from common import run_cli
    run_cli("Run the supply analyst alone on one supplier email and print its brief.", _analyst_only)
