# Week 4: HR MCP server and agent (any organization)

An MCP server that gives AI apps two tools for answering HR emails, and an agent
that uses them. It works for any organization whose files are laid out in one
folder (below). Two fictional ones are included: Hartwell Foods
(`hartwell-hr/`, the default) and Ridgeline Hotels (`ridgeline-hotels/`).

## Organization folders: `--org FOLDER`

Both `hr_mcp_server.py` and `agent.py` take `--org FOLDER`. The folder holds:

| Path | What it is |
| --- | --- |
| `hr-docs/` | The HR policy documents (HTML) that `search_policy` searches. |
| `employees.csv` | One row per employee, read by `get_leave_balance`. |
| `system_prompt.txt` | The agent's instructions, read on every run. |
| `sample-emails/` | Example `.eml` emails to try (optional). |

Without `--org`, `hartwell-hr/` is used. Each organization gets its own Chroma
database under `chroma_db/<folder name>-<id>/`, so switching organizations never
mixes up their documents. All of `chroma_db/` is ignored by Git.

The employee file's column names are not fixed. They are worked out from the
header row (see `org.py`), and the server prints what it found when it starts:

- **Email:** the column whose name mentions "email" or "e-mail" (one that also
  says "work" wins if there are several).
- **Name:** "Name", "Full Name", "Employee Name", and similar, or "First Name"
  and "Last Name" joined.
- **Balances:** every column whose name says "left", "remaining", "balance", or
  "available", such as `vacation_days_left` (Hartwell) or `PTO Days Remaining`
  (Ridgeline). Their values must be numbers.

`get_leave_balance` returns the email, the name, the record's other columns
under their own names, and `leave_balances` with each balance column.

| Tool | What it does |
| --- | --- |
| `search_policy(query)` | Returns the 4 passages from the organization's `hr-docs/` closest in meaning to the query, each with its source file, document version, section, and similarity score. |
| `get_leave_balance` | Returns an employee's leave balances from the organization's `employees.csv`. If no record matches, it returns an error telling the model not to state a balance. |

`get_leave_balance` has two modes:

- **HR staff mode** (default): takes any employee's work email.
- **Employee mode** (`--as-employee ADDRESS`): takes no inputs and can return
  only that employee's balance, so a model replying to that employee cannot look
  anyone else up.

## Files

- `hr_mcp_server.py`: the server (official `mcp` Python package, stdio).
- `hr_search.py`: document reading, chunking, and Chroma search, adapted from `week-03/ask.py`.
- `org.py`: the organization folder layout and the header-based reading of `employees.csv`.
- `requirements.txt`: packages to install into the repository's `.venv`.
- `chroma_db/`: one search database per organization, built automatically and ignored by Git.

## Setup

From the top of the repository:

```
.venv/bin/pip install -r week-04/requirements.txt
.venv/bin/python week-04/hr_mcp_server.py --rebuild    # optional: build the database now
claude mcp add hartwell-hr -- "$PWD/.venv/bin/python" "$PWD/week-04/hr_mcp_server.py"
```

The database is built on first start and rebuilt automatically when the documents
change. Run `--rebuild` to force a fresh build.

For employee mode, add `--as-employee` and an address after the script path. For
another organization, add `--org` and its folder, for example
`--org "$PWD/week-04/ridgeline-hotels"`.

## Agent: `agent.py`

A command-line agent that drafts a reply to one employee email using Claude
(`claude-sonnet-5`) and tool use. For each email it reads the sender's address,
starts `hr_mcp_server.py --org <folder> --as-employee <sender>`, and offers Claude that server's
tools plus one of its own:

- `save_reply(reply_text, needs_human_review)`: writes a draft to `week-04/drafts/`.
  It only saves. Nothing is ever sent.

The system prompt is read from the organization's `system_prompt.txt` on every run. The
loop stops after 10 tool calls, once the draft is saved, or on any reply that isn't
a tool request, and prints why. It prints each tool call (inputs and the first 300
characters of the result), the draft, and the total input and output tokens.

The API key comes from `ANTHROPIC_API_KEY`, or else from `API-key.txt` in the
folder above the repository. It is never printed or saved.

Each run prints, in order: the sender and subject; each tool step; then a line
containing only `DRAFT`, the reply text, and a line `Needs human review: true`
or `Needs human review: false`. If no draft was saved, there is no `DRAFT` line
and the review line says `true`.

```
.venv/bin/python week-04/agent.py --email week-04/hartwell-hr/sample-emails/03-megan-time-off.eml
.venv/bin/python week-04/agent.py --org week-04/ridgeline-hotels \
    --email week-04/ridgeline-hotels/sample-emails/01-jenna-pto.eml --save jenna-run.txt
```

### Inbox mode: `--inbox`

`--inbox` signs in to the Gmail sandbox with IMAP, using the same
`Agent-Email.txt` login as `load_inbox.py`. It then handles each unread inbox
email, one at a time, as its own separate agent run:

- Each email is marked as read **before** the agent works on it, so no email is
  handled twice. An email that gets no draft stays read, and the summary at the
  end lists it; mark it unread in Gmail to try again.
- Only emails from addresses in the organization's `employees.csv` are handled. Any
  other email (for example, Google's own alerts) is skipped and left unread.
- `save_reply` creates a Gmail draft (in the Drafts folder) replying to the
  sender, with `In-Reply-To` and `References` headers so it joins the sender's
  thread. If `needs_human_review` is true, the subject starts with
  `[NEEDS HR REVIEW]`, and both the employee's email and the draft get the Gmail
  label **Needs HR Review**. The label appears in Gmail's left sidebar, so HR
  can open it without changing any Gmail settings.
- The agent only reads email and creates drafts. It has no way to send email.

With `--email`, `save_reply` still writes a file to `week-04/drafts/`.

`--save FILE` (with `--email` or `--inbox`) also writes everything printed,
including the MCP server's messages, to a UTF-8 text file.
Log files named `*-run.txt` are ignored by Git, because they contain employee data.

```
.venv/bin/python week-04/load_inbox.py                   # put the sample emails in the inbox
.venv/bin/python week-04/agent.py --inbox --save inbox-run.txt
```

## Inbox loader: `load_inbox.py`

Places each `.eml` file from `hartwell-hr/sample-emails/` into a Gmail sandbox
inbox as an unread message dated now. It uses IMAP `APPEND`, so nothing is sent.

The login (`EMAIL_ADDRESS=` and `APP_PASSWORD=` lines) is read at run time from
`Agent-Email.txt` in the folder above the repository. The password is never
printed or saved. Gmail needs IMAP turned on and an app password.

```
.venv/bin/python week-04/load_inbox.py --dry-run   # list the emails, don't sign in
.venv/bin/python week-04/load_inbox.py             # load them
```

Each run adds a fresh copy of every email, so running it twice gives duplicates.
