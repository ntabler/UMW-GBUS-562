"""One organization's HR folder, and reading its employee file.

An organization folder holds:

    hr-docs/            the HR policy documents (HTML)
    employees.csv       one row per employee
    system_prompt.txt   the agent's instructions
    sample-emails/      example .eml files (optional)

The default is week-04/hartwell-hr. Pass another folder with --org.

The employee file's column names differ between organizations, so they are
worked out from the header row:

- the email column is the one whose header mentions "email" (or "e-mail");
  if there are several, one that also says "work" wins, else the first;
- the name column is "Name", "Full Name", "Employee Name", and so on, or
  "First Name" and "Last Name" joined;
- balance columns are the ones whose header says "left", "remaining",
  "balance", or "available" (for example "vacation_days_left" or
  "PTO Days Remaining"). Their values must be numbers.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

WEEK_DIR = Path(__file__).resolve().parent
DEFAULT_ORG_DIR = WEEK_DIR / "hartwell-hr"
# One Chroma database per organization, each in its own subfolder. Git ignores all of it.
DB_ROOT = WEEK_DIR / "chroma_db"

BALANCE_WORDS = {"left", "remaining", "balance", "available"}


class OrgError(Exception):
    """A problem with the organization folder or its employee file."""


@dataclass(frozen=True)
class Org:
    dir: Path

    @property
    def name(self) -> str:
        return self.dir.name

    @property
    def docs_dir(self) -> Path:
        return self.dir / "hr-docs"

    @property
    def employees_csv(self) -> Path:
        return self.dir / "employees.csv"

    @property
    def system_prompt_file(self) -> Path:
        return self.dir / "system_prompt.txt"

    @property
    def emails_dir(self) -> Path:
        return self.dir / "sample-emails"

    @property
    def db_dir(self) -> Path:
        # The folder name keeps it readable; the hash keeps two folders with the
        # same name (in different places) from sharing a database.
        digest = hashlib.sha256(str(self.dir).encode()).hexdigest()[:8]
        return DB_ROOT / f"{self.name}-{digest}"

    def system_prompt(self) -> str:
        try:
            text = self.system_prompt_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise OrgError(f"Could not read {self.system_prompt_file}: {error}") from error
        if not text:
            raise OrgError(f"{self.system_prompt_file} is empty.")
        return text


def load_org(path: str | Path | None) -> Org:
    """The organization folder at path (or the default), checked for its parts."""
    folder = Path(path).expanduser().resolve() if path else DEFAULT_ORG_DIR
    if not folder.is_dir():
        raise OrgError(f"The organization folder was not found: {folder}")
    org = Org(folder)
    missing = [
        p.name for p in (org.docs_dir, org.employees_csv, org.system_prompt_file) if not p.exists()
    ]
    if missing:
        raise OrgError(f"{folder} is missing: {', '.join(missing)}")
    return org


# --- Employee file -------------------------------------------------------------


def words(header: str) -> list[str]:
    """'PTO Days Remaining' -> ['pto', 'days', 'remaining']; 'e-mail' -> ['email']."""
    text = re.sub(r"e-mail", "email", header.lower())
    return re.findall(r"[a-z0-9]+", text)


def is_email_header(header: str) -> bool:
    return any(w in ("email", "mail") or w.endswith("email") for w in words(header))


@dataclass(frozen=True)
class Columns:
    email: str
    name: str | None
    first_last: tuple[str, str] | None
    balances: tuple[str, ...]

    def person_name(self, row: dict) -> str | None:
        if self.name:
            return (row.get(self.name) or "").strip() or None
        if self.first_last:
            joined = " ".join((row.get(c) or "").strip() for c in self.first_last).strip()
            return joined or None
        return None


def find_columns(headers: list[str], source: str = "employees.csv") -> Columns:
    headers = [h for h in headers if h and h.strip()]
    shown = ", ".join(repr(h) for h in headers) or "(none)"

    emails = [h for h in headers if is_email_header(h)]
    if not emails:
        raise OrgError(f"{source} has no email column (its columns are {shown}).")
    work = [h for h in emails if "work" in words(h)]
    email = (work or emails)[0]

    by_words = {" ".join(words(h)): h for h in headers}
    name = next(
        (by_words[k] for k in ("name", "full name", "employee name", "employee", "staff name",
                               "team member", "team member name") if k in by_words),
        None,
    )
    first_last = None
    if name is None:
        first = next((h for h in headers if words(h)[:1] == ["first"]), None)
        last = next((h for h in headers if words(h)[:1] == ["last"]), None)
        if first and last:
            first_last = (first, last)

    balances = tuple(
        h for h in headers
        if h != email and BALANCE_WORDS & set(words(h))
    )
    if not balances:
        raise OrgError(
            f"{source} has no balance columns: none of its column names say "
            f"{', '.join(sorted(BALANCE_WORDS))} (its columns are {shown})."
        )
    return Columns(email, name, first_last, balances)


def read_employees(path: Path) -> tuple[Columns, list[dict]]:
    """The columns and every row. Read fresh on each call, so edits show up right away."""
    try:
        # utf-8-sig drops the byte-order mark Excel adds, which would spoil the first header.
        with path.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            columns = find_columns(list(reader.fieldnames or []), path.name)
            rows = list(reader)
    except (OSError, csv.Error, UnicodeDecodeError) as error:
        raise OrgError(f"{path.name} could not be read ({error}).") from error
    return columns, rows


def find_employee(path: Path, email: str) -> tuple[Columns, dict | None]:
    columns, rows = read_employees(path)
    wanted = email.strip().lower()
    for row in rows:
        if (row.get(columns.email) or "").strip().lower() == wanted:
            return columns, row
    return columns, None


def employee_addresses(path: Path) -> set[str]:
    columns, rows = read_employees(path)
    return {
        address
        for row in rows
        if (address := (row.get(columns.email) or "").strip().lower())
    }


def number(text: str) -> int | float:
    value = float(text.strip().replace(",", ""))
    return int(value) if value.is_integer() else value
