"""Polite HTTP GET for registry lookups: generic User-Agent, rate-limited, cached on disk.

No personal data is sent: the User-Agent names the tool only, and no ``mailto`` is added.
Responses with status 200 or 404 are cached forever under the project's cache directory
(delete it to refresh); transient failures (429, 5xx, network errors) are retried with
backoff and never cached.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .config import Config

_last_request: dict[str, float] = {}


def _urlopen_bytes(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        return -1, b""


def _urlopen(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace") if e.fp else ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return -1, str(e)


class Http:
    def __init__(self, cfg: Config, sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.sleep = sleep
        self.cache: Path = cfg.cache_path
        self.requests_made = 0

    def _wait(self, host: str) -> None:
        last = _last_request.get(host)
        if last is not None:
            gap = time.monotonic() - last
            if gap < self.cfg.min_interval:
                self.sleep(self.cfg.min_interval - gap)
        _last_request[host] = time.monotonic()

    def get(self, url: str, offline: bool = False) -> tuple[int, str]:
        fp = self.cache / f"{hashlib.sha256(url.encode()).hexdigest()[:32]}.json"
        if fp.exists():
            d = json.loads(fp.read_text(encoding="utf-8"))
            return d["status"], d["body"]
        if offline:
            return -2, "offline and not cached"
        host = urllib.parse.urlparse(url).netloc
        headers = {"User-Agent": self.cfg.user_agent, "Accept": "application/json"}
        status, body = -1, ""
        for attempt in range(4):
            self._wait(host)
            self.requests_made += 1
            status, body = _urlopen(url, headers, 30)
            if status in (429, 500, 502, 503, 504, -1):
                self.sleep(2.0 * (attempt + 1))
                continue
            break
        if status in (200, 404):
            self.cache.mkdir(parents=True, exist_ok=True)
            shown = re.sub(r"([?&]email=)[^&]*", r"\1<redacted>", url)
            fp.write_text(json.dumps({"url": shown, "status": status, "body": body}),
                          encoding="utf-8")
        return status, body


def download(http: Http, url: str, dest: Path) -> tuple[bool, str]:
    """Save ``url`` to ``dest`` if it really is a PDF. Never overwrites an existing file."""
    if dest.exists():
        return False, f"{dest.name} already exists; left as is"
    http._wait(urllib.parse.urlparse(url).netloc)
    status, data = _urlopen_bytes(url, {"User-Agent": http.cfg.user_agent}, 60)
    if status != 200:
        return False, f"download failed (HTTP {status}); open the link by hand"
    if not data.startswith(b"%PDF"):
        return False, "link did not return a PDF (likely a landing page); open it by hand"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True, f"saved {dest.name} ({len(data) // 1024} KiB)"
