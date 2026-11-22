"""Brookfield's supply data: reading the CSV files and working out part coverage.

No AI here. Everything the supply analyst reports about stock, shipments, and
which orders are short comes from these functions, through the MCP server in
supply_mcp_server.py.

Days are counted from today (day 0). A shipment that arrives on day N can be
used for an order that ships on day N.

How coverage is worked out for one part:
  - Supply starts at the stock on hand and grows as each shipment arrives.
  - The open orders that use the part are taken in order of ship day, earliest
    first (ties by order number). Each one needs its bike quantity times the
    part's qty_per_bike from the bill of materials.
  - Orders are filled complete, one at a time, in that order: no order takes
    parts before every order ahead of it is filled, so a short order holds up
    the ones after it. That is the same as saying an order is covered if, on its
    ship day, the parts that have arrived cover its own units_needed plus those
    of every order ahead of it.
  - For an order that is not covered, units_short is how much of its own need
    is missing on its ship day, and ready_day is the first day it can ship: the
    first day enough has arrived for it and every order ahead of it (None if
    the arrivals never cover it). ready_day never goes down along the queue.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

WEEK_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = WEEK_DIR / "brookfield-bikes"

# Each file and the columns it must have.
FILES = {
    "parts.csv": ["part_id", "description", "on_hand", "supplier"],
    "inbound.csv": ["po_number", "part_id", "supplier", "qty", "arrives_in_days"],
    "orders.csv": ["order_id", "customer", "contact_email", "model", "qty", "ship_in_days", "late_penalty"],
    "bill-of-materials.csv": ["model", "part_id", "qty_per_bike"],
    "suppliers.csv": ["supplier", "contact_name", "contact_email", "part_id"],
}


class DataError(Exception):
    """A data file is missing or malformed, or a tool was given bad input."""


@dataclass
class SupplyData:
    folder: Path
    parts: dict[str, dict]            # part_id -> row
    inbound: list[dict]               # one row per purchase order
    orders: list[dict]                # one row per open customer order
    bom: list[dict]                   # model, part_id, qty_per_bike
    suppliers: list[dict]             # one row per supplier contact

    # --- Lookups ---------------------------------------------------------------

    def part(self, part_id: str) -> dict:
        key = part_id.strip().upper()
        if key not in self.parts:
            known = ", ".join(sorted(self.parts))
            raise DataError(f"There is no part {part_id!r}. Known parts: {known}.")
        return self.parts[key]

    def order(self, order_id: str) -> dict:
        for row in self.orders:
            if row["order_id"].upper() == order_id.strip().upper():
                return row
        raise DataError(f"There is no order {order_id!r}.")

    def shipments_for(self, part_id: str) -> list[dict]:
        part_id = self.part(part_id)["part_id"]
        return [row for row in self.inbound if row["part_id"] == part_id]

    def supplier_for_address(self, address: str) -> dict | None:
        for row in self.suppliers:
            if row["contact_email"].lower() == address.strip().lower():
                return row
        return None

    def orders_for_part(self, part_id: str) -> list[dict]:
        """The open orders that use the part, earliest ship day first."""
        part_id = self.part(part_id)["part_id"]
        per_bike = {row["model"]: row["qty_per_bike"] for row in self.bom if row["part_id"] == part_id}
        found = []
        for row in self.orders:
            if row["model"] in per_bike:
                found.append({
                    "order_id": row["order_id"],
                    "customer": row["customer"],
                    "model": row["model"],
                    "bikes": row["qty"],
                    "units_needed": row["qty"] * per_bike[row["model"]],
                    "ship_day": row["ship_in_days"],
                    "late_penalty": row["late_penalty"],
                })
        found.sort(key=lambda o: (o["ship_day"], o["order_id"]))
        return found

    # --- Coverage -------------------------------------------------------------

    def models_using(self, part_id: str) -> list[dict]:
        part_id = self.part(part_id)["part_id"]
        return [{"model": r["model"], "qty_per_bike": r["qty_per_bike"]} for r in self.bom if r["part_id"] == part_id]

    def coverage(self, part_id: str, arrivals: list[dict] | None = None) -> dict:
        """Coverage of every open order for the part.

        arrivals (each {"qty", "arrives_in_days"}) replaces ALL of the part's
        scheduled shipments. Without it, the scheduled shipments are used.
        """
        part = self.part(part_id)
        part_id = part["part_id"]
        if arrivals is None:
            arrivals = [{"qty": s["qty"], "arrives_in_days": s["arrives_in_days"]}
                        for s in self.shipments_for(part_id)]
        else:
            checked = []
            for i, s in enumerate(arrivals):
                qty, day = s.get("qty"), s.get("arrives_in_days")
                if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0:
                    raise DataError(f"Arrival {i + 1}: qty must be a whole number, 0 or more.")
                if not isinstance(day, int) or isinstance(day, bool) or day < 0:
                    raise DataError(f"Arrival {i + 1}: arrives_in_days must be a whole number of days from today, 0 or more.")
                checked.append({"qty": qty, "arrives_in_days": day})
            arrivals = checked
        arrivals = sorted(arrivals, key=lambda a: a["arrives_in_days"])

        def supply_on(day: int) -> int:
            return part["on_hand"] + sum(a["qty"] for a in arrivals if a["arrives_in_days"] <= day)

        def first_day_with(amount: int) -> int | None:
            if part["on_hand"] >= amount:
                return 0
            for a in arrivals:
                if supply_on(a["arrives_in_days"]) >= amount:
                    return a["arrives_in_days"]
            return None

        results = []
        needed_so_far = 0
        for order in self.orders_for_part(part_id):
            needed_so_far += order["units_needed"]
            available = supply_on(order["ship_day"])
            short = min(order["units_needed"], max(0, needed_so_far - available))
            ready = order["ship_day"] if short == 0 else first_day_with(needed_so_far)
            results.append({
                **order,
                "covered": short == 0,
                "units_short": short,
                "ready_day": ready,
                "days_late": None if ready is None else ready - order["ship_day"],
            })

        return {
            "part_id": part_id,
            "on_hand": part["on_hand"],
            "shipments": arrivals,
            "orders": results,
            "orders_at_risk": [r for r in results if not r["covered"]],
        }


# --- Reading the files -----------------------------------------------------------


def _read(folder: Path, name: str) -> list[dict]:
    path = folder / name
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = [c for c in FILES[name] if c not in (reader.fieldnames or [])]
            if missing:
                raise DataError(f"{path} is missing the column(s): {', '.join(missing)}.")
            return [{k: (v or "").strip() for k, v in row.items()} for row in reader if any(row.values())]
    except OSError as error:
        raise DataError(f"Could not read {path}: {error}") from error


def _whole(value: str, where: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise DataError(f"{where}: {value!r} is not a whole number.") from None
    if number < 0:
        raise DataError(f"{where}: {value!r} is negative.")
    return number


def _yes_no(value: str, where: str) -> bool:
    if value.lower() not in {"yes", "no"}:
        raise DataError(f"{where}: late_penalty must be yes or no, not {value!r}.")
    return value.lower() == "yes"


def load(folder: str | Path | None = None) -> SupplyData:
    folder = Path(folder) if folder else DEFAULT_DATA_DIR
    if not folder.is_dir():
        raise DataError(f"{folder} is not a folder.")

    parts = {}
    for row in _read(folder, "parts.csv"):
        row["part_id"] = row["part_id"].upper()
        row["on_hand"] = _whole(row["on_hand"], f"parts.csv {row['part_id']} on_hand")
        parts[row["part_id"]] = row

    inbound = _read(folder, "inbound.csv")
    for row in inbound:
        row["part_id"] = row["part_id"].upper()
        row["qty"] = _whole(row["qty"], f"inbound.csv {row['po_number']} qty")
        row["arrives_in_days"] = _whole(row["arrives_in_days"], f"inbound.csv {row['po_number']} arrives_in_days")

    orders = _read(folder, "orders.csv")
    for row in orders:
        where = f"orders.csv {row['order_id']}"
        row["qty"] = _whole(row["qty"], f"{where} qty")
        row["ship_in_days"] = _whole(row["ship_in_days"], f"{where} ship_in_days")
        row["late_penalty"] = _yes_no(row["late_penalty"], where)

    bom = _read(folder, "bill-of-materials.csv")
    for row in bom:
        row["part_id"] = row["part_id"].upper()
        row["qty_per_bike"] = _whole(row["qty_per_bike"], f"bill-of-materials.csv {row['model']} {row['part_id']}")

    suppliers = _read(folder, "suppliers.csv")
    for row in suppliers:
        row["part_id"] = row["part_id"].upper()

    for row in inbound + bom + suppliers:
        if row["part_id"] not in parts:
            raise DataError(f"Part {row['part_id']} is used in the data but is not in parts.csv.")

    return SupplyData(folder, parts, inbound, orders, bom, suppliers)
