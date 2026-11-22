"""Tests for supply_data.py and supply_mcp_server.py. No API calls.

    .venv/bin/python -m unittest week-05/test_supply.py -v      (from the top of the repository)
"""

from __future__ import annotations

import asyncio
import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import supply_data  # noqa: E402
from supply_data import DataError  # noqa: E402

SERVER = Path(__file__).resolve().parent / "supply_mcp_server.py"


def at_risk(result: dict) -> list[tuple]:
    return [(o["order_id"], o["units_short"], o["ship_day"], o["ready_day"]) for o in result["orders_at_risk"]]


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.data = supply_data.load()

    def test_current_schedule_covers_everything(self):
        for part in ["MC-500", "BP-48", "TR-29"]:
            self.assertEqual(at_risk(self.data.coverage(part)), [], part)

    def test_northgate_split_shipment(self):
        # 120 on the scheduled day 5, the other 280 three weeks later (day 26).
        result = self.data.coverage("MC-500", [{"qty": 120, "arrives_in_days": 5}, {"qty": 280, "arrives_in_days": 26}])
        self.assertEqual(at_risk(result), [("O-1043", 10, 14, 26), ("O-1044", 30, 18, 26), ("O-1045", 50, 21, 26)])
        self.assertEqual([o["days_late"] for o in result["orders_at_risk"]], [12, 8, 5])

    def test_kendall_options(self):
        option1 = self.data.coverage("TR-29", [{"qty": 160, "arrives_in_days": 8}, {"qty": 240, "arrives_in_days": 22}])
        option2 = self.data.coverage("TR-29", [{"qty": 400, "arrives_in_days": 12}])
        self.assertEqual(at_risk(option1), [])
        self.assertEqual(at_risk(option2), [("O-1046", 40, 9, 12)])

    def test_units_needed_uses_parts_per_bike(self):
        orders = {o["order_id"]: o for o in self.data.orders_for_part("TR-29")}
        self.assertEqual(orders["O-1046"]["units_needed"], 160)   # 80 bikes x 2 tires
        self.assertEqual([o["ship_day"] for o in self.data.orders_for_part("MC-500")], sorted(
            o["ship_day"] for o in self.data.orders_for_part("MC-500")))

    def test_short_order_holds_up_later_ones(self):
        # 40 on hand. O-1041 needs 40 on day 7, O-1042 needs 30 on day 10. If the
        # first 40 arrive only on day 12, O-1041 can't ship until day 12, and
        # O-1042 waits behind it even though 30 more arrived on day 9.
        result = self.data.coverage("MC-500", [{"qty": 30, "arrives_in_days": 9}, {"qty": 400, "arrives_in_days": 30}])
        by_id = {o["order_id"]: o for o in result["orders"]}
        self.assertTrue(by_id["O-1041"]["covered"])            # the 40 on hand
        self.assertTrue(by_id["O-1042"]["covered"])            # 30 arrive day 9
        self.assertFalse(by_id["O-1043"]["covered"])           # needs 100 by day 14
        # Nothing more arrives until day 30, so every later order waits for it.
        for oid in ["O-1043", "O-1044", "O-1045", "O-1048"]:
            self.assertEqual(by_id[oid]["ready_day"], 30, oid)
        ready = [o["ready_day"] for o in result["orders"]]
        self.assertEqual(ready, sorted(ready))                 # never goes down along the queue

    def test_never_covered(self):
        result = self.data.coverage("BP-48", [])
        self.assertTrue(all(o["ready_day"] is None for o in result["orders_at_risk"]))
        # 35 on hand cover O-1042 (30); O-1044 is then 25 short of its 30.
        self.assertEqual(at_risk(result)[0], ("O-1044", 25, 18, None))

    def test_bad_input(self):
        with self.assertRaises(DataError):
            self.data.coverage("XX-1")
        with self.assertRaises(DataError):
            self.data.coverage("MC-500", [{"qty": -5, "arrives_in_days": 3}])
        with self.assertRaises(DataError):
            self.data.coverage("MC-500", [{"qty": 5, "arrives_in_days": "soon"}])

    def test_bad_file_fails_to_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "org"
            shutil.copytree(supply_data.DEFAULT_DATA_DIR, folder)
            with (folder / "parts.csv").open("w", newline="") as f:
                csv.writer(f).writerows([["part_id", "on_hand"], ["MC-500", "40"]])
            with self.assertRaises(DataError):
                supply_data.load(folder)


class ServerTests(unittest.TestCase):
    def test_tools_over_stdio(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def run():
            params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = sorted(t.name for t in (await session.list_tools()).tools)
                    part = await session.call_tool("get_part", {"part_id": "tr-29"})
                    cover = await session.call_tool("check_coverage", {
                        "part_id": "TR-29", "arrivals": [{"qty": 400, "arrives_in_days": 12}]})
                    bad = await session.call_tool("get_orders_for_part", {"part_id": "XX-1"})
                    return names, part, cover, bad

        names, part, cover, bad = asyncio.run(run())
        self.assertEqual(names, ["check_coverage", "get_orders_for_part", "get_part"])
        part_json = json.loads(part.content[0].text)
        self.assertEqual(part_json["models_using"], [{"model": "Trail 29", "qty_per_bike": 2}])
        self.assertEqual(part_json["scheduled_shipments"][0]["po_number"], "PO-7795")
        self.assertEqual(at_risk(json.loads(cover.content[0].text)), [("O-1046", 40, 9, 12)])
        self.assertTrue(bad.is_error)


if __name__ == "__main__":
    unittest.main()
