"""Xiaoyuzhou FM CLI — agent-friendly, composable, clean.

Thin argparse surface over XiaoyuzhouClient. Every command prints JSON to stdout
(pretty by default, `--jsonl` for one-object-per-line piping). Episode results
drop the bulky `shownotes_html` unless `--full`; `--fields a,b,c` projects keys.

Errors print a JSON {"error":...} to stderr and exit non-zero, so callers can
branch on exit code without parsing stdout.

  xyz subs
  xyz episodes <pid> --since 2026-05-28 --until 2026-05-29
  xyz episode <eid>
  xyz transcript <eid> --media-id <mid> --format plain
  xyz transcript-url <eid> --media-id <mid>
  xyz search "关键词" --kind EPISODE
  xyz history
  xyz send-code <phone>        # then check SMS
  xyz login <phone> <code>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .client import XiaoyuzhouClient, XiaoyuzhouError

SLIM_KEYS = ("shownotes_html",)


def _clean(obj: Any, *, keep_full: bool, fields: list[str] | None) -> Any:
    if isinstance(obj, dict):
        obj = dict(obj)
        if not keep_full:
            for k in SLIM_KEYS:
                obj.pop(k, None)
        if fields:
            obj = {k: obj.get(k) for k in fields}
    return obj


def _emit(obj: Any, *, jsonl: bool = False, keep_full: bool = False,
          fields: list[str] | None = None) -> None:
    if isinstance(obj, list):
        items = [_clean(x, keep_full=keep_full, fields=fields) for x in obj]
        if jsonl:
            for x in items:
                print(json.dumps(x, ensure_ascii=False))
        else:
            print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(_clean(obj, keep_full=keep_full, fields=fields),
                         ensure_ascii=False, indent=2))


def _fields(arg: str | None) -> list[str] | None:
    return [f.strip() for f in arg.split(",") if f.strip()] if arg else None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xyz", description="Xiaoyuzhou FM CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_out(sp, *, full=True, jsonl=True, fields=True):
        if full:
            sp.add_argument("--full", action="store_true",
                            help="keep shownotes_html (dropped by default)")
        if jsonl:
            sp.add_argument("--jsonl", action="store_true",
                            help="one JSON object per line (for piping)")
        if fields:
            sp.add_argument("--fields", default=None,
                            help="comma-separated keys to keep, e.g. title,pub_date")

    sp = sub.add_parser("subs", help="list your subscribed podcasts")
    add_out(sp, full=False)

    sp = sub.add_parser("episodes", help="a podcast's episodes, newest first (auto-paginated)")
    sp.add_argument("pid")
    sp.add_argument("--limit", type=int, default=20, help="max episodes when no --since (default 20)")
    sp.add_argument("--since", default=None, help="inclusive YYYY-MM-DD; pages back to cover full window")
    sp.add_argument("--until", default=None, help="inclusive YYYY-MM-DD upper bound")
    add_out(sp)

    sp = sub.add_parser("episode", help="one episode's detail")
    sp.add_argument("eid")
    add_out(sp, jsonl=False)

    sp = sub.add_parser("transcript", help="download + parse the official transcript")
    sp.add_argument("eid")
    sp.add_argument("--media-id", required=True)
    sp.add_argument("--format", default="plain", choices=["plain", "timestamped", "segments"])
    sp.add_argument("--segments", action="store_true", help="also include parsed segment list")
    sp.add_argument("--text", action="store_true", help="print only the rendered text (no JSON wrapper)")

    sp = sub.add_parser("transcript-url", help="get the signed transcript URL only")
    sp.add_argument("eid")
    sp.add_argument("--media-id", required=True)

    sp = sub.add_parser("search", help="search podcasts or episodes")
    sp.add_argument("query")
    sp.add_argument("--kind", default="PODCAST", choices=["PODCAST", "EPISODE"])
    sp.add_argument("--limit", type=int, default=20)
    add_out(sp)

    sp = sub.add_parser("history", help="recently played episodes")
    sp.add_argument("--limit", type=int, default=20)
    add_out(sp, full=False)

    sp = sub.add_parser("send-code", help="send SMS login code")
    sp.add_argument("phone")
    sp.add_argument("--area-code", default="+86")

    sp = sub.add_parser("login", help="login with SMS code (saves token)")
    sp.add_argument("phone")
    sp.add_argument("code")
    sp.add_argument("--area-code", default="+86")

    return p


def run(args: argparse.Namespace) -> None:
    full = getattr(args, "full", False)
    jsonl = getattr(args, "jsonl", False)
    fields = _fields(getattr(args, "fields", None))
    with XiaoyuzhouClient() as c:
        if args.cmd == "subs":
            _emit(c.list_subscriptions(), jsonl=jsonl, keep_full=True, fields=fields)
        elif args.cmd == "episodes":
            eps = c.list_episodes(args.pid, limit=args.limit, since=args.since, until=args.until)
            _emit(eps, jsonl=jsonl, keep_full=full, fields=fields)
        elif args.cmd == "episode":
            _emit(c.get_episode(args.eid), keep_full=full, fields=fields)
        elif args.cmd == "transcript":
            out = c.fetch_transcript(args.eid, args.media_id, fmt=args.format,
                                     include_segments=args.segments)
            if args.text:
                if "text" in out:
                    print(out["text"])
                else:  # 'segments' mode or no_subtitle has no rendered text
                    print(json.dumps(
                        {"error": "no_text",
                         "message": f"no rendered text (status={out.get('status')}, "
                                    f"format={args.format}); drop --text or use --format plain"},
                        ensure_ascii=False), file=sys.stderr)
                    return 3
            else:
                _emit(out, keep_full=True)
        elif args.cmd == "transcript-url":
            _emit(c.get_transcript_url(args.eid, args.media_id), keep_full=True)
        elif args.cmd == "search":
            hits = c.search(args.query, kind=args.kind, limit=args.limit)
            _emit(hits, jsonl=jsonl, keep_full=full, fields=fields)
        elif args.cmd == "history":
            _emit(c.list_play_history(limit=args.limit), jsonl=jsonl, keep_full=True, fields=fields)
        elif args.cmd == "send-code":
            _emit(c.send_sms_code(args.phone, area_code=args.area_code), keep_full=True)
        elif args.cmd == "login":
            _emit(c.login_with_sms(args.phone, args.code, area_code=args.area_code), keep_full=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args) or 0
    except XiaoyuzhouError as e:
        print(json.dumps(e.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    except BrokenPipeError:
        # downstream pipe (e.g. `| head`) closed. Redirect stdout to devnull so the
        # interpreter's shutdown flush can't re-raise + print "Exception ignored".
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except Exception:
            pass
        return 0
    except Exception as e:  # noqa: BLE001 — surface anything else as a clean error
        print(json.dumps({"error": "unexpected", "message": str(e)}, ensure_ascii=False),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
