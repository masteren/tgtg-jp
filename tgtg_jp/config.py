"""設定ファイルの読み込みと検証。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 短すぎる間隔はアカウント停止のリスクを高めるため、下限を設ける。
MIN_INTERVAL_SECONDS = 60
DEFAULT_STATE_DIR = Path(os.environ.get("TGTG_JP_HOME", "~/.config/tgtg-jp")).expanduser()
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ConfigError(ValueError):
    """ユーザー向けの設定エラー。"""


@dataclass(frozen=True)
class QuietHours:
    start: int  # 0:00 からの分
    end: int

    def contains(self, minute_of_day: int) -> bool:
        if self.start <= self.end:
            return self.start <= minute_of_day < self.end
        # 日付をまたぐ場合（例: 23:00-07:00）
        return minute_of_day >= self.start or minute_of_day < self.end


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"  # 既定ではサーバー内部からのみアクセス可能
    port: int = 8787
    password: str = ""  # 環境変数 TGTG_JP_WEB_PASSWORD から


@dataclass(frozen=True)
class Config:
    email: str
    discord_webhook_url: str
    interval_seconds: int = 120
    jitter_seconds: int = 20
    targets: frozenset[str] = field(default_factory=frozenset)  # 空 = お気に入り全件
    quiet_hours: QuietHours | None = None
    max_pages: int = 3
    timezone: str = "Asia/Tokyo"
    state_dir: Path = DEFAULT_STATE_DIR
    web: WebConfig = field(default_factory=WebConfig)

    @property
    def credentials_path(self) -> Path:
        return self.state_dir / "credentials.json"

    @property
    def notified_path(self) -> Path:
        return self.state_dir / "notified.json"


def _minutes(value: str, name: str) -> int:
    match = _HHMM.match(str(value))
    if not match:
        raise ConfigError(f"{name} は HH:MM 形式で指定してください（例: 23:00）。")
    return int(match.group(1)) * 60 + int(match.group(2))


def parse_config(raw: dict) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml の形式が正しくありません。")

    email = str(raw.get("email") or "").strip()
    if not email or "@" not in email:
        raise ConfigError("email に TGTG アカウントのメールアドレスを設定してください。")

    # Webhook URL は環境変数を優先（設定ファイルに秘密情報を書かずに済むように）。
    webhook = os.environ.get("DISCORD_WEBHOOK_URL") or str(
        (raw.get("discord") or {}).get("webhook_url") or ""
    )
    webhook = webhook.strip()
    if webhook and not webhook.startswith(
        ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
    ):
        raise ConfigError("discord.webhook_url は https://discord.com/api/webhooks/ で始まる URL にしてください。")

    polling = raw.get("polling") or {}
    interval = polling.get("interval_seconds", 120)
    jitter = polling.get("jitter_seconds", 20)
    max_pages = polling.get("max_pages", 3)
    for name, value in (("interval_seconds", interval), ("jitter_seconds", jitter), ("max_pages", max_pages)):
        if type(value) is not int or value < 0:
            raise ConfigError(f"polling.{name} は 0 以上の整数にしてください。")
    if interval < MIN_INTERVAL_SECONDS:
        raise ConfigError(f"polling.interval_seconds は {MIN_INTERVAL_SECONDS} 秒以上にしてください。")
    if not 1 <= max_pages <= 10:
        raise ConfigError("polling.max_pages は 1〜10 にしてください。")

    quiet = None
    qh = raw.get("quiet_hours")
    if qh:
        quiet = QuietHours(_minutes(qh.get("start"), "quiet_hours.start"), _minutes(qh.get("end"), "quiet_hours.end"))

    targets = frozenset(str(t).strip() for t in (raw.get("targets") or []) if str(t).strip())

    web_raw = raw.get("web") or {}
    port = web_raw.get("port", 8787)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ConfigError("web.port は 1024〜65535 の整数にしてください。")
    web = WebConfig(
        enabled=web_raw.get("enabled", True) is not False,
        host=str(web_raw.get("host") or "127.0.0.1"),
        port=port,
        password=os.environ.get("TGTG_JP_WEB_PASSWORD", ""),
    )
    if web.enabled and web.host not in LOOPBACK_HOSTS and len(web.password) < 12:
        raise ConfigError("web.host を外部に公開する場合は、環境変数 TGTG_JP_WEB_PASSWORD に"
                          "12 文字以上のパスワードを設定してください。")

    state_dir = raw.get("state_dir")
    return Config(
        email=email,
        discord_webhook_url=webhook,
        interval_seconds=interval,
        jitter_seconds=jitter,
        targets=targets,
        quiet_hours=quiet,
        max_pages=max_pages,
        timezone=str(raw.get("timezone") or "Asia/Tokyo"),
        state_dir=Path(state_dir).expanduser() if state_dir else DEFAULT_STATE_DIR,
        web=web,
    )


def load_config(path: str | Path) -> Config:
    path = Path(path).expanduser()
    if not path.exists():
        raise ConfigError(f"{path} が見つかりません。config.example.yaml をコピーして作成してください。")
    with path.open(encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f) or {})
