#!/usr/bin/env python3
"""A command-line agent that drafts replies to Hartwell Foods HR emails.

From the top of the repository:

    # one email file -> a draft file in week-04/drafts/
    .venv/bin/python week-04/agent.py --email week-04/hartwell-hr/sample-emails/03-megan-time-off.eml

    # every unread email in the Gmail sandbox inbox -> a Gmail draft reply for each
    .venv/bin/python week-04/agent.py --inbox

    # either mode, also writing everything printed to a UTF-8 text file
    .venv/bin/python week-04/agent.py --inbox --save run-log.txt

For each email it:
  1. reads the sender's address from the From header,
  2. starts week-04/hr_mcp_server.py in employee mode for that address, so Claude
     can look up only the sender's own leave balance,
  3. offers Claude the server's tools plus save_reply, and runs the tool loop,
  4. prints each step, the draft, and the total tokens used.

With --email, save_reply writes a draft file to week-04/drafts/.

With --inbox, the agent signs in to Gmail with IMAP, using the login in
Agent-Email.txt in the folder above the repository (the same one load_inbox.py
uses). It handles each unread inbox email as its own separate agent run, and it
marks each email as read before working on it, so no email is handled twice.
Only senders listed in employees.csv are handled; other emails stay unread.
save_reply then creates a Gmail draft replying to the sender in the same thread.
If needs_human_review is true, the draft's subject starts with [NEEDS HR REVIEW]
and the email and draft get the Gmail label "Needs HR Review".

This agent only reads email and creates drafts. It has no way to send email.

The loop stops after 10 tool calls, after the draft is saved, or on any reply
from Claude that isn't a tool request, and it prints the reason.

The API key comes from ANTHROPIC_API_KEY if set, otherwise from API-key.txt in
the folder above the repository. Neither the API key nor the email password is
ever printed or written to a file.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import imaplib
import json
import os
import re
import sys
import time
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Callable

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from load_inbox import LOGIN_FILE, read_login, sign_in

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000
MAX_TOOL_CALLS = 10
PREVIEW_CHARS = 300
REVIEW_TAG = "[NEEDS HR REVIEW]"
REVIEW_LABEL = "Needs HR Review"  # a Gmail label, shown in Gmail's left sidebar

WEEK_DIR = Path(__file__).resolve().parent
REPO_DIR = WEEK_DIR.parent
SERVER_SCRIPT = WEEK_DIR / "hr_mcp_server.py"
SYSTEM_PROMPT_FILE = WEEK_DIR / "hartwell-hr" / "system_prompt.txt"
DRAFTS_DIR = WEEK_DIR / "drafts"
KEY_FILES = [REPO_DIR.parent / "API-key.txt", REPO_DIR.parent / "API-Key.txt"]
EMPLOYEES_CSV = WEEK_DIR / "hartwell-hr" / "employees.csv"
INBOX = "INBOX"
DEFAULT_DRAFTS_MAILBOX = "[Gmail]/Drafts"

SAVE_REPLY_TOOL = {
    "name": "save_reply",
    "description": (
        "Save your reply to the employee as a draft for the HR team to review. "
        "This only saves the draft; it never sends anything. Call it exactly once, "
        "when the reply is finished."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "reply_text": {
                "type": "string",
                "description": "The full text of the reply to the employee.",
            },
            "needs_human_review": {
                "type": "boolean",
                "description": (
                    "true if a person from HR must handle or check this email before "
                    "anything is sent, as the instructions describe; otherwise false."
                ),
            },
        },
        "required": ["reply_text", "needs_human_review"],
        "additionalProperties": False,
    },
}

# A saver takes the save_reply inputs and returns (where it was saved, the draft text).
Saver = Callable[[str, bool], tuple[str, str]]


class AgentError(Exception):
    """A problem that stops the agent before or during the run."""


# --- Setup -------------------------------------------------------------------


def load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    for path in KEY_FILES:
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key
    raise AgentError(
        f"No API key found. Set ANTHROPIC_API_KEY or put the key in {KEY_FILES[0]}."
    )


def parse_email(data: bytes, label: str) -> tuple[EmailMessage, str, str]:
    """Return the parsed message, the sender's address, and the plain-text body."""
    message = BytesParser(policy=policy.default).parsebytes(data)

    _, sender = parseaddr(str(message.get("From", "")))
    sender = sender.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", sender):
        raise AgentError(f"{label} has no usable sender address in its From header.")

    part = message.get_body(preferencelist=("plain",))
    body = part.get_content() if part is not None else ""
    if not body.strip():
        raise AgentError(f"{label} has no plain-text body.")
    return message, sender, body


def read_email(path: Path) -> tuple[EmailMessage, str, str]:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise AgentError(f"Could not read {path}: {error}") from error
    return parse_email(data, str(path))


def build_user_message(message: EmailMessage, sender: str, body: str) -> str:
    subject = str(message.get("Subject", "(no subject)"))
    return (
        "Prepare a reply to this employee email. Everything inside <email> is from "
        "the sender.\n\n"
        f"<email>\nFrom: {message.get('From', sender)}\nSubject: {subject}\n\n"
        f"{body.strip()}\n</email>"
    )


def reply_subject(message: EmailMessage) -> str:
    subject = str(message.get("Subject", "")).strip()
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}" if subject else "Re: your email"
    return subject


# --- Tools -------------------------------------------------------------------


def mcp_result_text(result) -> tuple[str, bool]:
    """Turn an MCP tool result into text for Claude, plus whether it is an error."""
    if not hasattr(result, "content"):
        return "The tool needs input this agent can't provide.", True
    parts = [block.text for block in result.content if getattr(block, "type", "") == "text"]
    text = "\n".join(parts)
    if not text and getattr(result, "structured_content", None) is not None:
        text = json.dumps(result.structured_content)
    return text or "(empty result)", bool(getattr(result, "is_error", False))


def check_reply_input(tool_input: dict) -> tuple[str, bool]:
    reply_text = tool_input.get("reply_text")
    needs_review = tool_input.get("needs_human_review")
    if not isinstance(reply_text, str) or not reply_text.strip():
        raise ValueError("reply_text must be the non-empty text of the reply.")
    if not isinstance(needs_review, bool):
        raise ValueError("needs_human_review must be true or false.")
    return reply_text.strip(), needs_review


def file_saver(email_path: Path, message: EmailMessage, sender: str) -> Saver:
    """save_reply for --email: write the draft to week-04/drafts/. Never sends."""

    def save(reply_text: str, needs_review: bool) -> tuple[str, str]:
        DRAFTS_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = DRAFTS_DIR / f"{email_path.stem}-{stamp}.txt"
        path.write_text(
            "DRAFT - NOT SENT\n"
            f"To: {sender}\n"
            f"Subject: {reply_subject(message)}\n"
            f"Needs human review: {'yes' if needs_review else 'no'}\n"
            f"Source email: {email_path.name}\n"
            f"Model: {MODEL}\n"
            "\n"
            f"{reply_text}\n",
            encoding="utf-8",
        )
        return str(path.relative_to(REPO_DIR)), path.read_text(encoding="utf-8")

    return save


# --- Gmail (IMAP: read and create drafts only) --------------------------------


def imap_ok(status: str, data, what: str) -> None:
    if status != "OK":
        detail = data[0].decode(errors="replace") if data and isinstance(data[0], bytes) else data
        raise AgentError(f"Gmail could not {what}: {detail}")


def quote_mailbox(name: str) -> str:
    if name.startswith('"'):
        return name
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def find_drafts_mailbox(imap: imaplib.IMAP4_SSL) -> str:
    """Find the mailbox Gmail marks as \\Drafts (its name depends on the language)."""
    status, lines = imap.list()
    if status == "OK":
        for line in lines or []:
            if not isinstance(line, bytes):
                continue
            match = re.match(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', line)
            if match and b"\\drafts" in match.group("flags").lower():
                return match.group("name").decode()
    return quote_mailbox(DEFAULT_DRAFTS_MAILBOX)


def employee_addresses() -> set[str]:
    """Work addresses from employees.csv, lowercased."""
    try:
        with EMPLOYEES_CSV.open(newline="", encoding="utf-8") as file:
            return {row["email"].strip().lower() for row in csv.DictReader(file) if row.get("email")}
    except (OSError, KeyError, csv.Error) as error:
        raise AgentError(f"Could not read {EMPLOYEES_CSV.name}: {error}") from error


def peek_sender(imap: imaplib.IMAP4_SSL, uid: bytes) -> str:
    """The sender's address, read without marking the email as read."""
    status, data = imap.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
    imap_ok(status, data, f"read the sender of email {uid.decode()}")
    header = BytesParser(policy=policy.default).parsebytes(fetch_first_literal(data) or b"")
    return parseaddr(str(header.get("From", "")))[1].strip().lower()


def ensure_label(imap: imaplib.IMAP4_SSL, label: str) -> None:
    """Create the Gmail label if it doesn't exist yet. Gmail labels are IMAP mailboxes."""
    try:
        imap.create(quote_mailbox(label))
    except imaplib.IMAP4.error:
        pass  # it already exists
    try:
        imap.subscribe(quote_mailbox(label))  # so mail apps list it too
    except imaplib.IMAP4.error:
        pass


def add_label(imap: imaplib.IMAP4_SSL, uid: bytes | str, label: str) -> None:
    """Add a Gmail label to one message in the selected mailbox."""
    uid = uid.decode() if isinstance(uid, bytes) else uid
    status, data = imap.uid("STORE", uid, "+X-GM-LABELS", f"({quote_mailbox(label)})")
    imap_ok(status, data, f"add the label {label!r}")


def appended_uid(data) -> str | None:
    """The new message's UID from Gmail's '[APPENDUID <validity> <uid>]' reply."""
    for item in data or []:
        text = item.decode(errors="replace") if isinstance(item, bytes) else str(item)
        match = re.search(r"\[APPENDUID \d+ (\d+)\]", text)
        if match:
            return match.group(1)
    return None


def label_for_review(
    imap: imaplib.IMAP4_SSL, inbox_uid: bytes, drafts_mailbox: str, draft_uid: str | None
) -> str:
    """Put the employee's email and the draft under the review label. Returns a note."""
    ensure_label(imap, REVIEW_LABEL)
    add_label(imap, inbox_uid, REVIEW_LABEL)  # INBOX is selected: label the original email
    if draft_uid is None:
        return f"Labeled the email {REVIEW_LABEL!r} (Gmail did not report the draft's id)."
    try:
        status, data = imap.select(drafts_mailbox)
        imap_ok(status, data, "open the Drafts folder")
        add_label(imap, draft_uid, REVIEW_LABEL)
    finally:
        status, data = imap.select(INBOX)  # back to the inbox for the next email
        imap_ok(status, data, "reopen the inbox")
    return f"Labeled the email and the draft {REVIEW_LABEL!r}."


def gmail_saver(
    imap: imaplib.IMAP4_SSL,
    drafts_mailbox: str,
    my_address: str,
    inbox_uid: bytes,
    message: EmailMessage,
    sender: str,
) -> Saver:
    """save_reply for --inbox: create a Gmail draft in the sender's thread. Never sends."""

    def save(reply_text: str, needs_review: bool) -> tuple[str, str]:
        subject = reply_subject(message)
        if needs_review:
            subject = f"{REVIEW_TAG} {subject}"

        draft = EmailMessage(policy=policy.SMTP)
        draft["From"] = my_address
        draft["To"] = str(message.get("Reply-To") or message.get("From") or sender)
        draft["Subject"] = subject
        draft["Date"] = formatdate(localtime=True)
        draft["Message-ID"] = make_msgid(domain=my_address.rpartition("@")[2] or None)
        original_id = str(message.get("Message-ID", "")).strip()
        if original_id:
            # These two headers are what put the draft in the sender's thread.
            draft["In-Reply-To"] = original_id
            references = str(message.get("References", "")).split()
            draft["References"] = " ".join(references + [original_id])
        draft.set_content(reply_text + "\n")

        try:
            status, data = imap.append(
                drafts_mailbox, r"(\Draft)", imaplib.Time2Internaldate(time.time()), draft.as_bytes()
            )
        except (imaplib.IMAP4.error, OSError) as error:
            raise ValueError(f"Gmail did not accept the draft: {error}") from error
        if status != "OK":
            raise ValueError(f"Gmail did not accept the draft: {data}")

        label_note = "none"
        if needs_review:
            # The draft exists either way; a labeling problem is reported, not fatal.
            try:
                label_note = label_for_review(imap, inbox_uid, drafts_mailbox, appended_uid(data))
            except (AgentError, imaplib.IMAP4.error) as error:
                label_note = f"WARNING: could not add the {REVIEW_LABEL!r} label: {error}"

        shown = (
            "GMAIL DRAFT - NOT SENT\n"
            f"To: {draft['To']}\n"
            f"Subject: {subject}\n"
            f"In-Reply-To: {original_id or '(none: the original email had no Message-ID)'}\n"
            f"Needs human review: {'yes' if needs_review else 'no'}\n"
            f"Gmail label: {label_note}\n"
            f"Model: {MODEL}\n\n"
            f"{reply_text}\n"
        )
        return f"Gmail drafts ({drafts_mailbox})", shown

    return save


def fetch_first_literal(data) -> bytes | None:
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def claim_unread_email(imap: imaplib.IMAP4_SSL, uid: bytes) -> bytes | None:
    """Mark one email as read, then return its raw bytes. None if it was already read."""
    status, data = imap.uid("FETCH", uid, "(FLAGS)")
    imap_ok(status, data, f"read the flags of email {uid.decode()}")
    flags = imaplib.ParseFlags(data[0] if data and isinstance(data[0], bytes) else b"")
    if b"\\Seen" in flags:
        return None  # someone (or another run) already handled it

    status, data = imap.uid("STORE", uid, "+FLAGS", r"(\Seen)")
    imap_ok(status, data, f"mark email {uid.decode()} as read")

    # BODY.PEEK reads the message without touching flags (it is already marked read).
    status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
    imap_ok(status, data, f"download email {uid.decode()}")
    raw = fetch_first_literal(data)
    if raw is None:
        raise AgentError(f"Gmail returned no content for email {uid.decode()}.")
    return raw


# --- Output ------------------------------------------------------------------


class Tee:
    """Write everything to the terminal and to a log file."""

    def __init__(self, stream, log):
        self.stream = stream
        self.log = log

    def write(self, text: str) -> int:
        self.stream.write(text)
        self.log.write(text)
        return len(text)

    def flush(self) -> None:
        self.stream.flush()
        self.log.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def preview(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "..."


def print_step(number: int, name: str, tool_input: dict, result: str, is_error: bool) -> None:
    print(f"\n[Step {number}] {name}")
    print(f"  inputs: {json.dumps(tool_input, ensure_ascii=False)}")
    print(f"  {'error' if is_error else 'result'}: {preview(result)}")


# --- Agent loop --------------------------------------------------------------


async def run_agent(
    client: anthropic.AsyncAnthropic,
    system_prompt: str,
    message: EmailMessage,
    sender: str,
    body: str,
    label: str,
    save: Saver,
) -> bool:
    """One separate agent run for one email. Returns True if a draft was saved."""
    print(f"Email:  {label}")
    print(f"Sender: {sender}")
    print(f"Model:  {MODEL}")

    server = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT), "--as-employee", sender],
        cwd=str(REPO_DIR),
    )
    async with stdio_client(server, errlog=sys.__stderr__) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            mcp_names = {tool.name for tool in listed.tools}
            tools = [
                {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                for t in listed.tools
            ] + [SAVE_REPLY_TOOL]
            print(f"Tools:  {', '.join(t['name'] for t in tools)}")

            messages: list[dict] = [
                {"role": "user", "content": build_user_message(message, sender, body)}
            ]
            input_tokens = output_tokens = 0
            tool_calls = 0
            draft_where: str | None = None
            draft_text = ""
            stop_reason_text = ""

            while True:
                try:
                    response = await client.messages.create(
                        model=MODEL,
                        max_tokens=MAX_TOKENS,
                        system=system_prompt,
                        tools=tools,
                        messages=messages,
                    )
                except anthropic.AuthenticationError:
                    stop_reason_text = "The API key was rejected."
                    break
                except anthropic.RateLimitError:
                    stop_reason_text = "Rate limited by the API. Try again in a minute."
                    break
                except anthropic.APIStatusError as error:
                    stop_reason_text = f"The API returned an error ({error.status_code}): {error.message}"
                    break
                except anthropic.APIConnectionError:
                    stop_reason_text = "Could not reach the API. Check your connection."
                    break

                input_tokens += response.usage.input_tokens
                output_tokens += response.usage.output_tokens
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason != "tool_use":
                    said = " ".join(b.text for b in response.content if b.type == "text").strip()
                    stop_reason_text = (
                        f"Claude replied without requesting a tool (stop reason: {response.stop_reason})."
                    )
                    if said:
                        stop_reason_text += f"\nClaude said: {preview(said)}"
                    break

                results = []
                for block in (b for b in response.content if b.type == "tool_use"):
                    if tool_calls >= MAX_TOOL_CALLS:
                        stop_reason_text = (
                            f"Reached the limit of {MAX_TOOL_CALLS} tool calls; "
                            f"did not run the {block.name} call Claude asked for next."
                        )
                        break
                    tool_calls += 1

                    if block.name == "save_reply":
                        if draft_where is not None:
                            text, is_error = "A draft was already saved. save_reply runs only once.", True
                        else:
                            try:
                                reply_text, needs_review = check_reply_input(block.input)
                                draft_where, draft_text = save(reply_text, needs_review)
                                text, is_error = f"Draft saved to {draft_where}. Not sent.", False
                            except (ValueError, OSError) as error:
                                text, is_error = f"The draft was not saved: {error}", True
                    elif block.name in mcp_names:
                        try:
                            result = await session.call_tool(block.name, block.input)
                            text, is_error = mcp_result_text(result)
                        except Exception as error:  # the server crashed or timed out
                            text, is_error = f"The tool call failed: {error}", True
                    else:
                        text, is_error = f"There is no tool named {block.name}.", True

                    print_step(tool_calls, block.name, block.input, text, is_error)
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": is_error}
                    )

                if stop_reason_text:
                    break
                if draft_where is not None:
                    stop_reason_text = "The draft was saved, so the task is done."
                    break
                if tool_calls >= MAX_TOOL_CALLS:
                    stop_reason_text = f"Reached the limit of {MAX_TOOL_CALLS} tool calls."
                    break
                messages.append({"role": "user", "content": results})

    print(f"\nStopped: {stop_reason_text}")
    print("\n" + "=" * 70)
    if draft_where is not None:
        print(f"Draft ({draft_where}):\n")
        print(draft_text.rstrip())
    else:
        print("No draft was saved.")
    print("=" * 70)
    print(f"Tool calls: {tool_calls}")
    print(f"Tokens: {input_tokens} input, {output_tokens} output")
    return draft_where is not None


async def run_file(email_path: Path) -> int:
    message, sender, body = read_email(email_path)
    system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    client = anthropic.AsyncAnthropic(api_key=load_api_key())
    saved = await run_agent(
        client, system_prompt, message, sender, body, str(email_path),
        file_saver(email_path, message, sender),
    )
    return 0 if saved else 1


async def run_inbox() -> int:
    client = anthropic.AsyncAnthropic(api_key=load_api_key())

    address, password = read_login(LOGIN_FILE)
    imap = sign_in(address, password)
    del password
    print(f"Signed in to Gmail as {address}. This agent reads email and creates drafts only.")

    results: list[tuple[str, str]] = []  # (email, outcome)
    try:
        drafts_mailbox = find_drafts_mailbox(imap)
        status, data = imap.select(INBOX)
        imap_ok(status, data, "open the inbox")
        status, data = imap.uid("SEARCH", None, "UNSEEN")
        imap_ok(status, data, "search the inbox")
        uids = data[0].split() if data and data[0] else []
        print(f"Unread emails in {INBOX}: {len(uids)}")
        employees = employee_addresses()

        for number, uid in enumerate(uids, start=1):
            print("\n" + "#" * 70)
            print(f"# Email {number} of {len(uids)} (IMAP UID {uid.decode()})")
            print("#" * 70)

            # Only employees get an agent run. Anything else stays unread and untouched.
            sender_address = peek_sender(imap, uid)
            if sender_address not in employees:
                shown = sender_address or "(no sender address)"
                print(f"Skipped: {shown} is not in {EMPLOYEES_CSV.name}. Left unread.")
                results.append((f"UID {uid.decode()} from {shown}", "skipped (not an employee; left unread)"))
                continue

            raw = claim_unread_email(imap, uid)
            if raw is None:
                print("Already marked read by something else; skipped.")
                results.append((f"UID {uid.decode()}", "skipped (already read)"))
                continue

            label = f"inbox UID {uid.decode()}"
            try:
                message, sender, body = parse_email(raw, label)
            except AgentError as error:
                print(f"Skipped: {error}")
                results.append((label, f"skipped: {error}"))
                continue
            label = f"{label}: {message.get('Subject', '(no subject)')}"

            # Re-read the system prompt for every run, so each email is a fresh run.
            system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
            saver = gmail_saver(imap, drafts_mailbox, address, uid, message, sender)
            saved = await run_agent(client, system_prompt, message, sender, body, label, saver)
            results.append((label, "draft created" if saved else "NO DRAFT (still marked read)"))
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    print("\n" + "#" * 70)
    print("Summary (nothing was sent):")
    for label, outcome in results:
        print(f"  {label}: {outcome}")
    if not results:
        print("  No unread emails.")
    missing = sum(1 for _, outcome in results if outcome.startswith("NO DRAFT"))
    if missing:
        print(f"{missing} email(s) got no draft. Mark them unread in Gmail to try again.")
    return 1 if missing else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Draft replies to Hartwell Foods HR emails.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--email", type=Path, help="path to one email file (.eml)")
    source.add_argument(
        "--inbox", action="store_true",
        help="handle every unread email in the Gmail sandbox inbox, creating Gmail drafts",
    )
    parser.add_argument(
        "--save", type=Path, metavar="FILE",
        help="also write everything printed to this text file (UTF-8)",
    )
    args = parser.parse_args()

    log = None
    if args.save:
        try:
            log = args.save.open("w", encoding="utf-8")
        except OSError as error:
            sys.exit(f"Error: could not open {args.save} for writing: {error}")
        sys.stdout = Tee(sys.__stdout__, log)
        sys.stderr = Tee(sys.__stderr__, log)

    code: int | str = 0
    try:
        code = asyncio.run(run_inbox() if args.inbox else run_file(args.email))
    except AgentError as error:
        code = f"Error: {error}"
    except (imaplib.IMAP4.error, OSError) as error:
        code = f"Error: lost the connection to Gmail: {error}"
    except SystemExit as stop:  # e.g. a refused Gmail login from load_inbox.sign_in
        code = stop.code
    finally:
        if isinstance(code, str):
            print(code, file=sys.stderr)
            code = 1
        if log is not None:
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            log.close()
            print(f"Output saved to {args.save}")
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
