#!/usr/bin/env python3
"""An MCP server with three supply tools for one company's data folder.

AI apps start it and talk to it over stdio. From the top of the repository:

    .venv/bin/python week-05/supply_mcp_server.py
    .venv/bin/python week-05/supply_mcp_server.py --org week-05/copperline-coffee
    claude mcp add brookfield-supply -- "$PWD/.venv/bin/python" "$PWD/week-05/supply_mcp_server.py"

--org FOLDER names the folder holding parts.csv, inbound.csv, orders.csv,
bill-of-materials.csv, and suppliers.csv. Without it, week-05/brookfield-bikes
is used. The files are read again on every tool call, so edits show up at once.
Which columns hold the product, quantity, parts per unit, and ship day is
worked out from the header rows (supply_data.find_columns) and printed at start.

Tools (all read-only; the coverage math is in supply_data.py):
  get_part(part_id)                 stock on hand, scheduled shipments, products that use it
  get_orders_for_part(part_id)      open orders that need the part, earliest ship day first
  check_coverage(part_id, arrivals) which orders are covered if the part's scheduled
                                    shipments are replaced by these arrivals

Days are counted from today (day 0).
Nothing may be printed to stdout: in stdio mode it carries the MCP messages.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

import supply_data
from supply_data import DataError

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


class Arrival(BaseModel):
    qty: int = Field(description="How many units arrive.")
    arrives_in_days: int = Field(description="When they arrive, in whole days from today (today is day 0).")


def log(text: str) -> None:
    print(f"[supply server] {text}", file=sys.stderr, flush=True)


def build_server(folder: Path, company: str) -> MCPServer:
    server = MCPServer(
        name=f"{folder.name}-supply",
        instructions=(
            f"Supply data for {company}. Days are counted from today (day 0). "
            "Report only numbers these tools return; never work out coverage yourself."
        ),
    )

    def data() -> supply_data.SupplyData:
        try:
            return supply_data.load(folder)
        except DataError as error:
            raise ToolError(f"The supply data could not be read: {error}") from error

    @server.tool(annotations=READ_ONLY)
    def get_part(part_id: str) -> dict:
        """Look up one part: its description, stock on hand, supplier, the
        products that use it (with parts per unit), and its scheduled shipments
        (purchase order, quantity, and arrival day in days from today)."""
        d = data()
        try:
            part = d.part(part_id)
            return {
                **part,
                "products_using": d.products_using(part_id),
                "scheduled_shipments": d.shipments_for(part_id),
                "today_is_day": 0,
            }
        except DataError as error:
            raise ToolError(str(error)) from error

    @server.tool(annotations=READ_ONLY)
    def get_orders_for_part(part_id: str) -> dict:
        """List the open customer orders that need this part, earliest ship day
        first: order number, customer, product, quantity, units_needed (quantity
        times parts per unit), ship_day (days from today), and late_penalty."""
        d = data()
        try:
            return {"part_id": d.part(part_id)["part_id"], "orders": d.orders_for_part(part_id)}
        except DataError as error:
            raise ToolError(str(error)) from error

    @server.tool(annotations=READ_ONLY)
    def check_coverage(part_id: str, arrivals: list[Arrival]) -> dict:
        """Work out which open orders for a part are covered if its arrivals
        change.

        arrivals REPLACES ALL of the part's scheduled shipments: each item is
        {qty, arrives_in_days}, in whole days from today. To change one purchase
        order, include the part's other scheduled shipments (from get_part)
        unchanged. To check the current schedule, pass the scheduled shipments.

        Orders are filled complete, in ship-day order, so a short order holds up
        the ones after it. For each order it returns covered and, if not
        covered, units_short (units missing on its ship day) and ready_day (the
        first day it can ship, or null if the arrivals never cover it), plus
        days_late. orders_at_risk lists the orders that are not covered.
        """
        d = data()
        try:
            return d.coverage(part_id, [a.model_dump() for a in arrivals])
        except DataError as error:
            raise ToolError(str(error)) from error

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Supply MCP server for one company folder (stdio).")
    parser.add_argument("--org", metavar="FOLDER",
                        help="company folder with the five CSV files (default: week-05/brookfield-bikes)")
    args = parser.parse_args()

    folder = Path(args.org) if args.org else supply_data.DEFAULT_DATA_DIR
    try:
        d = supply_data.load(folder)  # fail at start-up if the files are bad
    except DataError as error:
        sys.exit(f"Error: {error}")
    log(f"{folder.name}: {len(d.parts)} parts, {len(d.inbound)} purchase orders, {len(d.orders)} open orders")
    for line in d.column_lines():
        log(f"columns in {line}")
    build_server(folder, d.company).run("stdio")


if __name__ == "__main__":
    main()
