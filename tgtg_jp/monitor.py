"""監視ループと、Web ページに渡す現在の状態。"""

from __future__ import annotations

import datetime as dt
import logging
import random
import threading
import time
from dataclasses import dataclass, field, replace
from zoneinfo import ZoneInfo

from .client import AccessDenied, ApiError, CredentialStore, RateLimited, fetch_favorites, snapshot
from .config import Config
from .notify import NotifyError
from .watcher import Bag, StockTracker, parse_bags

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ACCESS_DENIED = 2  # systemd に再起動させない終了コード
MAX_RATE_LIMIT_BACKOFF = 3600
MAX_ERROR_BACKOFF = 900


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass(frozen=True)
class Status:
    """ある時点の監視状態（不変）。"""

    state: str = "starting"  # starting / ok / quiet / retrying / stopped
    message: str = ""
    checked_at: dt.datetime | None = None
    next_check_at: dt.datetime | None = None
    bags: tuple[Bag, ...] = ()
    since: dict[str, dt.datetime] = field(default_factory=dict)  # bag.key -> 入荷時刻


class StatusBoard:
    """監視スレッドが書き、Web スレッドが読む。ロックの中で丸ごと差し替えるだけ。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._status = Status()

    def get(self) -> Status:
        with self._lock:
            return self._status

    def set(self, **changes) -> None:
        with self._lock:
            self._status = replace(self._status, **changes)


def seconds_until_quiet_end(config: Config, now: dt.datetime) -> float:
    """静音時間帯なら終了までの秒数、そうでなければ 0 を返す。"""
    qh = config.quiet_hours
    if qh is None:
        return 0.0
    local = now.astimezone(ZoneInfo(config.timezone))
    minute = local.hour * 60 + local.minute
    if not qh.contains(minute):
        return 0.0
    end = local.replace(hour=qh.end // 60, minute=qh.end % 60, second=0, microsecond=0)
    if end <= local:
        end += dt.timedelta(days=1)
    return (end - local).total_seconds()


class Monitor:
    def __init__(self, config: Config, client, notifier, tracker: StockTracker,
                 credentials: CredentialStore | None = None, *, board: StatusBoard | None = None,
                 sleep=time.sleep, now=_utcnow, rng: random.Random | None = None):
        self.config = config
        self.client = client
        self.notifier = notifier  # None なら通知しない
        self.tracker = tracker
        self.credentials = credentials
        self.board = board or StatusBoard()
        self.sleep = sleep
        self.now = now
        self.rng = rng or random.Random()
        self.stop_requested = False
        self._saved_tokens = snapshot(client) if credentials else None

    def request_stop(self, *_):
        self.stop_requested = True

    def _wait(self, seconds: float) -> None:
        # 1 秒ずつ眠り、Ctrl+C / systemctl stop にすぐ反応できるようにする。
        end = time.monotonic() + seconds
        while not self.stop_requested and (remaining := end - time.monotonic()) > 0:
            self.sleep(min(remaining, 1.0))

    def _persist_tokens(self) -> None:
        # トークンが更新されていたら保存し、再起動後も再ログイン不要にする。
        if not self.credentials:
            return
        current = snapshot(self.client)
        if current != self._saved_tokens:
            self.credentials.save(current)
            self._saved_tokens = current

    def _alert(self, text: str) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.message(text, error=True)
        except NotifyError:
            pass

    def poll_once(self) -> int:
        """1 回取得して状態を更新し、通知する。送信した通知の数を返す。"""
        items = fetch_favorites(self.client, self.config.max_pages)
        self._persist_tokens()
        bags = parse_bags(items, self.config.targets)
        now = self.now()
        fresh = self.tracker.update(bags, now)
        sent = 0
        for bag in fresh:
            if self.notifier is None:
                self.tracker.mark(bag)
                continue
            try:
                self.notifier.bag(bag)
            except NotifyError as exc:
                # 記録しない → 次回もう一度送る
                log.warning("%s（%s）", exc, bag.store_name)
                continue
            self.tracker.mark(bag)
            sent += 1
        for bag in fresh:
            log.info("入荷: %s 残り%d", bag.store_name, bag.available)
        self.tracker.save()
        since = {b.key: t for b in bags if (t := self.tracker.since(b))}
        self.board.set(state="ok", message="", checked_at=now, bags=tuple(bags), since=since)
        in_stock = sum(1 for b in bags if b.available > 0)
        log.info("取得 %d 件 / 在庫あり %d 件 / 新規入荷 %d 件", len(bags), in_stock, len(fresh))
        return sent

    def run(self) -> int:
        cfg = self.config
        log.info("監視開始: 間隔 %d±%d 秒, 対象 %s", cfg.interval_seconds, cfg.jitter_seconds,
                 f"{len(cfg.targets)} 件" if cfg.targets else "お気に入り全件")
        errors = 0
        while not self.stop_requested:
            quiet = seconds_until_quiet_end(cfg, self.now())
            if quiet > 0:
                log.info("静音時間帯のため %d 分休止します。", int(quiet // 60) + 1)
                self.board.set(state="quiet", message="",
                               next_check_at=self.now() + dt.timedelta(seconds=quiet))
                self._wait(quiet)
                continue
            try:
                self.poll_once()
                errors = 0
                delay = cfg.interval_seconds + self.rng.uniform(0, cfg.jitter_seconds)
            except AccessDenied as exc:
                log.error("%s 監視を停止します。", exc)
                self.board.set(state="stopped", message=str(exc), next_check_at=None)
                self._alert(f"⛔ 監視を停止しました。\n{exc}")
                return EXIT_ACCESS_DENIED
            except RateLimited as exc:
                errors += 1
                delay = min(cfg.interval_seconds * 2 ** errors, MAX_RATE_LIMIT_BACKOFF)
                log.warning("%s %d 秒待機します。", exc, delay)
                self.board.set(state="retrying", message=str(exc))
            except ApiError as exc:
                errors += 1
                delay = min(60 * errors, MAX_ERROR_BACKOFF)
                log.warning("%s（連続 %d 回目）%d 秒後に再試行します。", exc, errors, delay)
                self.board.set(state="retrying", message=str(exc))
            self.board.set(next_check_at=self.now() + dt.timedelta(seconds=delay))
            self._wait(delay)
        log.info("監視を終了しました。")
        return EXIT_OK
