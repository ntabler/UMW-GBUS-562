## What I Built

I built a team of four specialists that prepare a company's response to a supplier email about a late or partially late shipment. Each of the four specialists performs its own job or function. The first specialist is a supply analyst. It uses read-only tools from my MCP server to check stock and open orders, and returns a JSON brief that will be checked by code. The next specialist is a supplier writer that will draft a message to the supplier about the shipment updates. The third specialist is a customer writer who will draft a message to the customer. And the final specialist is a reviewer who will look at the drafts for both the customer and supplier to ensure the draft is accurate.

The four specialists have two coordinator methods. One is pipeline.py which runs the specialists in a fixed order. Another is orchestrator.py which lets Claude choose which specialists to call, up to 10 tool calls. To make it work for multiple organizations, I added an --org option so the server, the specialists, and both coordinators read the data and prompts from any company folder. This allows me to switch to either Copperline Coffee Machines or Brookfield on demand. Copperline's files name some columns differently (units instead of qty, ship_by_day instead of ship_in_days), so the code now works out from each header row which columns hold the product, quantity, parts per unit, and ship day.

I ran all four Copperline emails through both coordinators, for eight saved runs. On Email 1, both drafted notices for exactly the three orders at risk and marked the two with late penalties for a person. On Email 2, neither reply chose an option. Emails 3 and 4 drafted no customer notices. The biggest difference was Email 4: the pipeline checked the data and wrote a reply, while the orchestrator decided nothing was needed and only called finish.

## Why This Approach

Task A: Agent, because it has to decide what to look up based on what the café says, such as the order, the warranty terms, or both. Based on its findings, it can draft an email.

Task B: Team, because there are multiple jobs for multiple audiences. One task is to find the machine the failed parts shipped with, another task is to draft an email for each affected customer and the final task is to draft a report for the safety regulator. A reviewer can be used to ensure accuracy with the emails.

Task C: Pipeline, because when the workflow is predictable and doesn't require multiple decisions to be made along the way, a pipeline is much easier and cheaper in tokens to conduct the work required.

Task D: Orchestrator, because the workflow requires various decisions to be made along the way. The inbox will receive various requests that require different tasks depending on the request. An orchestrator can choose specialists for an email depending on the content of the email.

## Example Run

```
Company: Copperline Coffee (copperline-coffee)
Email:   week-05/copperline-coffee/supplier-emails/01-danforth-pump-delay.eml
From:    Diane Kessler <d.kessler@danforthpump.example>
Subject: PO-5512 - PU-20 pumps - partial shipment

--- Supply analyst ----------------------------------------------------

>>> HANDOFF  pipeline -> supply analyst: supplier email 01-danforth-pump-delay.eml
    | <email>
    | From: Diane Kessler <d.kessler@danforthpump.example>
    | Subject: PO-5512 - PU-20 pumps - partial shipment
    | 
    | Hello,
    | 
    | I'm writing about PO-5512 for 200 PU-20 vibration pumps. A test rig on our
    | assembly line failed inspection, and we can't release the full order on the
    | scheduled date.
    | 
    | We can deliver 40 of the 200 pumps on the scheduled date. The other 160 will
    | follow 15 days later.
    | 
    | We're sorry for the disruption. To help us plan, could you send us a list of
    | the customer orders these pumps are for, with each customer's name and
    | quantity? We'll do our best to keep the delay as short as we can.
    | 
    | Diane Kessler
    | Key Accounts, Danforth Pump Works
    | </email>
[supply server] copperline-coffee: 4 parts, 4 purchase orders, 10 open orders
[supply server] columns in orders.csv: product = product, quantity = units, ship_day = ship_by_day
[supply server] columns in bill-of-materials.csv: product = product, per_unit = per_unit
  (supply analyst: 1957 input, 106 output tokens)

[supply analyst step 1] get_part
  inputs: {"part_id": "PU-20"}
  result: { "part_id": "PU-20", "description": "Vibration pump", "on_hand": 50, "supplier": "Danforth Pump Works", "products_using": [ { "product": "Cafe One", "per_unit": 1 }, { "product": "Cafe Duo", "per_unit": 2 } ], "scheduled_shipments": [ { "po_number": "PO-5512", "part_id": "PU-20", "supplier": "Danfo...

[supply analyst step 2] get_orders_for_part
  inputs: {"part_id": "PU-20"}
  result: { "part_id": "PU-20", "orders": [ { "order_id": "O-2201", "customer": "Morning Ritual Cafe", "product": "Cafe One", "quantity": 20, "units_needed": 20, "ship_day": 6, "late_penalty": false }, { "order_id": "O-2202", "customer": "Harbor Hotel Group", "product": "Cafe Duo", "quantity": 15, "units_need...
  (supply analyst: 3022 input, 120 output tokens)

[supply analyst step 3] check_coverage
  inputs: {"part_id": "PU-20", "arrivals": [{"qty": 40, "arrives_in_days": 4}, {"qty": 160, "arrives_in_days": 19}]}
  result: { "part_id": "PU-20", "on_hand": 50, "shipments": [ { "qty": 40, "arrives_in_days": 4 }, { "qty": 160, "arrives_in_days": 19 } ], "orders": [ { "order_id": "O-2201", "customer": "Morning Ritual Cafe", "product": "Cafe One", "quantity": 20, "units_needed": 20, "ship_day": 6, "late_penalty": false, "c...
  (supply analyst: 4578 input, 547 output tokens)

>>> HANDOFF  supply analyst -> pipeline: brief (3 order(s) at risk, 3 tool call(s))

ANALYST'S BRIEF
{
  "part_id": "PU-20",
  "what_changed": "PO-5512 will now arrive as 40 pumps on day 4 (as originally scheduled) and the remaining 160 pumps on day 19 (15 days later), instead of all 200 on day 4.",
  "orders_at_risk": [
    {
      "order_id": "O-2203",
      "customer": "Elm State University Dining",
      "units_short": 10,
      "ship_day": 12,
      "ready_day": 19,
      "late_penalty": true
    },
    {
      "order_id": "O-2204",
      "customer": "Kettle & Crumb Bakery",
      "units_short": 20,
      "ship_day": 15,
      "ready_day": 19,
      "late_penalty": false
    },
    {
      "order_id": "O-2205",
      "customer": "Tall Pines Coffee House",
      "units_short": 25,
      "ship_day": 17,
      "ready_day": 19,
      "late_penalty": true
    }
  ],
  "options": [],
  "decisions_for_a_person": [
    "Decide whether to accept the split shipment (40 now, 160 on day 19) or seek expediting/alternate sourcing to protect O-2203 and O-2205, which carry late penalties.",
    "Decide whether/how to respond to Danforth's request for the list of customer orders/quantities tied to this PO, given customer confidentiality considerations."
  ],
  "supplier_questions": [
    "Danforth asked for a list of the customer orders these pumps are for, with each customer's name and quantity."
  ]
}

--- Customer notice: O-2203 -------------------------------------------

>>> HANDOFF  pipeline -> customer writer: facts to write from
    | {
    |   "customer": "Elm State University Dining",
    |   "order_id": "O-2203",
    |   "product": "Cafe One",
    |   "quantity": 50,
    |   "planned_ship_day": 12,
    |   "expected_ship_day": 19,
    |   "days_later_than_planned": 7,
    |   "days_are_counted_from": "today"
    | }
  (customer writer: 360 input, 182 output tokens)

[customer writer, draft 1]
Subject: Update on Your Order O-2203

Dear Elm State University Dining,

We're writing to update you on order O-2203 for 50 units of the Cafe One.

This order is now expected to ship 7 days later than originally planned, due to a delay in a part from one of our suppliers.

We apologize for this delay and any inconvenience it causes your team.

We will keep you informed if there are any further updates regarding your order. Thank you for your patience and for choosing Copperline Coffee Machines.

Customer Service, Copperline Coffee Machines

>>> HANDOFF  customer writer -> reviewer: draft 1
  (reviewer: 640 input, 124 output tokens)

>>> HANDOFF  reviewer -> customer writer: REVISE
    | 1. None — numbers match facts.
    | 4. The draft references "a delay in a part from one of our suppliers," which mentions a supplier (even if unnamed, it discloses supplier involvement). Remove this phrase; simply state the order is delayed without attributing cause to a supplier. Change to something like: "This order is now expected to ship 7 days later than originally planned."
  (customer writer: 699 input, 165 output tokens)

[customer writer, draft 2]
Subject: Update on Your Order O-2203

Dear Elm State University Dining,

We're writing to update you on order O-2203 for 50 units of the Cafe One.

This order is now expected to ship 7 days later than originally planned.

We apologize for this delay and any inconvenience it causes your team.

We will keep you informed if there are any further updates regarding your order. Thank you for your patience and for choosing Copperline Coffee Machines.

Customer Service, Copperline Coffee Machines

>>> HANDOFF  customer writer -> reviewer: draft 2
  (reviewer: 623 input, 7 output tokens)

>>> HANDOFF  reviewer -> pipeline: APPROVE

[code check] passed

Saved (FOR A PERSON): week-05/drafts/copperline-coffee/01-danforth-pump-delay-pipeline-20261123-200314/notice-O-2203.txt

FINAL DRAFT TO CUSTOMER O-2203
Subject: Update on Your Order O-2203

Dear Elm State University Dining,

We're writing to update you on order O-2203 for 50 units of the Cafe One.

This order is now expected to ship 7 days later than originally planned.

We apologize for this delay and any inconvenience it causes your team.

We will keep you informed if there are any further updates regarding your order. Thank you for your patience and for choosing Copperline Coffee Machines.

Customer Service, Copperline Coffee Machines
Marked for a person: yes

--- Customer notice: O-2204 -------------------------------------------

>>> HANDOFF  pipeline -> customer writer: facts to write from
    | {
    |   "customer": "Kettle & Crumb Bakery",
    |   "order_id": "O-2204",
    |   "product": "Cafe Duo",
    |   "quantity": 10,
    |   "planned_ship_day": 15,
    |   "expected_ship_day": 19,
    |   "days_later_than_planned": 4,
    |   "days_are_counted_from": "today"
    | }
  (customer writer: 367 input, 157 output tokens)

[customer writer, draft 1]
Subject: Update on Your Order O-2204

Dear Kettle & Crumb Bakery,

We're writing to update you on order O-2204 for 10 Cafe Duo units. This order is now expected to ship 4 days later than planned, from today.

We apologize for the delay. It is due to a delay in a part from one of our suppliers.

We appreciate your patience and will keep you informed if anything else changes with your order.

Customer Service, Copperline Coffee Machines

>>> HANDOFF  customer writer -> reviewer: draft 1
  (reviewer: 622 input, 192 output tokens)

>>> HANDOFF  reviewer -> pipeline: APPROVE

[code check] passed

Saved: week-05/drafts/copperline-coffee/01-danforth-pump-delay-pipeline-20261123-200314/notice-O-2204.txt

FINAL DRAFT TO CUSTOMER O-2204
Subject: Update on Your Order O-2204

Dear Kettle & Crumb Bakery,

We're writing to update you on order O-2204 for 10 Cafe Duo units. This order is now expected to ship 4 days later than planned, from today.

We apologize for the delay. It is due to a delay in a part from one of our suppliers.

We appreciate your patience and will keep you informed if anything else changes with your order.

Customer Service, Copperline Coffee Machines
Marked for a person: no

--- Customer notice: O-2205 -------------------------------------------

>>> HANDOFF  pipeline -> customer writer: facts to write from
    | {
    |   "customer": "Tall Pines Coffee House",
    |   "order_id": "O-2205",
    |   "product": "Cafe One",
    |   "quantity": 25,
    |   "planned_ship_day": 17,
    |   "expected_ship_day": 19,
    |   "days_later_than_planned": 2,
    |   "days_are_counted_from": "today"
    | }
  (customer writer: 363 input, 157 output tokens)

[customer writer, draft 1]
Subject: Update on Your Order O-2205

Dear Tall Pines Coffee House,

We want to let you know that your order O-2205 for 25 units of the Cafe One is now expected to ship 2 days later than planned, on day 19 instead of day 17.

We apologize for this delay. It is due to a delay in a part from one of our suppliers.

We appreciate your patience and will keep you informed if anything changes.

Customer Service, Copperline Coffee Machines

>>> HANDOFF  customer writer -> reviewer: draft 1
  (reviewer: 618 input, 176 output tokens)

>>> HANDOFF  reviewer -> pipeline: APPROVE

[code check] passed

Saved (FOR A PERSON): week-05/drafts/copperline-coffee/01-danforth-pump-delay-pipeline-20261123-200314/notice-O-2205.txt

FINAL DRAFT TO CUSTOMER O-2205
Subject: Update on Your Order O-2205

Dear Tall Pines Coffee House,

We want to let you know that your order O-2205 for 25 units of the Cafe One is now expected to ship 2 days later than planned, on day 19 instead of day 17.

We apologize for this delay. It is due to a delay in a part from one of our suppliers.

We appreciate your patience and will keep you informed if anything changes.

Customer Service, Copperline Coffee Machines
Marked for a person: yes

--- Supplier reply ----------------------------------------------------

>>> HANDOFF  pipeline -> supplier writer: facts to write from
    | {
    |   "part_id": "PU-20",
    |   "what_changed": "PO-5512 will now arrive as 40 pumps on day 4 (as originally scheduled) and the remaining 160 pumps on day 19 (15 days later), instead of all 200 on day 4.",
    |   "orders_at_risk": [
    |     {
    |       "units_short": 10,
    |       "ship_day": 12,
    |       "ready_day": 19,
    |       "days_late": 7
    |     },
    |     {
    |       "units_short": 20,
    |       "ship_day": 15,
    |       "ready_day": 19,
    |       "days_late": 4
    |     },
    |     {
    |       "units_short": 25,
    |       "ship_day": 17,
    |       "ready_day": 19,
    |       "days_late": 2
    |     }
    |   ],
    |   "options": [],
    |   "decisions_for_a_person": [
    |     "Decide whether to accept the split shipment (40 now, 160 on day 19) or seek expediting/alternate sourcing to protect [an order] and [an order], which carry late penalties.",
    |     "Decide whether/how to respond to Danforth's request for the list of customer orders/quantities tied to this PO, given customer confidentiality considerations."
    |   ],
    |   "supplier_questions": [
    |     "Danforth asked for a list of the customer orders these pumps are for, with each customer's name and quantity."
    |   ]
    | }
  (supplier writer: 1025 input, 326 output tokens)

[supplier writer, draft 1]
Subject: RE: PO-5512 - PU-20 pumps - partial shipment

Hello Diane,

Thank you for the update. To confirm our understanding: PO-5512 will now arrive as 40 pumps on day 4, with the remaining 160 pumps on day 19, instead of all 200 on the original date.

This split will impact three of our downstream orders, leaving us short by 10, 20, and 25 units respectively, with delays of 7, 4, and 2 days against their original ship dates. We're not able to share customer names or order details, but we wanted you to understand the scale of the impact.

Given this, we'd ask that you look at options to reduce the gap — in particular, whether any portion of the 160 pumps could be shipped sooner than day 19, even in smaller partial batches. That would help us reduce the delay on our end.

If you need a decision from us on anything related to this change, we'll confirm by the time you require.

Best regards,
Purchasing, Copperline Coffee Machines

>>> HANDOFF  supplier writer -> reviewer: draft 1
  (reviewer: 1381 input, 121 output tokens)

>>> HANDOFF  reviewer -> supplier writer: REVISE
    | 1. The draft mentions specific downstream order impacts (10/20/25 units short, 7/4/2 days late) — this describes customer orders' details (even without names) to the supplier, which violates rule 3 ("describes no customer's order"). Remove the breakdown of units short and days late per order; keep only a general statement that the delay impacts downstream orders.
  (supplier writer: 1505 input, 281 output tokens)

[supplier writer, draft 2]
Subject: RE: PO-5512 - PU-20 pumps - partial shipment

Hello Diane,

Thank you for the update. To confirm our understanding: PO-5512 will now arrive as 40 pumps on day 4, with the remaining 160 pumps on day 19, instead of all 200 on the original date.

This split will impact downstream orders on our end, resulting in both unit shortfalls and delays. We're not able to share customer names or order details.

Given this, we'd ask that you look at options to reduce the gap — in particular, whether any portion of the 160 pumps could be shipped sooner than day 19, even in smaller partial batches. That would help us reduce the delay on our end.

If you need a decision from us on anything related to this change, we'll confirm by the time you require.

Best regards,
Purchasing, Copperline Coffee Machines

>>> HANDOFF  supplier writer -> reviewer: draft 2
  (reviewer: 1336 input, 7 output tokens)

>>> HANDOFF  reviewer -> pipeline: APPROVE

[code check] passed

Saved: week-05/drafts/copperline-coffee/01-danforth-pump-delay-pipeline-20261123-200314/supplier-reply.txt

FINAL DRAFT TO SUPPLIER
Subject: RE: PO-5512 - PU-20 pumps - partial shipment

Hello Diane,

Thank you for the update. To confirm our understanding: PO-5512 will now arrive as 40 pumps on day 4, with the remaining 160 pumps on day 19, instead of all 200 on the original date.

This split will impact downstream orders on our end, resulting in both unit shortfalls and delays. We're not able to share customer names or order details.

Given this, we'd ask that you look at options to reduce the gap — in particular, whether any portion of the 160 pumps could be shipped sooner than day 19, even in smaller partial batches. That would help us reduce the delay on our end.

If you need a decision from us on anything related to this change, we'll confirm by the time you require.

Best regards,
Purchasing, Copperline Coffee Machines
Marked for a person: no

--- Summary -----------------------------------------------------------
Email: 01-danforth-pump-delay.eml (PO-5512 - PU-20 pumps - partial shipment)
Coordinator: pipeline

Check the analyst's reading against the supplier's own words:
  Supplier wrote:
    "I'm writing about PO-5512 for 200 PU-20 vibration pumps."
    "A test rig on our assembly line failed inspection, and we can't release the full order on the scheduled date."
    "We can deliver 40 of the 200 pumps on the scheduled date."
    "The other 160 will follow 15 days later."
    "We'll do our best to keep the delay as short as we can."
  Analyst's reading: PO-5512 will now arrive as 40 pumps on day 4 (as originally scheduled) and the remaining 160 pumps on day 19 (15 days later), instead of all 200 on day 4.

Drafts saved (4), in week-05/drafts/copperline-coffee/01-danforth-pump-delay-pipeline-20261123-200314/:
  - supplier-reply.txt  to d.kessler@danforthpump.example  (reviewer approved)
  - notice-O-2203.txt  to dining@elmstate.example  (FOR A PERSON)
  - notice-O-2204.txt  to owner@kettleandcrumb.example  (reviewer approved)
  - notice-O-2205.txt  to hello@tallpinescoffee.example  (FOR A PERSON)

Drafts marked for a person (2):
  - notice-O-2203.txt: O-2203 has a late penalty.
  - notice-O-2205.txt: O-2205 has a late penalty.

Decisions for a person (3):
  - Decide whether to accept the split shipment (40 now, 160 on day 19) or seek expediting/alternate sourcing to protect O-2203 and O-2205, which carry late penalties.
  - Decide whether/how to respond to Danforth's request for the list of customer orders/quantities tied to this PO, given customer confidentiality considerations.
  - Supplier asked: Danforth asked for a list of the customer orders these pumps are for, with each customer's name and quantity.

Tokens:
  supply analyst: 3 call(s), 9557 input, 773 output tokens
  customer writer: 4 call(s), 1789 input, 661 output tokens
  reviewer: 6 call(s), 5220 input, 627 output tokens
  supplier writer: 2 call(s), 2530 input, 607 output tokens
  Total: 19096 input, 2668 output tokens
```

## Reflection

Email 1 reading: Matches, the brief stated that the first 40 pumps would arrive on day 4 as scheduled and the other 160 on day 19, which is 15 days after the scheduled day 4, as Diane wrote.

Email 1 customer names: No, the response included the phrase "We're not able to share customer names or order details."

Email 4 orchestrator: None, it made one tool call, finish, using 1 of its 10 allowed calls, and nothing else ran. The pipeline ran the supply analyst, skipped the customer writer because no orders were at risk, and ran the supplier writer, saving one draft reply to the supplier.
