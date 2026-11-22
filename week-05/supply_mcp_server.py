#!/usr/bin/env python3
"""An MCP server with Brookfield Bicycle Company's three supply tools.

AI apps start it and talk to it over stdio. From the top of the repository:

    .venv/bin/python week-05/supply_mcp_server.py
    .venv/bin/python week-05/supply_mcp_server.py --org week-05/brookfield-bikes
    claude mcp add brookfield-supply -- "$PWD/.venv/bin/python" "$PWD/week-05/supply_mcp_server.py"

--org FOLDER names the folder holding parts.csv, inbound.csv, orders.csv,
bill-of-materials.csv, and suppliers.csv. Without it, week-05/brookfield-bikes
is used. The files are read again on every tool call, so edits show up at once.

Tools (all read-only; the coverage math is in supply_data.py):
  get_part(part_id)                 stock on hand, scheduled shipments, models that use it
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


def build_server(folder: Path) -> MCPServer:
    server = MCPServer(
        name="brookfield-supply",
        instructions=(
            "Supply data for Brookfield Bicycle Company. Days are counted from today (day 0). "
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
        """Look up one part: its description, stock on hand, supplier, the bike
        models that use it (with parts per bike), and its scheduled shipments
        (purchase order, quantity, and arrival day in days from today)."""
        d = data()
        try:
            part = d.part(part_id)
            return {
                **part,
                "models_using": d.models_using(part_id),
                "scheduled_shipments": d.shipments_for(part_id),
                "today_is_day": 0,
            }
        except DataError as error:
            raise ToolError(str(error)) from error

    @server.tool(annotations=READ_ONLY)
    def get_orders_for_part(part_id: str) -> dict:
        """List the open customer orders that need this part, earliest ship day
        first: order number, customer, model, bikes, units_needed (bikes times
        parts per bike), ship_day (days from today), and late_penalty."""
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
    parser = argparse.ArgumentParser(description="Brookfield supply MCP server (stdio).")
    parser.add_argument("--org", metavar="FOLDER", help="data folder (default: week-05/brookfield-bikes)")
    args = parser.parse_args()

    folder = Path(args.org) if args.org else supply_data.DEFAULT_DATA_DIR
    try:
        d = supply_data.load(folder)  # fail at start-up if the files are bad
    except DataError as error:
        sys.exit(f"Error: {error}")
    log(f"{folder.name}: {len(d.parts)} parts, {len(d.inbound)} purchase orders, {len(d.orders)} open orders")
    build_server(folder).run("stdio")


if __name__ == "__main__":
    main()
