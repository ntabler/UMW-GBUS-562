# Week 5: a team of agents for supplier emails (any company)

When a supplier emails about a late or split shipment, a team of specialists
works out which customer orders are at risk and drafts the replies. Two
coordinators run the same specialists, so they can be compared:

- **`pipeline.py`** runs the specialists in a fixed order.
- **`orchestrator.py`** lets Claude decide which specialists to use.

Drafts are only saved to files. Nothing can be sent.

## Company folders: `--org FOLDER`

`supply_mcp_server.py`, `specialists.py`, `pipeline.py`, and `orchestrator.py`
all take `--org FOLDER`. Two fictional companies are included: Brookfield
Bicycle Company (`brookfield-bikes/`, the default) and Copperline Coffee
Machines (`copperline-coffee/`). A company folder holds:

| Path | What it is |
| --- | --- |
| `parts.csv`, `inbound.csv`, `orders.csv`, `bill-of-materials.csv`, `suppliers.csv` | The supply data. |
| `prompts/` | The system prompts: `analyst`, `supplier-writer`, `customer-writer`, `reviewer`, `orchestrator` (`.txt`). |
| `supplier-emails/` | Example `.eml` emails to try. |

Four kinds of column can have any reasonable name. They are worked out from the
header rows (`supply_data.find_columns`), and the server prints what it found:

| Holds | In | Brookfield | Copperline |
| --- | --- | --- | --- |
| Product | `orders.csv`, `bill-of-materials.csv` | `model` | `product` |
| Quantity | `orders.csv` | `qty` | `units` |
| Ship day | `orders.csv` | `ship_in_days` | `ship_by_day` |
| Parts per unit | `bill-of-materials.csv` | `qty_per_bike` | `per_unit` |

If no column, or more than one, looks like one of these, the program stops and
lists the file's columns. The other columns keep fixed names, which both
companies share. Each company's drafts go in `week-05/drafts/<company folder>/`.

## The specialists: `specialists.py`

| Specialist | What it does |
| --- | --- |
| Supply analyst | An agent with the supply MCP tools (below). It returns a JSON brief that code checks: the part, what changed, orders at risk, the supplier's options, decisions for a person, and the supplier's questions. Order numbers and day counts must come from a `check_coverage` result in the same run. |
| Supplier writer | Drafts the reply to the supplier. Customer names and order numbers are removed from the brief before it sees it. |
| Customer writer | Drafts a notice for one at-risk order, given only that order's facts. |
| Reviewer | Answers APPROVE or REVISE for a draft. Its last verdict line decides. |

**The review step:** each draft is written, reviewed, and revised up to 2 times.
Then code checks that no customer name is in a supplier draft and no supplier
name is in a customer notice. A draft is marked for a person if the reviewer
never approved it, the code check fails, or the order has a late penalty.

All specialists use `claude-sonnet-5`. Their system prompts are in the company
folder's `prompts/` and are read on every call.

The analyst's brief is printed in full. After each review step, the final draft
is printed once: a `FINAL DRAFT TO SUPPLIER` line (or `FINAL DRAFT TO CUSTOMER`
and the order ID), the draft, then `Marked for a person: yes` or `no`.

`specialists.py` run on its own runs just the supply analyst:

```
.venv/bin/python week-05/specialists.py --org week-05/copperline-coffee --email week-05/copperline-coffee/supplier-emails/01-danforth-pump-delay.eml
```

## The coordinators

From the top of the repository:

```
.venv/bin/python week-05/pipeline.py --email week-05/brookfield-bikes/supplier-emails/01-northgate-controller-delay.eml --save week-05/runs/01-pipeline.txt
.venv/bin/python week-05/orchestrator.py --email week-05/brookfield-bikes/supplier-emails/01-northgate-controller-delay.eml --save week-05/runs/01-orchestrator.txt
```

| Option | What it does |
| --- | --- |
| `--email EML` | The supplier email to respond to (required). |
| `--save FILE` | Also write everything printed to this file. |
| `--org FOLDER` | The company folder (default `week-05/brookfield-bikes`). |

**Pipeline:** 1. the supply analyst. 2. A customer notice for each order at
risk, skipped if there are none or while the supplier's options are undecided
(a person must choose first). 3. The supplier reply.

**Orchestrator:** a `claude-sonnet-5` agent with the prompt in
`prompts/orchestrator.txt` and four tools: `run_supply_analyst`,
`draft_supplier_reply`, `draft_customer_notice` (one order ID), and `finish` (a
summary and whether a person must decide). It stops after 10 tool calls and
prints each choice. Code still enforces the pipeline's rules: notices only for
orders in a checked brief, none while options are undecided, and each
specialist at most once per email (once per order for notices).

Both print every handoff and the same end-of-run summary: the supplier's own
sentences about quantities and dates beside the analyst's reading of them
(`what_changed`), drafts saved, drafts marked for a person, decisions for a
person, what was skipped, and tokens by specialist. The sentences are found by
a text search, not a model, so a misread such as "three weeks later" taken as
three weeks from today is easy to spot before any draft goes out. Drafts and `summary.txt` go to
`week-05/drafts/<company folder>/<email>-<coordinator>-<time>/`.

## Supply MCP server: `supply_mcp_server.py`

Read-only tools over the CSV files in the `--org` company folder. Days are counted
from today (day 0).

| Tool | What it does |
| --- | --- |
| `get_part(part_id)` | Stock on hand, scheduled shipments, and the bike models that use the part. |
| `get_orders_for_part(part_id)` | Open orders that need the part, earliest ship day first. |
| `check_coverage(part_id, arrivals)` | Which orders are covered if the part's shipments are replaced by these arrivals. |

```
claude mcp add brookfield-supply -- "$PWD/.venv/bin/python" "$PWD/week-05/supply_mcp_server.py"
```

## Files

- `brookfield-bikes/`, `copperline-coffee/`: the two company folders (CSV files, prompts, and supplier emails).
- `common.py`: API key, email reading, the tool loop, printing, and the shared command line.
- `supply_data.py`: reading the CSV files, working out their column names, and the coverage math.
- `runs/`: saved runs of both coordinators.
- `drafts/`: the drafts from the latest run of each email, one folder per company.
- `test_supply.py`, `test_specialists.py`, `test_orchestrator.py`: tests with no API calls.

## Setup and tests

```
.venv/bin/pip install -r week-05/requirements.txt
.venv/bin/python -m unittest week-05/test_supply.py week-05/test_specialists.py week-05/test_orchestrator.py
```

The API key is read from `ANTHROPIC_API_KEY`, or from `API-key.txt` in the
folder above the repository. It is never stored in the repository.
