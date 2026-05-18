#!/usr/bin/env python3
"""End-to-end MCP test: spawn the registered `run` script as a stdio MCP
child process, do the full handshake, and call every tool exactly once."""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

RUN = str(Path(__file__).resolve().parent.parent / "run")


def short(obj, n=120):
    s = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
    return s if len(s) <= n else s[:n] + "..."


def _unwrap(result):
    """FastMCP wraps return value as structuredContent={'result': <value>}."""
    if result.isError:
        return None
    sc = result.structuredContent or {}
    return sc.get("result") if "result" in sc else sc


async def main() -> int:
    params = StdioServerParameters(command=RUN, args=[])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"✅ initialize OK — server: {init.serverInfo.name} v{init.serverInfo.version}")

            tools = (await session.list_tools()).tools
            print(f"\n✅ tools/list — {len(tools)} tools:")
            for t in tools:
                ann = t.annotations
                ro = "RO" if ann and ann.readOnlyHint else "RW"
                print(f"   [{ro}] {t.name:<24} {short(t.description or '', 60)}")

            tool_names = {t.name for t in tools}
            expected = {"list_my_subscriptions", "list_recent_episodes", "get_episode",
                        "get_transcript_url", "search", "list_play_history"}
            missing = expected - tool_names
            if missing:
                print(f"❌ missing tools: {missing}")
                return 1

            print("\n--- exercising each tool ---")

            subs = _unwrap(await session.call_tool("list_my_subscriptions", {}))
            assert isinstance(subs, list) and subs, "subscriptions empty"
            pid = subs[0]["pid"]
            print(f"✅ list_my_subscriptions → {len(subs)} podcasts, first: {subs[0]['title']!r}")

            eps = _unwrap(await session.call_tool("list_recent_episodes", {"pid": pid, "limit": 3}))
            assert eps and "audio_url" in eps[0] and "media_id" in eps[0]
            eid, mid = eps[0]["eid"], eps[0]["media_id"]
            print(f"✅ list_recent_episodes(pid, 3) → {len(eps)} episodes, first: {eps[0]['title']!r}")

            ep = _unwrap(await session.call_tool("get_episode", {"eid": eid}))
            assert "shownotes_html" in ep
            print(f"✅ get_episode(eid) → title={ep['title']!r} duration={ep['duration_seconds']}s")

            t = _unwrap(await session.call_tool("get_transcript_url", {"eid": eid, "media_id": mid}))
            print(f"✅ get_transcript_url → status={t['status']!r} url={'present' if t['transcript_url'] else 'absent'}")

            sr = _unwrap(await session.call_tool("search", {"query": "AI Agent", "kind": "PODCAST", "limit": 3}))
            print(f"✅ search('AI Agent', PODCAST, 3) → {len(sr)} hits")

            hist = _unwrap(await session.call_tool("list_play_history", {"limit": 5}))
            print(f"✅ list_play_history(5) → {len(hist)} items")

            print("\n--- error path test (deliberate bad eid) ---")
            r = await session.call_tool("get_episode", {"eid": "0" * 24})
            if r.isError:
                err_text = r.content[0].text if r.content else "<no content>"
                print(f"✅ bad eid → isError=true, message={short(err_text, 100)}")
            else:
                print(f"⚠️  bad eid did NOT raise isError (got: {short(r.content[0].text if r.content else '<empty>', 80)})")

            print("\n🎉 all good")
            return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
