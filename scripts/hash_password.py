#!/usr/bin/env python3
"""Generate a PBKDF2 password hash for a TMS local account.

Run this on the operator's machine. The plaintext password never leaves the
terminal and never enters configuration - only the hash does. That matters here
because this repository is PUBLIC (DECISIONS.md D-002).

    python3 scripts/hash_password.py --user syhcho --roles admin

The password is read from a hidden prompt, so it stays out of shell history and
the process list.

Standard library only. Python 3.9 compatible.
"""

import argparse
import getpass
import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from tms.core.passwords import PasswordError, check_password_strength, hash_password  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="account name, e.g. syhcho")
    parser.add_argument(
        "--roles",
        default="admin",
        help="comma-separated: viewer, operator, admin (default: admin)",
    )
    parser.add_argument(
        "--temporary",
        action="store_true",
        help="mark the account so the holder must change the password at first login",
    )
    parser.add_argument(
        "--allow-weak",
        action="store_true",
        help="skip the strength check (not recommended for an account that can kill queries)",
    )
    args = parser.parse_args()

    password = getpass.getpass("password for {}: ".format(args.user))
    confirmation = getpass.getpass("confirm: ")
    if password != confirmation:
        print("ERROR: passwords do not match", file=sys.stderr)
        return 1

    if not args.allow_weak:
        try:
            check_password_strength(password)
        except PasswordError as exc:
            print("ERROR: {}".format(exc), file=sys.stderr)
            print("(--allow-weak 로 무시할 수 있으나 권장하지 않는다)", file=sys.stderr)
            return 1

    roles = [role.strip() for role in args.roles.split(",") if role.strip()]
    encoded = hash_password(password)

    print("\n# config/config.secret.yaml 의 portal 아래에 붙여넣는다")
    print("# 이 파일은 gitignore 대상이다. 절대 config.yaml 에 넣지 않는다.")
    print("portal:")
    print("  local_users:")
    print("    {}:".format(args.user))
    print('      password_hash: "{}"'.format(encoded))
    print("      roles: [{}]".format(", ".join(roles)))
    if args.temporary:
        print("      must_change_password: true")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
