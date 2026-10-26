import pytest

from tgtg_jp.config import parse_config


def make_item(item_id="1001", available=2, start="2026-10-26T09:00:00Z", end="2026-10-26T10:00:00Z",
              store="テストベーカリー", minor_units=500):
    return {
        "item": {
            "item_id": item_id,
            "name": "パンの詰め合わせ",
            "price_including_taxes": {"code": "JPY", "minor_units": minor_units, "decimals": 0},
            "value_including_taxes": {"code": "JPY", "minor_units": 1500, "decimals": 0},
        },
        "store": {"store_name": store},
        "pickup_interval": {"start": start, "end": end},
        "pickup_location": {"address": {"address_line": "東京都世田谷区"}},
        "items_available": available,
    }


@pytest.fixture
def config(tmp_path):
    return parse_config({
        "email": "user@example.com",
        "discord": {"webhook_url": "https://discord.com/api/webhooks/1/abc"},
        "polling": {"interval_seconds": 120, "jitter_seconds": 0},
        "state_dir": str(tmp_path),
    })


class FakeNotifier:
    def __init__(self, fail=False):
        self.bags, self.messages, self.fail = [], [], fail

    def bag(self, bag):
        from tgtg_jp.notify import NotifyError
        if self.fail:
            raise NotifyError("送信失敗")
        self.bags.append(bag)

    def message(self, text, error=False):
        self.messages.append(text)


class FakeClient:
    """get_items が返す値（または送出する例外）を順番に再生する。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.access_token, self.refresh_token, self.cookie = "a", "r", "c"
        self.user_agent, self.last_time_token_refreshed = "ua", None

    def get_items(self, **kwargs):
        self.calls += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
