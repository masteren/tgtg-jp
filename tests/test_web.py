import base64
import datetime as dt
import json
import urllib.error
import urllib.request

import pytest
from conftest import FakeClient, make_item

from tgtg_jp.config import ConfigError, parse_config
from tgtg_jp.monitor import Monitor, Status, StatusBoard
from tgtg_jp.watcher import StockTracker, parse_bags
from tgtg_jp.web import ago_label, pickup_label, render_page, start_server

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 26, 6, 0, tzinfo=UTC)  # 15:00 JST


def status_with(items, **kw):
    bags = tuple(parse_bags(items))
    since = {b.key: NOW - dt.timedelta(minutes=12) for b in bags if b.available}
    return Status(state="ok", checked_at=NOW, bags=bags, since=since, **kw)


def test_page_lists_in_stock_first_and_hides_sold_out(config):
    html = render_page(status_with([make_item("1", store="品切れ屋", available=0),
                                    make_item("2", store="パン屋")]), config, NOW)
    assert '<span class="count">1</span> 店に在庫があります' in html
    assert html.index("パン屋") < html.index("在庫なしのお気に入り（1）")
    assert "今日 18:00〜19:00 に受取" in html
    assert "12 分前に入荷" in html
    assert "¥500" in html and "¥1,500" in html


def test_page_escapes_store_names(config):
    html = render_page(status_with([make_item(store="<script>alert(1)</script>")]), config, NOW)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_page_states(config):
    assert "最初の確認を待っています" in render_page(Status(), config, NOW)
    assert "いまは在庫がありません" in render_page(status_with([]), config, NOW)
    stopped = render_page(Status(state="stopped", message="HTTP 403: ブロック", checked_at=NOW),
                          config, NOW)
    assert "確認を停止しました" in stopped and "HTTP 403" in stopped
    assert "在庫を確認できません" in stopped and "お気に入り登録" not in stopped


def test_labels():
    bag = parse_bags([make_item(start="2026-10-27T09:00:00Z", end="2026-10-27T10:00:00Z")])[0]
    assert pickup_label(bag, NOW, "Asia/Tokyo") == "明日 18:00〜19:00 に受取"
    assert ago_label(NOW, NOW) == "たった今入荷"
    assert ago_label(NOW - dt.timedelta(minutes=135), NOW) == "2 時間前に入荷"


def test_monitor_without_notifier_updates_board(config):
    board = StatusBoard()
    monitor = Monitor(config, FakeClient([[make_item()]]), None, StockTracker(), board=board)
    assert monitor.poll_once() == 0
    status = board.get()
    assert status.state == "ok" and len(status.bags) == 1 and status.since


def _get(url, password=None):
    req = urllib.request.Request(url)
    if password:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"me:{password}".encode()).decode())
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.headers, resp.read().decode()


def _serve(config, status):
    board = StatusBoard()
    board.set(**{k: getattr(status, k) for k in status.__dataclass_fields__})
    server = start_server(board, config)
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_http_server_serves_page_and_json(tmp_path):
    cfg = parse_config({"email": "a@b.c", "state_dir": str(tmp_path), "web": {"port": 18787}})
    server, base = _serve(cfg, status_with([make_item()]))
    try:
        code, headers, body = _get(base + "/")
        assert code == 200 and "在庫があります" in body
        assert headers["Cache-Control"] == "no-store"
        _, _, raw = _get(base + "/api/status")
        assert json.loads(raw)["bags"][0]["price"] == "¥500"
    finally:
        server.shutdown()


def test_http_server_requires_password_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("TGTG_JP_WEB_PASSWORD", "correct-horse-battery")
    cfg = parse_config({"email": "a@b.c", "state_dir": str(tmp_path), "web": {"port": 18788}})
    server, base = _serve(cfg, status_with([]))
    try:
        with pytest.raises(urllib.error.HTTPError) as info:
            _get(base + "/")
        assert info.value.code == 401
        with pytest.raises(urllib.error.HTTPError):
            _get(base + "/", "wrong")
        assert _get(base + "/", "correct-horse-battery")[0] == 200
        assert _get(base + "/healthz")[0] == 200
    finally:
        server.shutdown()


def test_public_host_needs_strong_password(monkeypatch):
    monkeypatch.delenv("TGTG_JP_WEB_PASSWORD", raising=False)
    with pytest.raises(ConfigError, match="TGTG_JP_WEB_PASSWORD"):
        parse_config({"email": "a@b.c", "web": {"host": "0.0.0.0"}})
    monkeypatch.setenv("TGTG_JP_WEB_PASSWORD", "long-enough-pass")
    assert parse_config({"email": "a@b.c", "web": {"host": "0.0.0.0"}}).web.host == "0.0.0.0"
