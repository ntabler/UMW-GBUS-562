# Week 4: Hartwell Foods HR MCP server

An MCP server that gives AI apps two tools for answering HR emails at Hartwell
Foods, a fictional company. Claude Code uses it first; a custom agent will use it
later.

| Tool | What it does |
| --- | --- |
| `search_policy(query)` | Returns the 4 passages from `hartwell-hr/hr-docs` closest in meaning to the query, each with its source file, document version, section, and similarity score. |
| `get_leave_balance` | Returns an employee's vacation, sick, and personal days left from `hartwell-hr/employees.csv`. If no record matches, it returns an error telling the model not to state a balance. |

`get_leave_balance` has two modes:

- **HR staff mode** (default): takes any employee's work email.
- **Employee mode** (`--as-employee ADDRESS`): takes no inputs and can return
  only that employee's balance, so a model replying to that employee cannot look
  anyone else up.

## Files

- `hr_mcp_server.py`: the server (official `mcp` Python package, stdio).
- `hr_search.py`: document reading, chunking, and Chroma search, adapted from `week-03/ask.py`.
- `requirements.txt`: packages to install into the repository's `.venv`.
- `chroma_db/`: the search database, built automatically and ignored by Git.

## Setup

From the top of the repository:

```
.venv/bin/pip install -r week-04/requirements.txt
.venv/bin/python week-04/hr_mcp_server.py --rebuild    # optional: build the database now
claude mcp add hartwell-hr -- "$PWD/.venv/bin/python" "$PWD/week-04/hr_mcp_server.py"
```

The database is built on first start and rebuilt automatically when the documents
change. Run `--rebuild` to force a fresh build.

For employee mode, add `--as-employee` and an address after the script path.

## Agent: `agent.py`

A command-line agent that drafts a reply to one employee email using Claude
(`claude-sonnet-5`) and tool use. For each email it reads the sender's address,
starts `hr_mcp_server.py --as-employee <sender>`, and offers Claude that server's
tools plus one of its own:

- `save_reply(reply_text, needs_human_review)`: writes a draft to `week-04/drafts/`.
  It only saves. Nothing is ever sent.

The system prompt is read from `hartwell-hr/system_prompt.txt` on every run. The
loop stops after 10 tool calls, once the draft is saved, or on any reply that isn't
a tool request, and prints why. It prints each tool call (inputs and the first 300
characters of the result), the draft, and the total input and output tokens.

The API key comes from `ANTHROPIC_API_KEY`, or else from `API-key.txt` in the
folder above the repository. It is never printed or saved.

```
.venv/bin/python week-04/agent.py --email week-04/hartwell-hr/sample-emails/03-megan-time-off.eml
```

### Inbox mode: `--inbox`

`--inbox` signs in to the Gmail sandbox with IMAP, using the same
`Agent-Email.txt` login as `load_inbox.py`. It then handles each unread inbox
email, one at a time, as its own separate agent run:

- Each email is marked as read **before** the agent works on it, so no email is
  handled twice. An email that gets no draft stays read, and the summary at the
  end lists it; mark it unread in Gmail to try again.
- Only emails from addresses in `hartwell-hr/employees.csv` are handled. Any
  other email (for example, Google's own alerts) is skipped and left unread.
- `save_reply` creates a Gmail draft (in the Drafts folder) replying to the
  sender, with `In-Reply-To` and `References` headers so it joins the sender's
  thread. If `needs_human_review` is true, the subject starts with
  `[NEEDS HR REVIEW]`, and both the employee's email and the draft get the Gmail
  label **Needs HR Review**. The label appears in Gmail's left sidebar, so HR
  can open it without changing any Gmail settings.
- The agent only reads email and creates drafts. It has no way to send email.

With `--email`, `save_reply` still writes a file to `week-04/drafts/`.

`--save FILE` (either mode) also writes everything printed to a UTF-8 text file.
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
