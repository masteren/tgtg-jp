"""デモモード: TGTG に接続せず、架空の店舗データで監視と在庫ボードを動かす。

本番と同じ Monitor / StockTracker / 在庫ボードを使い、API クライアントだけを
シミュレーションに差し替える。入荷・売り切れ・再入荷が時間とともに起き、
--scenario を指定するとアクセス拒否（403）やレート制限（429）も再現できる。
"""

from __future__ import annotations

import datetime as dt
import random
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config, WebConfig, parse_config

TZ = ZoneInfo("Asia/Tokyo")
SCENARIOS = ("normal", "blocked", "ratelimit")


@dataclass(frozen=True)
class DemoStore:
    item_id: str
    store: str
    bag: str
    price: int
    value: int
    pickup: tuple[str, str]  # 受取時間（日本時間 HH:MM）
    address: str


# すべて架空の店舗です。
STORES = (
    DemoStore("900001", "パン工房こむぎ堂", "パンの詰め合わせ", 500, 1500, ("19:00", "20:00"), "東京都世田谷区（デモ）"),
    DemoStore("900002", "喫茶ひだまり", "焼き菓子とサンドイッチ", 450, 1350, ("17:30", "18:30"), "東京都世田谷区（デモ）"),
    DemoStore("900003", "惣菜キッチンまるや", "お惣菜セット", 600, 1800, ("20:00", "21:00"), "東京都世田谷区（デモ）"),
    DemoStore("900004", "寿司割烹しおさい", "握りの詰め合わせ", 800, 2400, ("21:00", "21:30"), "東京都世田谷区（デモ）"),
    DemoStore("900005", "ベーグルと珈琲 ルポ", "ベーグル 4 個", 400, 1200, ("18:00", "19:00"), "東京都世田谷区（デモ）"),
    DemoStore("900006", "八百屋あおば", "旬の野菜", 350, 1000, ("16:00", "18:00"), "東京都世田谷区（デモ）"),
    DemoStore("900007", "洋菓子店ミルフイユ", "ケーキの詰め合わせ", 700, 2100, ("19:30", "20:30"), "東京都世田谷区（デモ）"),
    DemoStore("900008", "カレー食堂スパイス舎", "カレーとナン", 550, 1650, ("20:30", "21:30"), "東京都世田谷区（デモ）"),
)


class DemoAPIError(Exception):
    """tgtg-python の例外と同じ (status, content) 形式。"""


def _pickup_window(hhmm: tuple[str, str], now: dt.datetime) -> tuple[str, str]:
    """今日の受取時間がもう過ぎていれば明日にする。UTC の ISO 文字列で返す。"""
    local = now.astimezone(TZ)
    (sh, sm), (eh, em) = (map(int, t.split(":")) for t in hhmm)
    start = local.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = local.replace(hour=eh, minute=em, second=0, microsecond=0)
    if end <= local:
        start, end = start + dt.timedelta(days=1), end + dt.timedelta(days=1)
    utc = dt.timezone.utc
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.astimezone(utc).strftime(fmt), end.astimezone(utc).strftime(fmt)


class DemoClient:
    """get_items のたびに在庫が少しずつ変化する、TgtgClient の代役。"""

    RESTOCK_PROBABILITY = 0.2
    SELL_PROBABILITY = 0.35

    def __init__(self, scenario: str = "normal", seed: int | None = None, now=None):
        if scenario not in SCENARIOS:
            raise ValueError(f"scenario は {', '.join(SCENARIOS)} のいずれかです。")
        self.scenario = scenario
        self.rng = random.Random(seed)
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self.calls = 0
        # 最初は 3 店舗に在庫がある状態から始める
        self.stock = {s.item_id: 0 for s in STORES}
        for store in self.rng.sample(STORES, 3):
            self.stock[store.item_id] = self.rng.randint(1, 4)

    def _maybe_fail(self) -> None:
        if self.scenario == "blocked" and self.calls >= 4:
            raise DemoAPIError(403, b"datadome (demo)")
        if self.scenario == "ratelimit" and self.calls in (3, 4):
            raise DemoAPIError(429, b"")

    def _step(self) -> None:
        for item_id, left in self.stock.items():
            if left == 0:
                if self.rng.random() < self.RESTOCK_PROBABILITY:
                    self.stock[item_id] = self.rng.randint(1, 4)
            elif self.rng.random() < self.SELL_PROBABILITY:
                self.stock[item_id] = left - 1

    def backdate(self, tracker) -> None:
        """起動時から在庫のある出品を「少し前に入荷した」ことにする（入荷時刻の表示を見せるため）。"""
        from .watcher import parse_bags

        now = self.now()
        for bag in parse_bags(self._items(now)):
            if bag.available > 0:
                since = now - dt.timedelta(minutes=self.rng.randint(5, 150))
                tracker.seen[bag.key] = {"since": since.isoformat(), "notified": True}

    def get_items(self, *, page=1, page_size=20, **_):
        self.calls += 1
        self._maybe_fail()
        if page > 1:
            return []
        if self.calls > 1:
            self._step()
        return self._items(self.now())

    def _items(self, now: dt.datetime) -> list[dict]:
        items = []
        for s in STORES:
            start, end = _pickup_window(s.pickup, now)
            items.append({
                "item": {
                    "item_id": s.item_id,
                    "name": s.bag,
                    "price_including_taxes": {"code": "JPY", "minor_units": s.price, "decimals": 0},
                    "value_including_taxes": {"code": "JPY", "minor_units": s.value, "decimals": 0},
                },
                "store": {"store_name": s.store},
                "pickup_interval": {"start": start, "end": end},
                "pickup_location": {"address": {"address_line": s.address}},
                "items_available": self.stock[s.item_id],
            })
        return items


def demo_config(interval: int, host: str, port: int) -> Config:
    """デモ用の設定。状態は一時ディレクトリに置き、本番のデータには触れない。"""
    base = parse_config({"email": "demo@example.com"})
    return replace(
        base,
        interval_seconds=interval,  # 外部に送信しないので 60 秒の下限は適用しない
        jitter_seconds=0,
        quiet_hours=None,
        state_dir=Path(tempfile.mkdtemp(prefix="tgtg-jp-demo-")),
        web=WebConfig(enabled=True, host=host, port=port, password=""),
        demo=True,
    )
