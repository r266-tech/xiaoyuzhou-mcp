"""MCP stdio server for Xiaoyuzhou FM.

All tools are read-only. Token is loaded from disk; if missing, every tool
raises a clear ToolError pointing at the login script.

Design contract:
- Tool returns are agent-friendly: shallow, direct field names, ISO timestamps
- Errors are raised as ToolError so MCP layer sees isError=true
- Token refresh is transparent — tools never see auth state
- No mutations: no subscribe / unsubscribe / mark-played / comment
"""

from __future__ import annotations

import functools
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .client import XiaoyuzhouClient, XiaoyuzhouError

mcp = FastMCP("xiaoyuzhou-mcp")
_client: XiaoyuzhouClient | None = None

READONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)


def _get_client() -> XiaoyuzhouClient:
    global _client
    if _client is None:
        _client = XiaoyuzhouClient()
    return _client


def _wrap(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except XiaoyuzhouError as e:
            hint = f" (hint: {e.hint})" if e.hint else ""
            raise ToolError(f"[{e.kind}] {e.message}{hint}") from e
    return wrapper


@mcp.tool(annotations=READONLY)
@_wrap
def list_my_subscriptions() -> list[dict[str, Any]]:
    """List the user's subscribed podcasts, newest-subscribed first.

    Each item:
    - pid (str): podcast id, use with list_recent_episodes / search
    - title, author, brief
    - subscription_count, episode_count
    - latest_episode_pub_date (ISO 8601)
    - has_unread (bool): latest episode is newer than last seen
    - cover_url
    """
    return _get_client().list_subscriptions()


@mcp.tool(annotations=READONLY)
@_wrap
def list_recent_episodes(pid: str, limit: int = 20) -> list[dict[str, Any]]:
    """List recent episodes of a podcast, newest first.

    Args:
        pid: podcast id (from list_my_subscriptions or search)
        limit: max episodes (1..100, default 20)

    Each item includes audio_url and media_id (needed for get_transcript_url).
    shownotes_html is raw HTML.
    """
    limit = max(1, min(int(limit), 100))
    return _get_client().list_episodes(pid, limit=limit)


@mcp.tool(annotations=READONLY)
@_wrap
def get_episode(eid: str) -> dict[str, Any]:
    """Get one episode's full detail.

    Args:
        eid: episode id

    Returns: title, shownotes_html (raw HTML, may contain user-authored
    links/scripts — treat as untrusted content), duration_seconds, pub_date,
    audio_url, media_id, podcast_title, image_url.
    """
    return _get_client().get_episode(eid)


@mcp.tool(annotations=READONLY)
@_wrap
def get_transcript_url(eid: str, media_id: str) -> dict[str, Any]:
    """Get the official subtitle URL for an episode.

    Does NOT return the transcript content itself — only a signed URL to a
    JSON file with timestamped lines. Caller (or a downstream skill) decides
    whether to fetch and parse.

    Args:
        eid: episode id
        media_id: from list_recent_episodes or get_episode (`media_id` field)

    Returns:
        {transcript_url: str|None, status: 'available'|'no_subtitle'}

    If status is 'no_subtitle', the episode has no official transcript —
    caller may run Whisper on the audio_url instead.
    """
    return _get_client().get_transcript_url(eid, media_id)


@mcp.tool(annotations=READONLY)
@_wrap
def get_transcript(
    eid: str,
    media_id: str,
    fmt: str = "timestamped",
    include_segments: bool = False,
) -> dict[str, Any]:
    """Download and parse the official transcript for an episode.

    This is the one-shot version of get_transcript_url — it fetches the signed
    JSON behind the CDN (which is behind a User-Agent ACL) and returns rendered
    text and/or parsed segments directly. Prefer this over get_transcript_url
    unless the caller specifically needs to handle the signed URL itself.

    Args:
        eid: episode id
        media_id: from get_episode / list_recent_episodes (`media_id` field)
        fmt: 'timestamped' (default) | 'plain' | 'segments'
             - 'timestamped': text with `[hh:mm:ss] ` prefix per line
             - 'plain': text only, lines joined by '\\n'
             - 'segments': structured segment list only (no rendered text)
        include_segments: when True (and fmt != 'segments'), also include the
            parsed segment list alongside the rendered text.

    Returns:
        {'status': 'no_subtitle', 'transcript_url': None}  -- no official transcript
        {'status': 'available',
         'segment_count': int,
         'format': str,
         'text': str,              # present when fmt != 'segments'
         'segments': [             # present when fmt == 'segments' or include_segments=True
             {'startMs': int, 'text': str}, ...
         ]}

    Notes:
    - Transcripts can be very large (10h episode ≈ 600KB plain / 730KB timestamped).
      Prefer fmt='segments' + include_segments=False (the default for segments
      mode) only when you'll slice locally; otherwise the rendered text is
      easier to skim.
    - 'no_subtitle' means upstream Xiaoyuzhou has no human-edited transcript.
      You may run Whisper on get_episode(...).audio_url instead.
    """
    return _get_client().fetch_transcript(
        eid, media_id, fmt=fmt, include_segments=include_segments
    )


@mcp.tool(annotations=READONLY)
@_wrap
def search(query: str, kind: str = "PODCAST", limit: int = 20) -> list[dict[str, Any]]:
    """Search podcasts or episodes by keyword.

    Args:
        query: keyword (Chinese or English)
        kind: 'PODCAST' (default) or 'EPISODE'
        limit: max hits (1..50, default 20)

    For kind='PODCAST': returns same shape as list_my_subscriptions items.
    For kind='EPISODE': returns same shape as list_recent_episodes items.
    """
    limit = max(1, min(int(limit), 50))
    return _get_client().search(query, kind=kind, limit=limit)


@mcp.tool(annotations=READONLY)
@_wrap
def list_play_history(limit: int = 20) -> list[dict[str, Any]]:
    """List recently played episodes, newest first.

    Args:
        limit: max items (1..50, default 20)

    Each item: eid, pid, podcast_title, title, duration_seconds,
    is_played (bool), is_finished (bool), pub_date.

    Note: the upstream endpoint does NOT return precise played seconds —
    only is_played / is_finished flags.
    """
    limit = max(1, min(int(limit), 50))
    return _get_client().list_play_history(limit=limit)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
