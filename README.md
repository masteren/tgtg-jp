# tgtg-jp

フードロス削減アプリ [Too Good To Go](https://www.toogoodtogo.com/ja) のお気に入り店舗を定期的に確認し、
「サプライズバッグ」が出品されたら Discord に通知する個人用ツールです。

購入は自動化せず、通知を受けた本人が公式アプリで購入します。

> **注意**　非公式 API（[tgtg-python](https://github.com/ahivert/tgtg-python)）を利用しています。
> Too Good To Go とは無関係で、自動アクセスは同社の利用規約に抵触する可能性があります。
> 個人利用の範囲で、自己責任でお使いください。

## 特長

- **重複しない通知**　在庫が「なし → あり」になった出品だけを通知。売り切れ後の再入荷や翌日分は改めて通知し、
  通知履歴はファイルに保存するため再起動しても二重通知しません。
- **アクセス制御を尊重**　401/403（ログイン切れ・ボット対策によるブロック）を受けたら即停止して Discord に知らせます。
  tgtg-python が持つ DataDome Cookie の自動生成・再送機能は無効化しています。
- **控えめなポーリング**　間隔は 60 秒以上に制限し、ランダムなゆらぎと夜間の静音時間帯を設定可能。
  429 は指数バックオフ、通信エラーは段階的に待機して再試行します。
- **常駐運用**　systemd で動かし、障害時は自動再起動。人の対応が必要な停止（終了コード 2）では再起動しません。
- **秘密情報の扱い**　トークンは権限 0600 のファイルに保存し、Webhook URL は環境変数でも渡せます。
  ログやエラーメッセージにトークン・レスポンス本文を出しません。

## 構成

```
tgtg_jp/
├── __main__.py   CLI（login / favorites / test-notify / run）
├── config.py     設定の読み込みと検証
├── client.py     API クライアント、エラー分類、トークン保存
├── watcher.py    レスポンス解析と通知判定（純粋なロジック）
├── monitor.py    ポーリングループ、バックオフ、静音時間帯
└── notify.py     Discord Webhook（Embed 形式）
```

## セットアップ（Ubuntu 22.04 / Python 3.10 以上）

```bash
git clone git@github.com:masteren/tgtg-jp.git ~/tgtg-jp
cd ~/tgtg-jp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml   # email を設定
echo 'DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...' > .env && chmod 600 .env
```

Discord の Webhook URL は「サーバー設定 → 連携サービス → ウェブフック」で作成できます。

```bash
.venv/bin/python -m tgtg_jp login         # メールの PIN を入力
set -a; . ./.env; set +a
.venv/bin/python -m tgtg_jp test-notify   # Discord に届くか確認
.venv/bin/python -m tgtg_jp favorites     # お気に入りと item_id を表示
.venv/bin/python -m tgtg_jp run           # 監視開始（Ctrl+C で終了）
```

`favorites` が 403 で失敗する場合、そのサーバーからのアクセスはブロックされています。

## 常駐化（systemd）

`deploy/tgtg-jp.service` のユーザー名とパスを環境に合わせてから:

```bash
sudo cp deploy/tgtg-jp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tgtg-jp
journalctl -u tgtg-jp -f
```

ログイン切れやブロックで停止した場合は、`login` をやり直してから `sudo systemctl restart tgtg-jp`。

## テスト

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

API と Discord はモックに置き換えており、ネットワークには接続しません。

## ライセンス

GPL-3.0（依存ライブラリ tgtg-python のライセンスに合わせています）
