import pytest
from conftest import make_item

from tgtg_jp.notify import DiscordNotifier, NotifyError, bag_embed
from tgtg_jp.watcher import parse_bag

URL = "https://discord.com/api/webhooks/1/secret-token"


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


class Session:
    def __init__(self, *responses):
        self.responses, self.payloads = list(responses), []

    def post(self, url, json, timeout):
        self.payloads.append(json)
        return self.responses.pop(0)


def test_embed_contents():
    embed = bag_embed(parse_bag(make_item()), "Asia/Tokyo")
    assert "テストベーカリー" in embed["title"]
    values = {f["name"]: f["value"] for f in embed["fields"]}
    assert values["残り"] == "2 個"
    assert values["価格"] == "¥500（通常 ¥1,500）"
    assert values["受取時間"] == "10/26(月) 18:00〜19:00"


def test_retries_once_on_discord_rate_limit():
    waits = []
    session = Session(Resp(429, {"retry_after": 1.5}), Resp(204))
    DiscordNotifier(URL, session=session, sleep=waits.append).bag(parse_bag(make_item()))
    assert waits == [1.5]
    assert len(session.payloads) == 2


def test_error_message_hides_webhook_token():
    session = Session(Resp(404))
    with pytest.raises(NotifyError) as info:
        DiscordNotifier(URL, session=session).message("x")
    assert "secret-token" not in str(info.value)


def test_requires_webhook():
    with pytest.raises(NotifyError):
        DiscordNotifier("")
