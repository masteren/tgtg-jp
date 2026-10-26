"""Discord Webhook への通知。"""

from __future__ import annotations

import logging
import time

import requests

from .watcher import Bag

log = logging.getLogger(__name__)
GREEN, RED, GREY = 0x2ECC71, 0xE74C3C, 0x95A5A6


class NotifyError(RuntimeError):
    pass


def bag_embed(bag: Bag, tz: str) -> dict:
    title = bag.store_name if not bag.bag_name else f"{bag.store_name}｜{bag.bag_name}"
    price = bag.price or "不明"
    if bag.price and bag.original_price:
        price = f"{bag.price}（通常 {bag.original_price}）"
    fields = [
        {"name": "残り", "value": f"{bag.available} 個", "inline": True},
        {"name": "価格", "value": price, "inline": True},
        {"name": "受取時間", "value": bag.pickup_text(tz), "inline": False},
    ]
    if bag.address:
        fields.append({"name": "場所", "value": bag.address[:1024], "inline": False})
    return {
        "title": f"🥡 {title}"[:256],
        "url": bag.share_url,
        "description": "在庫が出ました。アプリで購入してください。",
        "color": GREEN,
        "fields": fields,
    }


class DiscordNotifier:
    def __init__(self, webhook_url: str, tz: str = "Asia/Tokyo", session: requests.Session | None = None,
                 sleep=time.sleep):
        if not webhook_url:
            raise NotifyError("Discord の Webhook URL が設定されていません。")
        self.webhook_url = webhook_url
        self.tz = tz
        self.session = session or requests.Session()
        self.sleep = sleep

    def _send(self, payload: dict) -> None:
        payload = {"username": "TGTG 在庫通知", **payload}
        for attempt in range(2):
            try:
                resp = self.session.post(self.webhook_url, json=payload, timeout=10)
            except requests.RequestException:
                raise NotifyError("Discord に接続できませんでした。") from None
            if resp.status_code in (200, 204):
                return
            if resp.status_code == 429 and attempt == 0:
                try:
                    wait = float(resp.json().get("retry_after", 1))
                except ValueError:
                    wait = 1.0
                self.sleep(min(max(wait, 0.5), 30))
                continue
            # Webhook URL はトークンを含むため、エラーメッセージに出さない。
            raise NotifyError(f"Discord への送信に失敗しました（HTTP {resp.status_code}）。")

    def bag(self, bag: Bag) -> None:
        self._send({"embeds": [bag_embed(bag, self.tz)]})

    def message(self, text: str, *, error: bool = False) -> None:
        self._send({"embeds": [{"description": text[:4000], "color": RED if error else GREY}]})
