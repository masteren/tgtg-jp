"""API レスポンスの解析と「通知すべき変化」の判定。ネットワークに依存しない純粋なロジック。"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Bag:
    item_id: str
    store_name: str
    bag_name: str
    available: int
    price: str | None  # 表示用（例: "¥500"）
    original_price: str | None
    pickup_start: dt.datetime | None  # UTC
    pickup_end: dt.datetime | None
    address: str

    @property
    def key(self) -> str:
        # 同じ商品でも受取日が変われば別の出品として扱う。
        start = self.pickup_start.isoformat() if self.pickup_start else "-"
        return f"{self.item_id}@{start}"

    @property
    def share_url(self) -> str:
        return f"https://share.toogoodtogo.com/item/{self.item_id}"

    def pickup_text(self, tz: str) -> str:
        if not self.pickup_start or not self.pickup_end:
            return "不明"
        zone = ZoneInfo(tz)
        start, end = self.pickup_start.astimezone(zone), self.pickup_end.astimezone(zone)
        weekday = "月火水木金土日"[start.weekday()]
        return f"{start:%m/%d}({weekday}) {start:%H:%M}〜{end:%H:%M}"


def format_price(value) -> str | None:
    if not isinstance(value, dict):
        return None
    units, decimals, code = value.get("minor_units"), value.get("decimals"), value.get("code")
    if type(units) is not int or type(decimals) is not int or not isinstance(code, str):
        return None
    amount = Decimal(units) / (Decimal(10) ** decimals)
    if code == "JPY":
        return f"¥{amount:,.0f}"
    return f"{amount:,.{decimals}f} {code}"


def _parse_time(value) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def parse_bag(raw: dict) -> Bag | None:
    item = raw.get("item") or {}
    item_id = str(item.get("item_id") or "").strip()
    if not item_id:
        return None
    store = raw.get("store") or {}
    pickup = raw.get("pickup_interval") or {}
    address = (
        ((raw.get("pickup_location") or {}).get("address") or {}).get("address_line")
        or ((store.get("store_location") or {}).get("address") or {}).get("address_line")
        or ""
    )
    available = raw.get("items_available", 0)
    return Bag(
        item_id=item_id,
        store_name=str(store.get("store_name") or raw.get("display_name") or "不明な店舗"),
        bag_name=str(item.get("name") or ""),
        available=available if type(available) is int and available > 0 else 0,
        price=format_price(item.get("price_including_taxes") or item.get("item_price")),
        original_price=format_price(item.get("value_including_taxes") or item.get("item_value")),
        pickup_start=_parse_time(pickup.get("start")),
        pickup_end=_parse_time(pickup.get("end")),
        address=str(address),
    )


def parse_bags(items: list[dict], targets: frozenset[str] = frozenset()) -> list[Bag]:
    bags = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        bag = parse_bag(raw)
        if bag and (not targets or bag.item_id in targets):
            bags.append(bag)
    return bags


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class StockTracker:
    """在庫のある出品を追跡し、入荷時刻と通知済みかどうかを記録する。

    在庫が「0 → あり」になった出品だけを通知対象にし、在庫がある間は再通知しない。
    売り切れたら記録を消すので、同じ受取枠で再入荷したときはもう一度通知される。
    記録はファイルに残すため、再起動しても入荷時刻を保ち、二重に通知しない。
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.seen: dict[str, dict] = {}  # key -> {"since": ISO, "notified": bool}
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.seen = {str(k): v for k, v in data.items()
                                 if isinstance(v, dict) and isinstance(v.get("since"), str)}
            except (OSError, ValueError):
                self.seen = {}

    def update(self, bags: list[Bag], now: dt.datetime | None = None) -> list[Bag]:
        """在庫状況を反映し、まだ通知していない在庫ありの出品を返す。"""
        now = now or _utcnow()
        in_stock = {b.key: b for b in bags if b.available > 0}
        for key in list(self.seen):
            if key not in in_stock:
                del self.seen[key]
        for key in in_stock:
            self.seen.setdefault(key, {"since": now.isoformat(), "notified": False})
        return [b for k, b in in_stock.items() if not self.seen[k]["notified"]]

    def mark(self, bag: Bag) -> None:
        entry = self.seen.setdefault(bag.key, {"since": _utcnow().isoformat(), "notified": False})
        entry["notified"] = True

    def since(self, bag: Bag) -> dt.datetime | None:
        entry = self.seen.get(bag.key)
        return _parse_time(entry["since"]) if entry else None

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.seen, f, ensure_ascii=False)
        os.replace(tmp, self.path)
