"""TGTG API クライアントとログイン情報の保存。

tgtg-python は 403 を受けると DataDome の Cookie を自動生成して再送する。
本ツールはその挙動を無効化し、アクセス拒否を受けたら素直に停止する。
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from tgtg import TgtgClient

LANGUAGE = "ja-JP"
TIMEOUT_SECONDS = 20


class SafeTgtgClient(TgtgClient):
    """リトライや DataDome Cookie の生成を行わない、1 リクエスト 1 送信のクライアント。"""

    def _post(self, url, **kwargs):
        kwargs.setdefault("allow_redirects", False)
        return self.session.post(
            url, headers=self._headers, proxies=self.proxies, timeout=self.timeout, **kwargs
        )


class AccessDenied(RuntimeError):
    """401/403。人の対応が必要なので監視を止める。"""


class RateLimited(RuntimeError):
    """429。しばらく待ってから再開する。"""


class ApiError(RuntimeError):
    """その他の一時的な失敗。"""


def status_of(exc: BaseException) -> int | None:
    """tgtg-python の例外 (status, content) や requests の例外から HTTP ステータスを取り出す。"""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code is None and getattr(exc, "args", None):
        code = exc.args[0]
    if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599:
        return code
    return None


def classify(exc: BaseException) -> RuntimeError:
    """例外を 3 種類に分類する。レスポンス本文やトークンはメッセージに含めない。"""
    code = status_of(exc)
    if code in (401, 403):
        hint = (
            "ログインが無効です。`python -m tgtg_jp login` で再ログインしてください。"
            if code == 401
            else "アクセスが拒否されました（DataDome などのブロックの可能性）。"
            "公式アプリで状態を確認し、時間をおいてから再開してください。"
        )
        return AccessDenied(f"HTTP {code}: {hint}")
    if code == 429:
        return RateLimited("HTTP 429: リクエストが多すぎます。")
    return ApiError(f"API 呼び出しに失敗しました（HTTP {code or '不明'}）。")


class CredentialStore:
    """トークンを 0600 の JSON に保存する。暗号化はしない（鍵が同じマシンにあれば意味がないため）。"""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not all(data.get(k) for k in ("access_token", "refresh_token", "cookie")):
            return None
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)


def snapshot(client: TgtgClient) -> dict:
    refreshed = client.last_time_token_refreshed
    return {
        "access_token": client.access_token,
        "refresh_token": client.refresh_token,
        "cookie": client.cookie,
        "user_agent": client.user_agent,
        "last_refreshed": refreshed.isoformat() if refreshed else None,
    }


def from_saved(data: dict) -> SafeTgtgClient:
    refreshed = data.get("last_refreshed")
    return SafeTgtgClient(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        cookie=data["cookie"],
        # 端末の User-Agent を固定し、起動のたびに別端末に見えないようにする。
        user_agent=data.get("user_agent") or None,
        last_time_token_refreshed=dt.datetime.fromisoformat(refreshed) if refreshed else None,
        language=LANGUAGE,
        timeout=TIMEOUT_SECONDS,
    )


def login_by_email(email: str) -> SafeTgtgClient:
    """メールに届く PIN（またはリンク）で認証する。対話入力が必要。"""
    client = SafeTgtgClient(email=email, language=LANGUAGE, timeout=TIMEOUT_SECONDS)
    try:
        client.login()
    except Exception as exc:  # noqa: BLE001 - 分類してから再送出
        raise classify(exc) from None
    return client


def fetch_favorites(client: TgtgClient, max_pages: int) -> list[dict]:
    """お気に入りを全ページ取得する（1 ページ 20 件、最大 max_pages ページ）。"""
    page_size = 20
    items: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            batch = client.get_items(favorites_only=True, page_size=page_size, page=page)
        except Exception as exc:  # noqa: BLE001
            raise classify(exc) from None
        if not isinstance(batch, list):
            raise ApiError("想定外のレスポンス形式です。")
        items.extend(batch)
        if len(batch) < page_size:
            break
    return items
