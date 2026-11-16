#!/usr/bin/env python3
"""Place the sample HR emails into a Gmail sandbox inbox as unread messages.

Usage (from the top of the repository):

    .venv/bin/python week-04/load_inbox.py            # load every sample email
    .venv/bin/python week-04/load_inbox.py --dry-run  # list them, don't sign in

It signs in with IMAP and uses APPEND to place each .eml file from
week-04/hartwell-hr/sample-emails directly into INBOX, without the \\Seen flag
(so it shows as unread) and dated now. Nothing is sent: no SMTP is used.

The login comes from Agent-Email.txt in the folder above the repository. The
file is read at run time only. The password is never printed or saved.
Expected format for that file:

    EMAIL_ADDRESS=you@gmail.com
    APP_PASSWORD=abcd efgh ijkl mnop

(It also accepts "email: ..." / "password: ..." labels, or the address and
password on two unlabeled lines.)
"""

import argparse
import imaplib
import re
import sys
import time
from email import policy
from email.parser import BytesParser
from email.utils import formatdate, make_msgid
from pathlib import Path

WEEK_DIR = Path(__file__).resolve().parent
EMAIL_DIR = WEEK_DIR / "hartwell-hr" / "sample-emails"
LOGIN_FILE = WEEK_DIR.parent.parent / "Agent-Email.txt"
IMAP_HOST = "imap.gmail.com"
MAILBOX = "INBOX"

ADDRESS_KEYS = {"email address", "email", "address", "user", "username", "login", "account"}
PASSWORD_KEYS = {"app password", "password", "pass"}


def read_login(path: Path) -> tuple[str, str]:
    """Return (address, password) from the login file. Never prints the password."""
    if not path.is_file():
        sys.exit(f"Error: login file not found: {path}")
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]

    address = password = None
    unlabeled = []
    for line in lines:
        match = re.match(r"^([A-Za-z _-]+?)\s*[:=]\s*(.+)$", line)
        # EMAIL_ADDRESS, email-address and "Email address" all become "email address".
        key = re.sub(r"[\s_-]+", " ", match.group(1)).strip().lower() if match else ""
        value = match.group(2).strip().strip("\"'").strip() if match else ""
        if key in ADDRESS_KEYS:
            address = value
        elif key in PASSWORD_KEYS:
            password = value
        else:
            unlabeled.append(line)

    # Fall back to "address on one line, password on the next".
    if address is None:
        address = next((line for line in unlabeled if "@" in line), None)
        if address is not None:
            unlabeled.remove(address)
    if password is None and unlabeled:
        password = unlabeled[0]

    if not address or "@" not in address:
        sys.exit(f"Error: no email address found in {path.name}.")
    if not password:
        sys.exit(f"Error: no password found in {path.name}.")
    return address, password


def prepare_message(path: Path) -> tuple[bytes, str]:
    """Return the message as CRLF bytes with today's Date, plus its subject."""
    message = BytesParser(policy=policy.SMTP).parse(path.open("rb"))
    del message["Date"]
    message["Date"] = formatdate(localtime=True)
    if message["Message-ID"] is None:
        message["Message-ID"] = make_msgid(domain="hartwellfoods.example")
    return message.as_bytes(), str(message.get("Subject", "(no subject)"))


def safe_error(error: Exception, password: str) -> str:
    """Error text with the password removed, in case a server ever echoes it."""
    text = str(error)
    for secret in {password, password.replace(" ", "")}:
        if secret:
            text = text.replace(secret, "[hidden]")
    return text


def sign_in(address: str, password: str) -> imaplib.IMAP4_SSL:
    # Gmail app passwords are shown with spaces; try as written, then without them.
    attempts = [password]
    if " " in password:
        attempts.append(password.replace(" ", ""))
    last_error = None
    for attempt in attempts:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        try:
            imap.login(address, attempt)
            return imap
        except imaplib.IMAP4.error as error:
            last_error = error
            try:
                imap.logout()
            except Exception:
                pass
    # Reveal only whether the value has an app password's shape, never the value.
    looks_like_app_password = re.fullmatch(r"[a-z]{16}", password.replace(" ", "")) is not None
    hint = (
        "The password in the file has the shape of an app password (16 letters), so it is "
        "probably wrong or revoked: make a new one and paste it in."
        if looks_like_app_password else
        "The password in the file is NOT 16 lowercase letters, so it is not a Google app "
        "password (or has extra characters, such as a comment or trailing text, on its line)."
    )
    sys.exit(
        f"Error: Gmail refused the login for {address}: {safe_error(last_error, password)}\n"
        f"{hint}\nApp passwords need 2-Step Verification on: https://myaccount.google.com/apppasswords"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="list the emails that would be loaded, without signing in")
    args = parser.parse_args()

    files = sorted(EMAIL_DIR.glob("*.eml"))
    if not files:
        sys.exit(f"Error: no .eml files in {EMAIL_DIR}")

    if args.dry_run:
        for path in files:
            _, subject = prepare_message(path)
            print(f"Would load {path.name}: {subject}")
        print(f"{len(files)} emails. Nothing was loaded.")
        return

    address, password = read_login(LOGIN_FILE)
    imap = sign_in(address, password)
    del password
    print(f"Signed in to {IMAP_HOST} as {address}.")

    loaded = 0
    try:
        for path in files:
            data, subject = prepare_message(path)
            status, response = imap.append(
                MAILBOX, "()", imaplib.Time2Internaldate(time.time()), data
            )
            if status == "OK":
                loaded += 1
                print(f"Loaded {path.name}: {subject}")
            else:
                print(f"Failed {path.name}: {response}", file=sys.stderr)
    finally:
        imap.logout()

    print(f"{loaded} of {len(files)} emails placed in {MAILBOX} as unread. Nothing was sent.")
    if loaded != len(files):
        sys.exit(1)


if __name__ == "__main__":
    main()
