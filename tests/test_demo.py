import datetime as dt

import pytest

from tgtg_jp.demo import STORES, DemoClient, _pickup_window, demo_config
from tgtg_jp.monitor import EXIT_ACCESS_DENIED, Monitor, StatusBoard
from tgtg_jp.watcher import StockTracker, parse_bags
from tgtg_jp.web import render_page

UTC = dt.timezone.utc
NOON_JST = dt.datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


def test_items_parse_like_real_api():
    bags = parse_bags(DemoClient(seed=1, now=lambda: NOON_JST).get_items())
    assert len(bags) == len(STORES)
    assert all(b.price and b.price.startswith("¥") for b in bags)
    assert sum(b.available > 0 for b in bags) == 3


def test_stock_changes_over_time():
    client = DemoClient(seed=7, now=lambda: NOON_JST)
    snapshots = {tuple(i["items_available"] for i in client.get_items()) for _ in range(20)}
    assert len(snapshots) > 5  # 入荷・売り切れが起きている
    assert all(n >= 0 for snap in snapshots for n in snap)


def test_pickup_moves_to_tomorrow_after_window():
    late = dt.datetime(2026, 10, 26, 13, 0, tzinfo=UTC)  # 22:00 JST
    start, _ = _pickup_window(("19:00", "20:00"), late)
    assert start == "2026-10-27T10:00:00Z"
    start, _ = _pickup_window(("19:00", "20:00"), NOON_JST)
    assert start == "2026-10-26T10:00:00Z"


def run_demo(scenario, polls=6):
    config = demo_config(interval=3, host="127.0.0.1", port=18890)
    client = DemoClient(scenario, seed=3)
    board = StatusBoard()
    monitor = Monitor(config, client, None, StockTracker(config.notified_path), board=board)

    def fake_wait(_seconds):
        if client.calls >= polls:
            monitor.stop_requested = True

    monitor._wait = fake_wait
    return monitor.run(), client, board, config


def test_normal_scenario_keeps_running():
    code, client, board, _ = run_demo("normal")
    assert code == 0 and client.calls == 6
    assert board.get().state == "ok"


def test_blocked_scenario_stops_safely():
    code, client, board, config = run_demo("blocked")
    assert code == EXIT_ACCESS_DENIED
    assert client.calls == 4  # 403 の後は一度もリクエストしない
    status = board.get()
    assert status.state == "stopped" and "403" in status.message
    html = render_page(status, config)
    assert "確認を停止しました" in html and "デモ" in html


def test_ratelimit_scenario_backs_off_and_recovers():
    code, client, board, _ = run_demo("ratelimit")
    assert code == 0 and client.calls == 6
    assert board.get().state == "ok"


def test_demo_config_is_isolated():
    config = demo_config(interval=3, host="127.0.0.1", port=18891)
    assert config.demo and config.interval_seconds == 3
    assert "tgtg-jp-demo-" in str(config.state_dir)
    assert "デモ（架空のデータ）" in render_page(StatusBoard().get(), config)


def test_backdate_gives_initial_stock_earlier_arrivals():
    client = DemoClient(seed=5, now=lambda: NOON_JST)
    tracker = StockTracker()
    client.backdate(tracker)
    bags = [b for b in parse_bags(client.get_items()) if b.available]
    assert tracker.update(bags, NOON_JST) == []  # 起動時の在庫は「新規入荷」扱いしない
    assert all(tracker.since(b) < NOON_JST for b in bags)
    assert client.calls == 1  # backdate はリクエスト数に数えない


def test_stopped_board_marks_stale_stock():
    _, _, board, config = run_demo("blocked")
    html = render_page(board.get(), config)
    assert "在庫がありました" in html and "停止前" in html


def test_unknown_scenario_rejected():
    with pytest.raises(ValueError):
        DemoClient("unknown")
