#!/usr/bin/env python3
"""Interactive SMS login for Xiaoyuzhou.

Usage:
    python scripts/login.py --phone 13800138000
    # then prompts for SMS code

Or two-step:
    python scripts/login.py --send 13800138000
    python scripts/login.py --verify 13800138000 1234
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xiaoyuzhou.client import XiaoyuzhouClient, XiaoyuzhouError


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", help="phone, +86 by default")
    ap.add_argument("--area-code", default="+86")
    ap.add_argument("--send", help="just send SMS code, exit")
    ap.add_argument("--verify", nargs=2, metavar=("PHONE", "CODE"),
                    help="verify SMS code with phone")
    args = ap.parse_args()

    with XiaoyuzhouClient() as c:
        try:
            if args.send:
                res = c.send_sms_code(args.send, args.area_code)
                print(f"SMS sent to {res['phone']}. Run --verify <phone> <code> next.")
                return 0
            if args.verify:
                phone, code = args.verify
                res = c.login_with_sms(phone, code, args.area_code)
                print(f"Logged in as {res['nickname']} (uid={res['uid']})")
                print(f"Token saved to {res['token_saved_to']}")
                return 0
            if args.phone:
                c.send_sms_code(args.phone, args.area_code)
                print(f"SMS sent to {args.phone}.")
                code = input("Enter SMS code: ").strip()
                res = c.login_with_sms(args.phone, code, args.area_code)
                print(f"Logged in as {res['nickname']} (uid={res['uid']})")
                print(f"Token saved to {res['token_saved_to']}")
                return 0
            ap.print_help()
            return 2
        except XiaoyuzhouError as e:
            print(f"ERROR [{e.kind}] {e.message}", file=sys.stderr)
            if e.hint:
                print(f"hint: {e.hint}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
