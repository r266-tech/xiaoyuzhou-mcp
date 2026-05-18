"""Xiaoyuzhou FM client — direct calls to api.xiaoyuzhoufm.com.

Token auth is self-healing: on 401, refresh once and retry. Callers never see
auth state. All public methods return LLM-friendly dicts: shallow nesting,
direct field names, actionable error hints.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.xiaoyuzhoufm.com"
PODCASTER_BASE = "https://podcaster-api.xiaoyuzhoufm.com"

DEFAULT_STATE_DIR = Path(
    os.environ.get("XIAOYUZHOU_STATE_DIR")
    or os.environ.get("BABATA_STATE_DIR", str(Path.home() / "cc-workspace/state"))
) / "xiaoyuzhou"


def _app_headers(access_token: str | None = None, device_id: str | None = None) -> dict[str, str]:
    now = datetime.now()
    local_time = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+0800"
    headers = {
        "Host": "api.xiaoyuzhoufm.com",
        "os": "android",
        "os-version": "28",
        "manufacturer": "Xiaomi",
        "model": "MI 6",
        "resolution": "1080x1920",
        "market": "xiaomi",
        "applicationid": "app.podcast.cosmos",
        "app-version": "2.99.1",
        "app-buildno": "1362",
        "webviewversion": "138.0.7204.179",
        "User-Agent": "Xiaoyuzhou/2.99.1(android 28)",
        "app-permissions": "100100",
        "wificonnected": "false",
        "timezone": "Asia/Shanghai",
        "local-time": local_time,
        "content-type": "application/json;charset=utf-8",
        "Accept-Encoding": "gzip",
    }
    if access_token:
        headers["x-jike-access-token"] = access_token
    if device_id:
        headers["x-jike-device-id"] = device_id
    return headers


def _web_headers() -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://podcaster.xiaoyuzhoufm.com",
        "referer": "https://podcaster.xiaoyuzhoufm.com/",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
        ),
    }


class XiaoyuzhouError(Exception):
    def __init__(self, kind: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        d = {"error": self.kind, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


@dataclass
class Credentials:
    access_token: str | None = None
    refresh_token: str | None = None
    device_id: str | None = None
    uid: str | None = None
    nickname: str | None = None
    saved_at: float = field(default_factory=time.time)

    @classmethod
    def load(cls, path: Path) -> "Credentials":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.saved_at = time.time()
        lock_path = path.with_suffix(path.suffix + ".lock")
        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(self.__dict__, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
            except Exception:
                if tmp.exists():
                    tmp.unlink()
                raise
        os.chmod(path, 0o600)

    def clear(self, path: Path) -> None:
        self.access_token = None
        self.refresh_token = None
        self.uid = None
        self.nickname = None
        if path.exists():
            path.unlink()


class XiaoyuzhouClient:
    """Stateless wrapper. Auth lives in Credentials; client just executes calls."""

    def __init__(
        self,
        creds: Credentials | None = None,
        state_dir: Path | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.state_dir = state_dir or DEFAULT_STATE_DIR
        self.token_path = self.state_dir / "token.json"
        self.creds = creds or Credentials.load(self.token_path)
        if not self.creds.device_id:
            self.creds.device_id = str(uuid.uuid4())
            if self.creds.access_token:
                self.creds.save(self.token_path)
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "XiaoyuzhouClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- Auth ---------------------------------------------------------------

    def send_sms_code(self, phone: str, area_code: str = "+86") -> dict[str, Any]:
        r = self._http.post(
            f"{PODCASTER_BASE}/v1/auth/send-code",
            headers=_web_headers(),
            json={"mobilePhoneNumber": phone, "areaCode": area_code},
        )
        if r.status_code != 200:
            raise XiaoyuzhouError(
                "send_code_failed",
                f"send-code returned {r.status_code}: {r.text[:200]}",
                hint="check phone number format or rate limit",
            )
        return {"ok": True, "phone": phone, "area_code": area_code}

    def login_with_sms(
        self, phone: str, verify_code: str, area_code: str = "+86"
    ) -> dict[str, Any]:
        r = self._http.post(
            f"{PODCASTER_BASE}/v1/auth/login-with-sms",
            headers=_web_headers(),
            json={
                "areaCode": area_code,
                "verifyCode": verify_code,
                "mobilePhoneNumber": phone,
            },
        )
        if r.status_code != 200:
            raise XiaoyuzhouError(
                "login_failed",
                f"login-with-sms returned {r.status_code}: {r.text[:200]}",
                hint="verify code may be wrong or expired",
            )
        access = r.headers.get("x-jike-access-token")
        refresh = r.headers.get("x-jike-refresh-token")
        if not access or not refresh:
            raise XiaoyuzhouError(
                "no_token_in_response",
                "login succeeded but x-jike-access-token / refresh missing in headers",
            )
        user = r.json().get("data", {}).get("user", {})
        self.creds.access_token = access
        self.creds.refresh_token = refresh
        self.creds.uid = user.get("uid")
        self.creds.nickname = user.get("nickname")
        if not self.creds.device_id:
            self.creds.device_id = str(uuid.uuid4())
        self.creds.save(self.token_path)
        return {
            "ok": True,
            "uid": self.creds.uid,
            "nickname": self.creds.nickname,
            "token_saved_to": str(self.token_path),
        }

    def refresh_access_token(self) -> bool:
        """Returns True on success. On credential-invalid (4xx), clears token
        on disk so subsequent calls fail fast. Network errors leave state alone."""
        if not self.creds.refresh_token:
            return False
        headers = _app_headers(device_id=self.creds.device_id)
        headers["x-jike-refresh-token"] = self.creds.refresh_token
        try:
            r = self._http.post(f"{API_BASE}/app_auth_tokens.refresh", headers=headers)
        except httpx.HTTPError:
            return False
        if 400 <= r.status_code < 500:
            self.creds.clear(self.token_path)
            return False
        if r.status_code != 200:
            return False
        try:
            body = r.json()
        except ValueError:
            body = {}
        new_access = r.headers.get("x-jike-access-token") or body.get("x-jike-access-token")
        new_refresh = r.headers.get("x-jike-refresh-token") or body.get("x-jike-refresh-token")
        if not new_access:
            return False
        self.creds.access_token = new_access
        if new_refresh:
            self.creds.refresh_token = new_refresh
        self.creds.save(self.token_path)
        return True

    # -- Authenticated calls (with auto-refresh on 401) ---------------------

    def _api_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        if not self.creds.access_token:
            raise XiaoyuzhouError(
                "not_logged_in", "no access_token", hint="run login_with_sms first"
            )
        url = f"{API_BASE}{path}"
        def _do():
            return self._http.request(
                method,
                url,
                headers=_app_headers(self.creds.access_token, self.creds.device_id),
                json=payload if method.upper() == "POST" else None,
                params=params,
            )
        r = _do()
        if r.status_code == 401:
            if not self.refresh_access_token():
                raise XiaoyuzhouError(
                    "auth_expired",
                    "access_token expired and refresh failed",
                    hint="re-login via SMS",
                )
            r = _do()
        if r.status_code != 200:
            raise XiaoyuzhouError(
                "api_error", f"{path} returned {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    def _api_post(self, path: str, payload: dict | None = None) -> dict:
        return self._api_request("POST", path, payload=payload)

    # -- Domain methods (verification surface) ------------------------------

    def list_subscriptions(self) -> list[dict[str, Any]]:
        """Returns flat list of subscribed podcasts."""
        data = self._api_post(
            "/v1/subscription/list",
            {"limit": "20", "sortOrder": "desc", "sortBy": "subscribedAt"},
        )
        items = data.get("data", [])
        return [_normalize_podcast(p) for p in items]

    def list_episodes(self, pid: str, limit: int = 20) -> list[dict[str, Any]]:
        data = self._api_post(
            "/v1/episode/list", {"pid": pid, "limit": str(limit), "order": "desc"}
        )
        return [_normalize_episode(e) for e in data.get("data", [])]

    def get_episode(self, eid: str) -> dict[str, Any]:
        data = self._api_request("GET", "/v1/episode/get", params={"eid": eid})
        return _normalize_episode(data.get("data", {}))

    def get_transcript_url(self, eid: str, media_id: str) -> dict[str, Any]:
        """Returns {'transcript_url': str|None, 'status': str}."""
        data = self._api_post(
            "/v1/episode-transcript/get", {"eid": eid, "mediaId": media_id}
        )
        inner = data.get("data") or {}
        if isinstance(inner.get("data"), dict):
            inner = inner["data"]
        url = inner.get("transcriptUrl")
        return {
            "transcript_url": url,
            "status": "available" if url else "no_subtitle",
        }

    def list_play_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Recently played episodes (newest first)."""
        data = self._api_post("/v1/episode-played/list-history", {})
        items = data.get("data", [])[:limit]
        return [_normalize_history_item(h) for h in items]

    def search(self, query: str, kind: str = "PODCAST", limit: int = 20) -> list[dict[str, Any]]:
        """kind: 'PODCAST' or 'EPISODE'. Returns up to limit normalized hits."""
        kind = kind.upper()
        if kind not in ("PODCAST", "EPISODE"):
            raise XiaoyuzhouError("bad_kind", f"kind must be PODCAST or EPISODE, got {kind!r}")
        data = self._api_post(
            "/v1/search/create",
            {
                "keyword": query,
                "type": kind,
                "limit": str(limit),
                "sourcePageName": "4",
                "currentPageName": "4",
            },
        )
        items = (data.get("data") or [])[:limit]
        if kind == "PODCAST":
            return [_normalize_podcast(p) for p in items]
        return [_normalize_episode(e) for e in items]


def _normalize_history_item(h: dict) -> dict:
    ep = h.get("episode") or h
    podcast = ep.get("podcast") or {}
    return {
        "eid": ep.get("eid"),
        "pid": podcast.get("pid"),
        "podcast_title": podcast.get("title"),
        "title": ep.get("title"),
        "duration_seconds": ep.get("duration"),
        "is_finished": ep.get("isFinished"),
        "is_played": ep.get("isPlayed"),
        "pub_date": ep.get("pubDate"),
    }


def _normalize_podcast(p: dict) -> dict:
    return {
        "pid": p.get("pid"),
        "title": p.get("title"),
        "author": p.get("author"),
        "brief": p.get("brief"),
        "subscription_count": p.get("subscriptionCount"),
        "episode_count": p.get("episodeCount"),
        "latest_episode_pub_date": p.get("latestEpisodePubDate"),
        "has_unread": (p.get("latestEpisodePubDate") or "")
        > (p.get("readTrackInfo", {}).get("lastSeenAt") or ""),
        "cover_url": (p.get("image") or {}).get("picUrl"),
    }


def _normalize_episode(e: dict) -> dict:
    enclosure = e.get("enclosure") or {}
    media = e.get("media") or {}
    media_source = media.get("source") or {}
    podcast = e.get("podcast") or {}
    return {
        "eid": e.get("eid"),
        "pid": podcast.get("pid"),
        "podcast_title": podcast.get("title"),
        "title": e.get("title"),
        "shownotes_html": e.get("shownotes"),
        "duration_seconds": e.get("duration"),
        "pub_date": e.get("pubDate"),
        "media_id": media.get("id"),
        "audio_url": media_source.get("url") or enclosure.get("url"),
        "image_url": (e.get("image") or {}).get("picUrl"),
    }
