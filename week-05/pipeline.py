#!/usr/bin/env python3
"""Coordinator 1: a fixed pipeline that prepares Brookfield's response to one supplier email.

From the top of the repository:

    .venv/bin/python week-05/pipeline.py \
        --email week-05/brookfield-bikes/supplier-emails/01-northgate-controller-delay.eml
    .venv/bin/python week-05/pipeline.py --email <file.eml> --save northgate-pipeline-run.txt

The steps always run in this order (the specialists are in specialists.py):
  1. The supply analyst works out which orders are at risk (MCP tools, JSON brief).
  2. A customer notice for each order at risk. Skipped if there are none, and
     also while the supplier's options are undecided: a person must choose an
     option first, so the orders each option affects are listed as a decision.
  3. A reply to the supplier (the writer may answer NO REPLY NEEDED).
Every draft goes through the review step. Drafts are saved to
week-05/drafts/<email>-pipeline-<time>/ and never sent.

At the end it prints a summary: drafts saved, drafts marked for a person,
decisions for a person, and total tokens; summary.txt in the same folder has
the same text. --save FILE also writes everything printed to FILE (UTF-8).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from common import DRAFTS_DIR, Email, handoff, run_cli
from specialists import Specialists, brief_decisions, print_summary


async def run_pipeline(client, data_dir: Path, email: Email) -> bool:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = DRAFTS_DIR / f"{email.path.stem}-pipeline-{stamp}"
    team = Specialists(client, data_dir, out_dir, coordinator="pipeline")
    print(f"Email:   {email.path}\nFrom:    {email.message.get('From', email.sender)}\nSubject: {email.subject}")

    drafts = []
    decisions: list[str] = []
    skipped: list[str] = []

    # 1. Supply analyst
    brief = await team.run_analyst(email)
    if brief is None:
        decisions.append("The supply analyst produced no usable brief: work out the impact of this email by hand.")
        skipped.append("Customer notices and the supplier reply: there is no checked brief to write from.")
    else:
        decisions += brief_decisions(brief)

        # 2. A customer notice for each order at risk
        at_risk = [r["order_id"] for r in brief["orders_at_risk"]]
        if brief["options"]:
            skipped.append("Customer notices: the supplier offered options, so no notice is drafted "
                           "until a person chooses one.")
            handoff("pipeline", "customer writer", "skipped: options are undecided")
        elif not at_risk:
            skipped.append("Customer notices: no orders at risk.")
            handoff("pipeline", "customer writer", "skipped: no orders at risk")
        else:
            for order_id in at_risk:
                drafts.append(await team.prepare_customer_notice(brief, order_id, email.path.name))

        # 3. A reply to the supplier
        reply = await team.prepare_supplier_reply(email, brief)
        if reply is None:
            skipped.append("Supplier reply: the writer found that no reply is needed.")
        else:
            drafts.insert(0, reply)

    print_summary(team, email, out_dir, drafts, decisions, skipped,
                  save=bool(drafts) or brief is not None)
    return brief is not None


if __name__ == "__main__":
    run_cli("Prepare Brookfield's response to one supplier email with a fixed pipeline of specialists.",
            run_pipeline)
