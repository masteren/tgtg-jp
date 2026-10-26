"""在庫ボード: 現在の在庫状況を 1 ページで見せる小さな Web サーバー（標準ライブラリのみ）。"""

from __future__ import annotations

import base64
import datetime as dt
import hmac
import json
import logging
import threading
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from .config import Config
from .monitor import Status, StatusBoard
from .watcher import Bag

log = logging.getLogger(__name__)
WEEKDAYS = "月火水木金土日"
REFRESH_SECONDS = 60

CSS = """
:root {
  --paper: #eef1ec; --ink: #16231b; --muted: #5c6b61; --rule: #cfd8d1;
  --sticker: #ffd60a; --sticker-ink: #c8201a; --alert: #a8231b; --alert-bg: #f8e3e0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #111813; --ink: #e4ebe5; --muted: #93a398; --rule: #29342c;
    --alert: #ff8f84; --alert-bg: #3a1d1a;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--paper); color: var(--ink);
  font-family: "Zen Kaku Gothic New", "Hiragino Sans", "Yu Gothic", system-ui, sans-serif;
  font-size: 1rem; line-height: 1.6;
}
main { max-width: 36rem; margin: 0 auto; padding: 1.25rem 1rem 3rem; }
.top {
  display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline;
  gap: .25rem 1rem; color: var(--muted); font-size: .8125rem;
}
.top > span:last-child { white-space: nowrap; }
.top b { color: var(--ink); font-weight: 700; }
.demo {
  margin-left: .5rem; padding: .0625rem .5rem; border: 1px solid var(--muted);
  border-radius: 999px; font-size: .75rem; white-space: nowrap;
}
h1 {
  margin: 1.75rem 0 .25rem; font-weight: 900; letter-spacing: -.01em;
  font-size: clamp(1.75rem, 7.5vw, 2.5rem); line-height: 1.2;
}
h1 .count { font-size: 1.6em; }
.lede { margin: 0 0 1.75rem; color: var(--muted); }
.notice {
  margin: 0 0 1.75rem; padding: .875rem 1rem; border-left: 4px solid var(--alert);
  background: var(--alert-bg); color: var(--ink);
}
.notice strong { color: var(--alert); }
.notice code { font-size: .875em; }
ul { list-style: none; margin: 0; padding: 0; }
.bag {
  display: grid; grid-template-columns: 5.75rem 1fr; gap: 1rem; align-items: start;
  padding: 1.25rem 0; border-top: 1px solid var(--rule);
}
.bag:last-child { border-bottom: 1px solid var(--rule); }
.sticker {
  width: 5.75rem; height: 5.75rem; border-radius: 50%; background: var(--sticker);
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  transform: rotate(-7deg); color: #1c1c1c; text-align: center; line-height: 1.1;
  box-shadow: inset 0 0 0 3px #fff4b0;
}
.sticker .price { color: var(--sticker-ink); font-weight: 900; font-size: 1.375rem; }
.sticker .was { font-size: .6875rem; margin-top: .25rem; text-decoration: line-through; }
.sticker .left { font-size: .75rem; font-weight: 700; margin-top: .125rem; }
.store { margin: 0; font-size: 1.125rem; font-weight: 700; line-height: 1.35; }
.kind { margin: .125rem 0 .5rem; color: var(--muted); font-size: .875rem; }
.when { margin: 0; font-weight: 700; }
.meta { margin: .25rem 0 0; color: var(--muted); font-size: .8125rem; }
.new { color: var(--sticker-ink); font-weight: 700; }
@media (prefers-color-scheme: dark) { .new { color: var(--sticker); } }
a { color: inherit; }
.open { display: inline-block; margin-top: .625rem; font-weight: 700; font-size: .875rem; }
a:focus-visible, summary:focus-visible { outline: 3px solid var(--sticker); outline-offset: 2px; }
details { margin-top: 2.25rem; }
summary { cursor: pointer; color: var(--muted); font-size: .875rem; }
.rest li { padding: .5rem 0; border-bottom: 1px solid var(--rule); font-size: .875rem; }
.rest li span { color: var(--muted); }
footer { margin-top: 2.5rem; color: var(--muted); font-size: .75rem; }
"""


def _local(t: dt.datetime, tz: str) -> dt.datetime:
    return t.astimezone(ZoneInfo(tz))


def _day_label(t: dt.datetime, now: dt.datetime) -> str:
    days = (t.date() - now.date()).days
    if days == 0:
        return "今日"
    if days == 1:
        return "明日"
    return f"{t:%-m/%-d}（{WEEKDAYS[t.weekday()]}）"


def pickup_label(bag: Bag, now: dt.datetime, tz: str) -> str:
    if not bag.pickup_start or not bag.pickup_end:
        return "受取時間は未定"
    start, end = _local(bag.pickup_start, tz), _local(bag.pickup_end, tz)
    return f"{_day_label(start, _local(now, tz))} {start:%H:%M}〜{end:%H:%M} に受取"


def ago_label(since: dt.datetime | None, now: dt.datetime) -> str:
    if since is None:
        return ""
    minutes = int((now - since).total_seconds() // 60)
    if minutes < 1:
        return "たった今入荷"
    if minutes < 60:
        return f"{minutes} 分前に入荷"
    return f"{minutes // 60} 時間前に入荷"


def _notice(status: Status, config: Config) -> str:
    msg = escape(status.message)
    if status.state == "stopped":
        hint = ("デモの --scenario blocked で再現したアクセス拒否です。" if config.demo
                else "対応後に <code>sudo systemctl restart tgtg-jp</code> で再開します。")
        stale = ""
        if status.bags and status.checked_at:
            stale = (f"<br>下の在庫は停止前（{_local(status.checked_at, config.timezone):%H:%M}）の情報で、"
                     "更新されていません。")
        return (f'<div class="notice" role="alert"><strong>確認を停止しました。</strong>{msg}<br>'
                f"{hint}{stale}</div>")
    if status.state == "retrying":
        return f'<div class="notice"><strong>確認に失敗しました。</strong>{msg} 自動で再試行します。</div>'
    return ""


def _headline(status: Status, in_stock: list[Bag], now: dt.datetime, tz: str) -> str:
    if status.state == "stopped" and not in_stock:
        return "<h1>在庫を確認できません</h1>"
    if status.checked_at is None:
        return "<h1>最初の確認を待っています</h1><p class=\"lede\">起動から数秒で表示されます。</p>"
    if in_stock:
        stores = len({b.item_id for b in in_stock})
        verb = "ありました" if status.state == "stopped" else "あります"
        return f'<h1><span class="count">{stores}</span> 店に在庫が{verb}</h1>'
    lede = "公式アプリで店舗をお気に入り登録すると、ここに表示されます。" if not status.bags else \
        "入荷するとこのページに表示されます。"
    if status.state == "quiet" and status.next_check_at:
        lede = f"夜間は確認を休んでいます。{_local(status.next_check_at, tz):%H:%M} に再開します。"
    return f'<h1>いまは在庫がありません</h1><p class="lede">{lede}</p>'


def _bag_item(bag: Bag, status: Status, now: dt.datetime, tz: str) -> str:
    was = f'<span class="was">{escape(bag.original_price)}</span>' if bag.original_price else ""
    ago = ago_label(status.since.get(bag.key), now)
    fresh = status.since.get(bag.key) and (now - status.since[bag.key]) < dt.timedelta(minutes=30)
    ago_html = f'<span class="new">{ago}</span>' if fresh else escape(ago)
    address = f"<br>{escape(bag.address)}" if bag.address else ""
    kind = f'<p class="kind">{escape(bag.bag_name)}</p>' if bag.bag_name else '<p class="kind"></p>'
    return f"""<li class="bag">
  <div class="sticker" aria-label="価格 {escape(bag.price or '不明')}、残り {bag.available} 個">
    <span class="price">{escape(bag.price or '—')}</span>{was}<span class="left">残り{bag.available}</span>
  </div>
  <div>
    <h2 class="store">{escape(bag.store_name)}</h2>
    {kind}
    <p class="when">{escape(pickup_label(bag, now, tz))}</p>
    <p class="meta">{ago_html}{address}</p>
    <a class="open" href="{escape(bag.share_url)}" rel="noreferrer">アプリで開く</a>
  </div>
</li>"""


def render_page(status: Status, config: Config, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    tz = config.timezone
    in_stock = sorted((b for b in status.bags if b.available > 0),
                      key=lambda b: status.since.get(b.key) or now, reverse=True)
    sold_out = [b for b in status.bags if b.available == 0]

    checked = "まだ確認していません"
    if status.checked_at:
        checked = f"最終確認 <b>{_local(status.checked_at, tz):%H:%M}</b>"
        if status.next_check_at and status.state in ("ok", "retrying"):
            checked += f"　次回 {_local(status.next_check_at, tz):%H:%M}"

    demo_tag = '<span class="demo">デモ（架空のデータ）</span>' if config.demo else ""
    items = "\n".join(_bag_item(b, status, now, tz) for b in in_stock)
    listing = f"<ul>{items}</ul>" if in_stock else ""
    rest = ""
    if sold_out:
        rows = "".join(
            f"<li>{escape(b.store_name)} <span>{escape(b.bag_name)}</span></li>" for b in sold_out)
        rest = (f"<details><summary>在庫なしのお気に入り（{len(sold_out)}）</summary>"
                f'<ul class="rest">{rows}</ul></details>')

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{REFRESH_SECONDS}">
<meta name="robots" content="noindex">
<title>TGTG 在庫ボード{"（デモ）" if config.demo else ""}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Zen+Kaku+Gothic+New:wght@400;700;900&display=swap">
<style>{CSS}</style>
</head>
<body>
<main>
  <div class="top"><span>TGTG 在庫ボード{demo_tag}</span><span>{checked}</span></div>
  {_headline(status, in_stock, now, tz)}
  {_notice(status, config)}
  {listing}
  {rest}
  <footer>1 分ごとに自動で更新します。購入は公式アプリで行ってください。</footer>
</main>
</body>
</html>"""


def status_json(status: Status) -> dict:
    def ts(t):
        return t.isoformat() if t else None

    return {
        "state": status.state,
        "message": status.message,
        "checked_at": ts(status.checked_at),
        "next_check_at": ts(status.next_check_at),
        "bags": [
            {
                "item_id": b.item_id, "store_name": b.store_name, "bag_name": b.bag_name,
                "available": b.available, "price": b.price, "original_price": b.original_price,
                "pickup_start": ts(b.pickup_start), "pickup_end": ts(b.pickup_end),
                "address": b.address, "in_stock_since": ts(status.since.get(b.key)),
            }
            for b in status.bags
        ],
    }


SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}


def make_handler(board: StatusBoard, config: Config):
    password = config.web.password.encode()

    class Handler(BaseHTTPRequestHandler):
        server_version = "tgtg-jp"
        sys_version = ""

        def log_message(self, fmt, *args):  # 標準エラーではなく logging へ
            log.debug("web: " + fmt, *args)

        def _authorized(self) -> bool:
            if not password:
                return True
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return False
            try:
                _, _, given = base64.b64decode(header[6:]).partition(b":")
            except ValueError:
                return False
            return hmac.compare_digest(given, password)

        def _send(self, code: int, body: bytes, content_type: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                return self._send(200, b"ok", "text/plain; charset=utf-8")
            if not self._authorized():
                return self._send(401, "認証が必要です".encode(), "text/plain; charset=utf-8",
                                  {"WWW-Authenticate": 'Basic realm="tgtg-jp", charset="UTF-8"'})
            status = board.get()
            if path == "/":
                return self._send(200, render_page(status, config).encode(), "text/html; charset=utf-8")
            if path == "/api/status":
                body = json.dumps(status_json(status), ensure_ascii=False).encode()
                return self._send(200, body, "application/json; charset=utf-8")
            return self._send(404, "見つかりません".encode(), "text/plain; charset=utf-8")

        do_HEAD = do_GET

    return Handler


def start_server(board: StatusBoard, config: Config) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((config.web.host, config.web.port), make_handler(board, config))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="web", daemon=True).start()
    log.info("在庫ボード: http://%s:%d/", config.web.host, config.web.port)
    return server
