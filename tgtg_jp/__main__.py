"""コマンドライン: python -m tgtg_jp <command>"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from .client import (AccessDenied, ApiError, CredentialStore, RateLimited, fetch_favorites,
                     from_saved, login_by_email, snapshot)
from .config import ConfigError, load_config
from .monitor import Monitor
from .notify import DiscordNotifier, NotifyError
from .watcher import StockTracker, parse_bags

log = logging.getLogger("tgtg_jp")


def _client(config):
    data = CredentialStore(config.credentials_path).load()
    if not data:
        raise ConfigError("ログイン情報がありません。先に `python -m tgtg_jp login` を実行してください。")
    return from_saved(data)


def cmd_login(config, args) -> int:
    print("TGTG からメールが届きます。PIN を入力してください（リンク方式の場合は空のまま Enter）。")
    client = login_by_email(config.email)
    CredentialStore(config.credentials_path).save(snapshot(client))
    print(f"ログインしました。保存先: {config.credentials_path}")
    return 0


def cmd_favorites(config, args) -> int:
    client = _client(config)
    bags = parse_bags(fetch_favorites(client, config.max_pages))
    CredentialStore(config.credentials_path).save(snapshot(client))
    if not bags:
        print("お気に入りがありません。公式アプリで店舗をお気に入り登録してください。")
    for bag in bags:
        mark = "●" if bag.available else "○"
        name = f"{bag.store_name} {bag.bag_name}".strip()
        print(f"{mark} {bag.item_id:>10}  残り{bag.available:>2}  {bag.price or '-':>7}  {name}")
    return 0


def cmd_test_notify(config, args) -> int:
    DiscordNotifier(config.discord_webhook_url, config.timezone).message("✅ tgtg-jp のテスト通知です。")
    print("Discord に送信しました。")
    return 0


def cmd_run(config, args) -> int:
    client = _client(config)
    notifier = DiscordNotifier(config.discord_webhook_url, config.timezone)
    tracker = StockTracker(config.notified_path)
    monitor = Monitor(config, client, notifier, tracker, CredentialStore(config.credentials_path))
    signal.signal(signal.SIGINT, monitor.request_stop)
    signal.signal(signal.SIGTERM, monitor.request_stop)
    return monitor.run()


COMMANDS = {
    "login": (cmd_login, "メール認証でログインし、トークンを保存する"),
    "favorites": (cmd_favorites, "お気に入りの item_id と在庫を一覧表示する"),
    "test-notify": (cmd_test_notify, "Discord にテスト通知を送る"),
    "run": (cmd_run, "監視を開始する"),
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tgtg_jp", description="Too Good To Go 在庫通知ツール")
    parser.add_argument("-c", "--config", default="config.yaml", help="設定ファイル（既定: config.yaml）")
    parser.add_argument("-v", "--verbose", action="store_true", help="デバッグログを出す")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_, help_text) in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    # requests/urllib3 のデバッグログは URL やヘッダーを含むので抑える。
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    try:
        config = load_config(args.config)
        return COMMANDS[args.command][0](config, args)
    except AccessDenied as exc:
        log.error("%s", exc)
        return 2
    except (ConfigError, NotifyError, RateLimited, ApiError) as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
