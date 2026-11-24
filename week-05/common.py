"""Helpers shared by the week-05 specialists and both coordinators.

Most of this is carried over from week-04/agent.py: reading the API key and
.eml files, the tool loop and its API error handling, the printed steps, the
--save log (Tee), and the pipe that copies the MCP server's stderr into it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import Awaitable, Callable

import anthropic

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000
PREVIEW_CHARS = 300

WEEK_DIR = Path(__file__).resolve().parent
REPO_DIR = WEEK_DIR.parent
DRAFTS_DIR = WEEK_DIR / "drafts"
# The key file sits in the folder above the repository. It is read, never written or printed.
KEY_FILES = [REPO_DIR.parent / "API-Key.txt", REPO_DIR.parent / "API-key.txt"]


class AgentError(Exception):
    """A problem that stops a specialist or a coordinator."""


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
    raise AgentError(f"No API key found. Set ANTHROPIC_API_KEY or put the key in {KEY_FILES[0]}.")


@dataclass
class Email:
    path: Path
    message: EmailMessage
    sender: str
    body: str

    @property
    def subject(self) -> str:
        return str(self.message.get("Subject", "")).strip() or "(no subject)"

    @property
    def reply_subject(self) -> str:
        s = self.subject
        return s if s.lower().startswith("re:") else f"Re: {s}"

    def as_text(self) -> str:
        """The email for a prompt. Everything inside <email> came from the sender."""
        return (
            f"<email>\nFrom: {self.message.get('From', self.sender)}\nSubject: {self.subject}\n\n"
            f"{self.body.strip()}\n</email>"
        )


def read_email(path: Path) -> Email:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise AgentError(f"Could not read {path}: {error}") from error
    message = BytesParser(policy=policy.default).parsebytes(data)
    _, sender = parseaddr(str(message.get("From", "")))
    sender = sender.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", sender):
        raise AgentError(f"{path} has no usable sender address in its From header.")
    part = message.get_body(preferencelist=("plain",))
    body = part.get_content() if part is not None else ""
    if not body.strip():
        raise AgentError(f"{path} has no plain-text body.")
    return Email(path, message, sender, body)


def read_prompt(folder: Path, name: str) -> str:
    path = folder / "prompts" / f"{name}.txt"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise AgentError(f"Could not read the {name} prompt at {path}: {error}") from error
    if not text:
        raise AgentError(f"The {name} prompt at {path} is empty.")
    return text


def relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_DIR))
    except ValueError:
        return str(path)


# --- Calling Claude ------------------------------------------------------------


@dataclass
class Usage:
    """Tokens used, by specialist."""

    by_role: dict[str, list[int]] = field(default_factory=dict)

    def add(self, role: str, response) -> None:
        totals = self.by_role.setdefault(role, [0, 0, 0])
        totals[0] += response.usage.input_tokens
        totals[1] += response.usage.output_tokens
        totals[2] += 1

    def lines(self) -> list[str]:
        out = []
        for role, (i, o, calls) in self.by_role.items():
            out.append(f"{role}: {calls} call(s), {i} input, {o} output tokens")
        i = sum(v[0] for v in self.by_role.values())
        o = sum(v[1] for v in self.by_role.values())
        out.append(f"Total: {i} input, {o} output tokens")
        return out


async def ask(client: anthropic.AsyncAnthropic, usage: Usage, role: str, system: str,
              messages: list, tools: list | None = None, effort: str = "medium"):
    """One Messages API call. Raises AgentError on API errors and refusals."""
    kwargs = {"tools": tools} if tools else {}
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            output_config={"effort": effort},
            **kwargs,
        )
    except anthropic.AuthenticationError:
        raise AgentError("The API key was rejected.") from None
    except anthropic.RateLimitError:
        raise AgentError("Rate limited by the API. Try again in a minute.") from None
    except anthropic.APIStatusError as error:
        raise AgentError(f"The API returned an error ({error.status_code}): {error.message}") from None
    except anthropic.APIConnectionError:
        raise AgentError("Could not reach the API. Check your connection.") from None
    usage.add(role, response)
    print(f"  ({role}: {response.usage.input_tokens} input, {response.usage.output_tokens} output tokens)")
    if response.stop_reason == "refusal":
        raise AgentError(f"The {role} request was declined (refusal).")
    if response.stop_reason == "max_tokens":
        raise AgentError(f"The {role} reply was cut off at {MAX_TOKENS} tokens.")
    return response


def response_text(response) -> str:
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


# A tool runner takes (name, input) and returns (result text, is_error).
ToolRunner = Callable[[str, dict], Awaitable[tuple[str, bool]]]


@dataclass
class LoopResult:
    text: str          # Claude's final text, when it stopped without a tool request
    stopped: str       # why the loop stopped
    tool_calls: int


async def tool_loop(client, usage: Usage, role: str, system: str, messages: list, tools: list,
                    run_tool: ToolRunner, max_calls: int, is_done: Callable[[], bool] = lambda: False,
                    effort: str = "medium", calls_so_far: int = 0) -> LoopResult:
    """The week-4 tool loop, made general. messages is extended in place, so a
    caller can add a message and call this again to continue the conversation."""
    calls = calls_so_far
    while True:
        response = await ask(client, usage, role, system, messages, tools, effort)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return LoopResult(response_text(response), f"{role} finished (stop reason: {response.stop_reason}).", calls)

        results = []
        stopped = ""
        for block in (b for b in response.content if b.type == "tool_use"):
            if calls >= max_calls:
                stopped = f"Reached the limit of {max_calls} tool calls; did not run {block.name}."
                text, is_error = "Not run: the tool-call limit was reached.", True
            else:
                calls += 1
                try:
                    text, is_error = await run_tool(block.name, block.input)
                except AgentError as error:
                    text, is_error = f"The tool failed: {error}", True
                print_step(role, calls, block.name, block.input, text, is_error)
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": is_error})
        messages.append({"role": "user", "content": results})
        if stopped:
            return LoopResult("", stopped, calls)
        if is_done():
            return LoopResult("", f"{role} finished.", calls)


# --- Printing and logs ---------------------------------------------------------


def preview(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "..."


def handoff(sender: str, receiver: str, what: str, payload: str | None = None) -> None:
    """Print one handoff between the coordinator and a specialist, or between specialists."""
    print(f"\n>>> HANDOFF  {sender} -> {receiver}: {what}")
    if payload:
        for line in payload.strip().splitlines():
            print(f"    | {line}")
    sys.stdout.flush()


def heading(text: str) -> None:
    print(f"\n--- {text} " + "-" * max(3, 66 - len(text)))
    sys.stdout.flush()


def print_step(role: str, number: int, name: str, tool_input: dict, result: str, is_error: bool) -> None:
    print(f"\n[{role} step {number}] {name}")
    print(f"  inputs: {json.dumps(tool_input, ensure_ascii=False)}")
    print(f"  {'error' if is_error else 'result'}: {preview(result)}")
    sys.stdout.flush()


def mcp_result_text(result) -> tuple[str, bool]:
    """Turn an MCP tool result into text for Claude, plus whether it is an error."""
    if not hasattr(result, "content"):
        return "The tool needs input this program can't provide.", True
    parts = [block.text for block in result.content if getattr(block, "type", "") == "text"]
    text = "\n".join(parts)
    if not text and getattr(result, "structured_content", None) is not None:
        text = json.dumps(result.structured_content)
    return text or "(empty result)", bool(getattr(result, "is_error", False))


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


@contextlib.contextmanager
def server_errlog():
    """A pipe for the MCP server's stderr, copied line by line to sys.stderr,
    so its messages reach the terminal and the --save log alike."""
    read_fd, write_fd = os.pipe()
    writer = os.fdopen(write_fd, "w", encoding="utf-8")

    def copy() -> None:
        with os.fdopen(read_fd, encoding="utf-8", errors="replace") as reader:
            for line in reader:
                sys.stderr.write(line)
                sys.stderr.flush()

    thread = threading.Thread(target=copy, daemon=True)
    thread.start()
    try:
        yield writer
    finally:
        writer.close()
        thread.join(timeout=5)


# --- Command line ----------------------------------------------------------------


def run_cli(description: str, handle: Callable[[anthropic.AsyncAnthropic, Path, Email], Awaitable[bool]]) -> None:
    """Shared command line for pipeline.py, orchestrator.py, and specialists.py."""
    from supply_data import DEFAULT_DATA_DIR, DataError

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--email", type=Path, required=True, metavar="EML",
                        help="the supplier email file (.eml) to prepare a response to")
    parser.add_argument("--org", type=Path, metavar="FOLDER", default=DEFAULT_DATA_DIR,
                        help="company folder with the five CSV files and prompts/ (default: week-05/brookfield-bikes)")
    parser.add_argument("--save", type=Path, metavar="FILE",
                        help="also write everything printed to this text file (UTF-8)")
    args = parser.parse_args()

    log = None
    if args.save:
        try:
            args.save.parent.mkdir(parents=True, exist_ok=True)
            log = args.save.open("w", encoding="utf-8")
        except OSError as error:
            sys.exit(f"Error: could not open {args.save} for writing: {error}")
        sys.stdout = Tee(sys.__stdout__, log)
        sys.stderr = Tee(sys.__stderr__, log)

    async def run_all() -> int:
        if not args.org.is_dir():
            raise AgentError(f"--org {args.org} is not a folder.")
        email = read_email(args.email)
        client = anthropic.AsyncAnthropic(api_key=load_api_key())
        try:
            return 0 if await handle(client, args.org.resolve(), email) else 1
        except DataError as error:
            raise AgentError(f"The company data in {args.org} could not be read: {error}") from None

    code: int | str = 0
    try:
        code = asyncio.run(run_all())
    except AgentError as error:
        code = f"Error: {error}"
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
    sys.exit(code)
