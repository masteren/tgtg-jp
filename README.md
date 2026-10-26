# tgtg-jp

フードロス削減アプリ [Too Good To Go](https://www.toogoodtogo.com/ja) のお気に入り店舗を定期的に確認し、
「サプライズバッグ」の在庫を 1 ページの**在庫ボード**にまとめて表示する個人用ツールです。
Discord への通知も任意で使えます。

購入は自動化せず、本人が公式アプリで購入します。

> **注意**　非公式 API（[tgtg-python](https://github.com/ahivert/tgtg-python)）を利用しています。
> Too Good To Go とは無関係で、自動アクセスは同社の利用規約に抵触する可能性があります。
> 個人利用の範囲で、自己責任でお使いください。

## 在庫ボード

在庫のある店舗を、入荷が新しい順に表示します。価格は見切りシール風のラベルで、
受取時間は「今日 18:00〜19:00」のように日本時間で表示し、1 分ごとに自動更新します。
スマートフォン表示とダークモードに対応しています。

`/api/status` では同じ内容を JSON で取得できます。

## 特長

- **入荷の追跡**　在庫が「なし → あり」になった時刻を記録し、売り切れたら記録を消します。
  再入荷や翌日分は新しい入荷として扱い、記録はファイルに保存するため再起動しても保たれます。
- **アクセス制御を尊重**　401/403（ログイン切れ・ボット対策によるブロック）を受けたら確認を止め、
  理由をボードに表示します。tgtg-python が持つ DataDome Cookie の自動生成・再送機能は無効化しています。
- **控えめなポーリング**　間隔は 60 秒以上に制限し、ランダムなゆらぎと夜間の静音時間帯を設定可能。
  429 は指数バックオフ、通信エラーは段階的に待機して再試行します。
- **依存を増やさない Web**　標準ライブラリの `ThreadingHTTPServer` で配信。監視スレッドと Web スレッドは
  不変オブジェクト `Status` をロック内で差し替えて共有します。
- **安全な既定値**　ボードは既定でサーバー内部（127.0.0.1）のみで待ち受け、外部公開には
  12 文字以上のパスワード（Basic 認証）を必須にしています。CSP などのセキュリティヘッダーを付与し、
  店舗名などの外部データはすべてエスケープします。
- **常駐運用**　systemd で動かし、障害時は自動再起動。人の対応が必要な停止（終了コード 2）では再起動しません。

## 構成

```
tgtg_jp/
├── __main__.py   CLI（login / favorites / test-notify / run）
├── config.py     設定の読み込みと検証
├── client.py     API クライアント、エラー分類、トークン保存
├── watcher.py    レスポンス解析と入荷の追跡（純粋なロジック）
├── monitor.py    ポーリングループ、バックオフ、静音時間帯、状態の共有
├── web.py        在庫ボード（HTML / JSON）
└── notify.py     Discord Webhook（任意）
```

## セットアップ（Ubuntu 22.04 / Python 3.10 以上）

```bash
git clone git@github.com:masteren/tgtg-jp.git ~/tgtg-jp
cd ~/tgtg-jp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml   # email を設定
touch .env && chmod 600 .env         # 秘密情報を置く場合に使用（任意）

.venv/bin/python -m tgtg_jp login       # メールの PIN を入力
.venv/bin/python -m tgtg_jp favorites   # お気に入りと item_id を表示
.venv/bin/python -m tgtg_jp run         # 監視と在庫ボードを開始（Ctrl+C で終了）
```

`favorites` が 403 で失敗する場合、そのサーバーからのアクセスはブロックされています。

### ボードを見る

既定ではサーバー内部でのみ待ち受けるため、手元の PC から SSH トンネル経由で開きます。

```bash
ssh -N -L 8787:127.0.0.1:8787 <サーバー>
# ブラウザで http://localhost:8787/
```

スマートフォンから見たい場合は、nginx などで HTTPS 化したうえでリバースプロキシするか、
`web.host` を公開アドレスにして `.env` に `TGTG_JP_WEB_PASSWORD=...` を設定してください。

### Discord 通知（任意）

`.env` に `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...` を書くと、入荷時に通知します。
`set -a; . ./.env; set +a` の後、`python -m tgtg_jp test-notify` で送信を確認できます。

## 常駐化（systemd）

`deploy/tgtg-jp.service` のユーザー名とパスを環境に合わせてから:

```bash
sudo cp deploy/tgtg-jp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tgtg-jp
journalctl -u tgtg-jp -f
```

ログイン切れやブロックで確認が止まった場合は、`login` をやり直してから `sudo systemctl restart tgtg-jp`。

## テスト

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

TGTG API と Discord はモックに置き換えており、外部には接続しません
（Web サーバーのテストのみ 127.0.0.1 で待ち受けます）。

## ライセンス

GPL-3.0（依存ライブラリ tgtg-python のライセンスに合わせています）
