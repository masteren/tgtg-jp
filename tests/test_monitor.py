import datetime as dt

import pytest
from conftest import FakeClient, FakeNotifier, make_item

from tgtg_jp.client import CredentialStore
from tgtg_jp.config import ConfigError, parse_config
from tgtg_jp.monitor import EXIT_ACCESS_DENIED, Monitor, seconds_until_quiet_end
from tgtg_jp.watcher import StockTracker


class TgtgAPIError(Exception):
    """tgtg-python と同じ (status, content) 形式の例外。"""


def run_monitor(config, responses, notifier=None, polls=None, credentials=None):
    client = FakeClient(responses)
    notifier = notifier or FakeNotifier()
    waits = []
    monitor = Monitor(config, client, notifier, StockTracker(), credentials,
                      now=lambda: dt.datetime(2026, 10, 26, 3, 0, tzinfo=dt.timezone.utc))
    polls = polls if polls is not None else len(responses)

    def fake_sleep(seconds):
        waits.append(seconds)
        if client.calls >= polls:
            monitor.stop_requested = True

    monitor.sleep = fake_sleep
    monitor._wait = lambda s: fake_sleep(s)
    return monitor.run(), client, notifier, waits


def test_same_stock_is_notified_once(config):
    code, client, notifier, _ = run_monitor(config, [[make_item()], [make_item()], [make_item()]])
    assert code == 0
    assert client.calls == 3
    assert len(notifier.bags) == 1


def test_access_denied_stops_and_alerts(config):
    code, client, notifier, _ = run_monitor(config, [TgtgAPIError(403, b"datadome"), [make_item()]])
    assert code == EXIT_ACCESS_DENIED
    assert client.calls == 1
    assert notifier.messages and "停止" in notifier.messages[0]


def test_unauthorized_also_stops(config):
    code, _, _, _ = run_monitor(config, [TgtgAPIError(401, b"")])
    assert code == EXIT_ACCESS_DENIED


def test_rate_limit_backs_off_exponentially(config):
    code, _, _, waits = run_monitor(config, [TgtgAPIError(429, b""), TgtgAPIError(429, b""), []])
    assert code == 0
    assert waits[:2] == [240, 480]


def test_network_error_retries(config):
    code, client, notifier, waits = run_monitor(config, [ConnectionError("down"), [make_item()]])
    assert code == 0
    assert waits[0] == 60
    assert len(notifier.bags) == 1


def test_failed_notification_is_retried_next_poll(config):
    notifier = FakeNotifier(fail=True)
    client = FakeClient([[make_item()], [make_item()]])
    monitor = Monitor(config, client, notifier, StockTracker())
    assert monitor.poll_once() == 0
    notifier.fail = False
    assert monitor.poll_once() == 1


def test_refreshed_tokens_are_saved(config, tmp_path):
    store = CredentialStore(tmp_path / "credentials.json")
    client = FakeClient([[]])
    monitor = Monitor(config, client, FakeNotifier(), StockTracker(), store)
    client.access_token = "new-token"
    monitor.poll_once()
    assert store.load()["access_token"] == "new-token"
    assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600


def test_quiet_hours_across_midnight(tmp_path):
    cfg = parse_config({"email": "a@b.c", "quiet_hours": {"start": "23:00", "end": "07:00"},
                        "state_dir": str(tmp_path)})
    utc = dt.timezone.utc
    # 01:30 JST → 07:00 まで 5.5 時間
    assert seconds_until_quiet_end(cfg, dt.datetime(2026, 10, 25, 16, 30, tzinfo=utc)) == 5.5 * 3600
    # 12:00 JST は対象外
    assert seconds_until_quiet_end(cfg, dt.datetime(2026, 10, 26, 3, 0, tzinfo=utc)) == 0


@pytest.mark.parametrize("raw, message", [
    ({"email": "a@b.c", "polling": {"interval_seconds": 10}}, "60"),
    ({"email": ""}, "email"),
    ({"email": "a@b.c", "discord": {"webhook_url": "https://evil.example/hook"}}, "webhook_url"),
    ({"email": "a@b.c", "quiet_hours": {"start": "25:00", "end": "07:00"}}, "HH:MM"),
])
def test_invalid_config(raw, message, monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def test_webhook_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/9/env")
    assert parse_config({"email": "a@b.c"}).discord_webhook_url.endswith("/9/env")
