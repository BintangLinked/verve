"""CLI helper to generate ADMIN_PASSWORD_HASH and SESSION_SECRET: `python -m app.hash_password`.

Purpose: the admin password is never written in plain text in .env. This tool prompts for the password (without
echoing it on screen) and then prints two lines that can be pasted into backend/.env.
Relations: uses hash_password from app/auth.py; the output is read by app/config.py.
"""
import getpass
import secrets
import sys

from app.auth import hash_password


def main() -> int:
    password = getpass.getpass("Admin password (min 12 characters): ")
    if len(password) < 12:
        print("Password is too short.", file=sys.stderr)
        return 1
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1
    # Single quotes: the hash contains `$` characters that must not be interpreted.
    print(f"ADMIN_PASSWORD_HASH='{hash_password(password)}'")
    print(f"SESSION_SECRET='{secrets.token_urlsafe(48)}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
