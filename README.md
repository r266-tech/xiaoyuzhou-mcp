# xiaoyuzhou-mcp

> Read-only MCP server for **Xiaoyuzhou FM (小宇宙 FM / 小宇宙播客)** — list subscriptions, browse episodes, fetch official transcript URLs, search podcasts. Agent-friendly, clean, distributable.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/Model_Context_Protocol-1.0%2B-blue)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/python-3.11%2B-green.svg)](https://www.python.org/)

## What this is

`xiaoyuzhou-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server that lets any MCP-compatible agent (Claude Code, Codex, Claude Desktop, Cline, Continue, ...) read a user's Xiaoyuzhou FM podcast data:

- 我的订阅 / my subscribed podcasts
- 最新单集 / recent episodes of a podcast
- 单集详情 + shownotes
- 官方字幕 URL (for transcript-based summarization / RAG)
- 搜索播客 / 单集 (search podcasts or episodes)
- 播放历史 / play history

All tools are **read-only by design**. No subscribe / unsubscribe / comment / mark-played endpoints are exposed — agents can recommend but never act on the user's behalf without explicit human approval.

## Why this exists

When a user asks an agent things like *"check my 小宇宙 subscriptions for new episodes"* or *"summarize this 小宇宙 podcast"*, today's options are bad:

- Xiaoyuzhou has no official public API.
- Existing third-party Go client ([MosesHe/xiaoyuzhoufm-mcp](https://github.com/MosesHe/xiaoyuzhoufm-mcp)) doesn't expose subscriptions or audio URLs.
- Directly scraping the website is fragile and leaks browser/auth state into the agent loop.

This server wraps the real Xiaoyuzhou app API with proper Android headers, transparent SMS-based auth, atomic token storage, and LLM-friendly output schemas — so agents get clean data and users get a working integration.

## When to use this MCP

Trigger this server whenever the user mentions any of these (Chinese or English):

- 小宇宙 / 小宇宙 FM / 小宇宙播客 / Xiaoyuzhou / Xiaoyuzhoufm
- 我的播客订阅 / 订阅了什么播客 / podcast subscriptions
- 最近更新 / 新单集 / 有没有新的播客 / new episodes
- 播客字幕 / 播客转文字 / podcast transcript / podcast subtitles
- 搜播客 / 找播客 / search podcast / search episode
- 我最近听了什么 / 播放历史 / play history

If the user is asking about **Apple Podcasts, Spotify, or generic podcasts**, this is NOT the right tool — this server only talks to Xiaoyuzhou.

## Tools

All six tools are read-only (`readOnlyHint=true`, `destructiveHint=false`).

| Tool | Purpose |
|------|---------|
| `list_my_subscriptions()` | The user's subscribed podcasts, newest-subscribed first. Each item has `pid`, `title`, `author`, `latest_episode_pub_date`, `has_unread`. |
| `list_recent_episodes(pid, limit=20)` | Recent episodes of a podcast (newest first). Returns `eid`, `media_id`, `audio_url`, `shownotes_html`, `duration_seconds`, ... |
| `get_episode(eid)` | Full detail of one episode including `shownotes_html` (raw HTML — treat as untrusted). |
| `get_transcript_url(eid, media_id)` | Signed URL to the official subtitle JSON, or `status: 'no_subtitle'`. Does **not** fetch the transcript itself — caller decides. |
| `search(query, kind='PODCAST', limit=20)` | Search podcasts or episodes by keyword. `kind` is `'PODCAST'` or `'EPISODE'`. |
| `list_play_history(limit=20)` | Recently played episodes with `is_played` / `is_finished` flags. |

Output schemas are **shallow** (no nested wrappers), use **snake_case**, and prefer **ISO 8601** timestamps so LLMs can reason about them without preprocessing.

## Quick start

### 1. Install

```bash
git clone https://github.com/r266-tech/xiaoyuzhou-mcp.git
cd xiaoyuzhou-mcp
pip install -e .
```

### 2. Login (one time, SMS)

```bash
python scripts/login.py --phone 13800138000
# enter the SMS code when prompted
```

Token is stored at `~/cc-workspace/state/xiaoyuzhou/token.json` with `chmod 0600`. Override the location via the `XIAOYUZHOU_STATE_DIR` env var if you don't use the `cc-workspace` convention.

Two-step variant (useful for headless / CI flows):

```bash
python scripts/login.py --send 13800138000
python scripts/login.py --verify 13800138000 123456
```

### 3. Register with your agent

**Claude Code** — `~/.claude.json`:

```json
{
  "mcpServers": {
    "xiaoyuzhou-mcp": {
      "command": "/absolute/path/to/xiaoyuzhou-mcp/run"
    }
  }
}
```

**Codex** — `~/.codex/config.toml`:

```toml
[mcp_servers.xiaoyuzhou-mcp]
command = "/absolute/path/to/xiaoyuzhou-mcp/run"
```

**Claude Desktop** — `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "xiaoyuzhou-mcp": {
      "command": "/absolute/path/to/xiaoyuzhou-mcp/run"
    }
  }
}
```

Restart the agent. The six tools become available.

### 4. Verify

```bash
python scripts/verify.py        # direct client smoke test
python scripts/e2e_test.py      # full stdio MCP roundtrip (spawn + JSON-RPC)
```

## Design notes

- **Agent-first output**: shallow dicts, snake_case field names, ISO timestamps, no nested wrapper layers like `{data: {data: [...]}}`.
- **Transparent auth**: on 401 the client refreshes once and retries; tools never see auth state. 4xx during refresh clears the token (re-login required); network errors leave state alone.
- **Atomic token writes**: `fcntl.flock` + uuid'd `.tmp` file + `os.replace` → no half-written tokens even with concurrent agents. Parent dir is `0o700`, file is `0o600`.
- **Distributable**: no hardcoded `/Users/...` paths, no homebrew assumptions; runs on Intel Mac, Apple Silicon, and Linux. Use `$XIAOYUZHOU_STATE_DIR` or `$BABATA_STATE_DIR` to relocate state.
- **Read-only on purpose**: write endpoints (subscribe, comment, mark-played) exist on the upstream API but are deliberately not exposed. Agents shouldn't be performing public actions on the user's behalf without explicit human confirmation.

## Gotchas

- **Android UA is required**. The transcript endpoint validates User-Agent strictly — iOS UA returns 400. `_app_headers()` already uses the correct Xiaomi MI 6 / Android 28 UA.
- **`get_episode` is GET + query string**, other authenticated endpoints are POST + JSON body. This is mirrored from the real app behavior.
- **`list_play_history` does NOT return precise played-seconds** — upstream only exposes `is_played` / `is_finished` boolean flags.

## Acknowledgments

Endpoint surface was originally mapped against the unofficial Go client [MosesHe/xiaoyuzhoufm-mcp](https://github.com/MosesHe/xiaoyuzhoufm-mcp) and the [xyz-dl](https://github.com/xyz-dl) project — thanks to both.

## License

[MIT](LICENSE) — see the file for full text.

---

<!-- babata-star-callout-v2 -->
## If this saved you time

Starring the repo helps me prioritize which integrations to keep maintained. This project is part of [babata](https://github.com/r266-tech) — a personal, macOS-native AI infrastructure stack.
