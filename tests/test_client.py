"""Client behaviour: proof-of-work solving and refusal detection."""

import hashlib
from unittest.mock import patch

import pytest

from stooq_cli.client import (
    StooqBlocked,
    StooqClient,
    StooqQuotaExceeded,
)

CHALLENGE_PAGE = (
    '<html><body><script>(async()=>{const c="ABC123",d=2,t="0".repeat(d);'
    "})();</script></body></html>"
)

QUOTA_PAGE = (
    "<html><body><table><tr><td><b>Historical values of GOLD.US</b></td></tr></table>"
    "<span style=color:#f00>Exceeded the daily site hits limit<br>"
    "The data has been hidden</span></body></html>"
)


@pytest.fixture
def client(tmp_path):
    return StooqClient(str(tmp_path / "cookies.txt"))


def test_solve_finds_valid_nonce():
    nonce = StooqClient._solve("ABC123", 2)
    assert hashlib.sha256(f"ABC123{nonce}".encode()).hexdigest().startswith("00")


def test_challenge_is_solved_then_page_returned(client):
    pages = [CHALLENGE_PAGE, "<html>real content</html>"]

    def fake_request(url, data=None, referer=None):
        if data is not None:  # the /__verify post
            return ""
        return pages.pop(0) if pages else "<html>real content</html>"

    with patch.object(client, "_request", side_effect=fake_request), patch(
        "stooq_cli.client.time.sleep"
    ):
        body = client.get("https://stooq.com/anything")
    assert "real content" in body


def test_quota_exceeded_detected_deep_in_page(client):
    with patch.object(client, "_request", return_value=QUOTA_PAGE):
        with pytest.raises(StooqQuotaExceeded):
            client.get("https://stooq.com/q/d/?s=gold.us")


def test_access_denied_detected(client):
    with patch.object(client, "_request", return_value="Access denied"):
        with pytest.raises(StooqBlocked):
            client.get("https://stooq.com/q/d/?s=x")


def test_unsolvable_challenge_gives_up(client):
    with patch.object(client, "_request", return_value=CHALLENGE_PAGE), patch(
        "stooq_cli.client.time.sleep"
    ):
        with pytest.raises(StooqBlocked):
            client.get("https://stooq.com/anything")


def test_normal_page_passes_through(client):
    with patch.object(client, "_request", return_value="<html>fine</html>"):
        assert client.get("https://stooq.com/") == "<html>fine</html>"
