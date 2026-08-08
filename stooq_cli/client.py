"""HTTP session for stooq.com.

Stooq fronts its pages with a small JavaScript proof-of-work check (compute a
SHA-256 nonce, post it to /__verify, receive a session cookie). This client
performs the same handshake a browser does, persists the resulting cookies on
disk, and paces requests politely so the terminal stays a good citizen.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://stooq.com"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MIN_INTERVAL = 0.6  # seconds between requests
CHALLENGE_RE = re.compile(r'const c="([^"]+)",d=(\d+)')


class StooqError(Exception):
    """Base class for stooq client failures with a user-facing message."""


class StooqUnavailable(StooqError):
    """Network problem or unexpected response."""


class StooqBlocked(StooqError):
    """Stooq refused the request (access denied or the browser check failed)."""


class StooqQuotaExceeded(StooqError):
    """Stooq's per-day anonymous request quota is used up. It resets daily;
    cached data remains usable in the meantime."""


class StooqClient:
    def __init__(self, cookie_path: str):
        self._cookie_path = cookie_path
        self._jar = http.cookiejar.MozillaCookieJar(cookie_path)
        if os.path.exists(cookie_path):
            try:
                self._jar.load(ignore_discard=True, ignore_expires=True)
            except OSError:
                pass
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar)
        )
        self._lock = threading.Lock()
        self._last_request = 0.0

    # -- low level ----------------------------------------------------------

    def _save_cookies(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._cookie_path), exist_ok=True)
            self._jar.save(ignore_discard=True, ignore_expires=True)
        except OSError:
            pass

    def _pace(self) -> None:
        wait = self._last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _request(self, url: str, data: bytes | None = None, referer: str | None = None) -> str:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        self._pace()
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with self._opener.open(req, timeout=25) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise StooqUnavailable(f"Stooq returned HTTP {exc.code} for this request.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StooqUnavailable("Could not reach stooq.com. Check your connection.") from exc
        self._save_cookies()
        return body

    @staticmethod
    def _solve(prefix: str, difficulty: int) -> int:
        target = "0" * difficulty
        nonce = 0
        encoded = prefix.encode()
        while True:
            digest = hashlib.sha256(encoded + str(nonce).encode()).hexdigest()
            if digest.startswith(target):
                return nonce
            nonce += 1

    def _verify(self, challenge: str, difficulty: int, referer: str) -> None:
        nonce = self._solve(challenge, difficulty)
        data = urllib.parse.urlencode({"c": challenge, "n": nonce}).encode()
        try:
            self._request(f"{BASE}/__verify", data=data, referer=referer)
        except StooqUnavailable:
            # The verify endpoint rate-limits; the retry loop in get() handles it.
            pass

    # -- public -------------------------------------------------------------

    def get(self, url: str, referer: str | None = None) -> str:
        """Fetch a page, transparently completing the browser check if shown."""
        body = self._request(url, referer=referer)
        for attempt in range(3):
            match = CHALLENGE_RE.search(body)
            if not match:
                break
            self._verify(match.group(1), int(match.group(2)), url)
            time.sleep(1.0 + attempt)
            body = self._request(url, referer=referer)
        if CHALLENGE_RE.search(body):
            raise StooqBlocked("Stooq's browser check did not clear. Try again in a minute.")
        stripped = body.strip()
        if stripped.lower().startswith("access denied"):
            raise StooqBlocked("Stooq denied this request.")
        # Stooq hides data once the anonymous daily quota is spent. The notice
        # sits inside the page body, not at the top, so scan the whole document.
        if "exceeded the daily site hits limit" in body.lower():
            raise StooqQuotaExceeded(
                "Stooq's daily request limit for this address has been reached. "
                "It resets tomorrow; cached symbols still load."
            )
        return body

    def quote_history_page(self, symbol: str, d1: str, d2: str, page: int) -> str:
        url = (
            f"{BASE}/q/d/?s={urllib.parse.quote(symbol)}"
            f"&d1={d1}&d2={d2}&i=d&l={page}"
        )
        return self.get(url, referer=f"{BASE}/")

    def category_page(self, category_id: int, page: int = 1) -> str:
        url = f"{BASE}/t/?i={category_id}&v=0&l={page}"
        return self.get(url, referer=f"{BASE}/")

    def search(self, query: str) -> str:
        url = f"{BASE}/cmp/?q={urllib.parse.quote(query)}"
        return self.get(url, referer=f"{BASE}/")
