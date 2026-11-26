# Yahoo!オークション 中古タイヤ監視

Yahoo!オークションの公開検索と商品詳細を定期確認し、指定したタイヤの本数と状態に合い、商品価格と配送先への確定送料の合計が上限以下なら Discord に通知します。Python 3.12 以上の標準ライブラリだけで動きます。GitHub Actions で運用する場合、PC の起動は不要です。

## ファイル構成

| パス | 内容 |
| --- | --- |
| `config.toml` | サイズ、本数、予算、状態、配送先、初回の動作 |
| `watcher/` | 取得、照合、送料計算、Discord 通知、履歴保存 |
| `state/history.json` | 既定ブランチに保存する通知済み商品 ID と初回の基準時刻 |
| `tests/` | 判定・送料・通知・履歴・公開 HTML のパーサーの自動テスト |
| `.github/workflows/watch.yml` | 定期実行、手動実行、実行前の自動テスト |

## 監視条件

`config.toml` を編集し、サイズや予算ごとに `[[watch]]` を追加します。初期ファイルには埼玉県向けの設定例が2件あります。

```toml
first_run = "skip"
search_pages = 2
max_details = 12

[[watch]]
size = "225/45R17"
count = 4
max_total_yen = 30000
condition = "used"
prefecture = "埼玉県"
```

- `condition` は中古の `used`、未使用の `new`、両方の `any` から指定します。中古には Yahoo! の「未使用に近い」を含めます。
- `first_run = "skip"` は初回の通常実行を開始した時刻以前の出品を除外します。後から検索結果に入った既存出品も除外されます。`"notify"` は初回に取得したページ内の合致商品も通知します。初回実行後の設定変更で既存の履歴や基準時刻が消えることはありません。
- `search_pages` は検索語ごとに1〜3ページ、`max_details` は全検索を通じて1〜24件です。検索語はサイズから生成します。Yahoo! の検索語を調整したいときは `query = "225-45r17 4本"` のように指定できます。異なる検索語は6件までです。
- タイヤ専用カテゴリ内で、タイトルに一致するサイズが1種類あり、本数が確定するものを照合します。全角や空白を含む表記に対応します。商品詳細にタイヤ仕様があれば照合し、サイズ混在、本数の矛盾、単価・ばら売りなど曖昧な表記、複数数量の出品は除外します。
- 現在価格か即決価格のどちらかが送料込み上限以下なら通知します。両方ある場合は価格と合計をそれぞれ表示し、現在価格には入札で変動する旨を表示します。ストアは税込額を使用します。

## GitHub で有効にする

1. 完全無料で運用するには、リポジトリが **Public** であることを確認します。公開リポジトリの標準 `ubuntu-latest` ランナーを使用し、キャッシュ、成果物、有料 API は利用しません。非公開リポジトリでは月間無料枠を消費します。[GitHub Actions の料金](https://docs.github.com/en/actions/concepts/billing-and-usage)
2. Discord のチャンネル設定「連携サービス」で Webhook を作成し、URL をコピーします。GitHub の **Settings → Secrets and variables → Actions → New repository secret** で `DISCORD_WEBHOOK_URL` を登録します。URL はコードや設定ファイルに保存しません。[Discord Webhook の仕様](https://docs.discord.com/developers/resources/webhook#execute-webhook)
3. `config.toml` を調整し、実装一式を GitHub の既定ブランチ（このリポジトリでは `main`）に反映します。**Settings → Actions → General** でワークフローが許可され、自動発行の `GITHUB_TOKEN` が `contents: write` で履歴ファイルを更新できることを確認します。カスタムトークンの登録は不要です。ブランチ保護や組織ポリシーで直接更新が制限される場合は、履歴の更新を許可できる構成にする必要があります。
4. **Actions → Yahoo!オークション タイヤ監視 → Run workflow** で既定ブランチを選び、試行をオンにして実行します。検索・詳細取得のログを確認し、試行をオフにすると実際の通知と履歴更新が有効になります。初回の試行は既存出品も候補として表示します。
5. 定期実行は日本時間の毎時17分・47分です。Actions が混雑していると実行が遅延または省略される場合があります。公開リポジトリに60日間アクティビティがなければ、GitHub は定期実行を無効化することがあります。[GitHub Actions の定期実行仕様](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## ローカル実行とログ

```console
python -m unittest discover -s tests -v
python -m watcher --dry-run
```

ローカルから本送信する場合は、環境変数 `DISCORD_WEBHOOK_URL` を設定して `python -m watcher` を実行します。ローカルの履歴は Git 管理外の `state.local.json` に保存されます。GitHub Actions とは履歴を共有しません。

GitHub では **Actions → 実行履歴 → watch** を開き、「公開検索を試行」または「監視と通知」のログを確認します。検索件数、詳細の対象件数、送料不明、通知済みの商品 ID、通信エラーを記録します。Yahoo! へのアクセスは直列で、リクエスト開始間隔は1.5秒以上、通信タイムアウトは7秒です。一時的な通信エラーと対象の 5xx の再試行は1回までです。403・429 が返った場合は実行を止めます。ジョブ全体の上限は5分です。

Discord がメッセージ ID を返した商品だけを履歴に追加し、すぐに GitHub の Contents API で保存します。定期・手動実行は同じ同時実行グループで直列化し、更新時にファイルの SHA を照合します。Discord の失敗時は履歴を更新せず、再び候補に入れば次の実行で再試行します。Discord が受信した直後に応答が途切れたり、履歴を保存できなかったりした場合は、次の実行で重複する可能性があります。履歴保存エラーを修正するまでは再実行を控え、Discord と履歴を確認してください。[GitHub Contents API](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents)

## 送料の確認結果と制約

2026年11月20日に、ログインなしで公開検索の1・2ページ目と商品詳細の HTML を取得し、埋め込みデータを確認しました。

| 実例 | 確認結果 |
| --- | --- |
| [中古4本セット・出品者負担](https://auctions.yahoo.co.jp/jp/auction/k1250718376) | 検索の送料無料表示と詳細の出品者負担が一致。説明に沖縄・離島の例外があり、内陸の埼玉県では0円として扱えました。税込の現在価格と即決価格も確認しました。 |
| [着払い](https://auctions.yahoo.co.jp/jp/auction/p1252986077) | 詳細に着払いが表示され、埼玉県向けの金額と個口数を確定できませんでした。 |
| [地域別・本数別送料のストア出品](https://auctions.yahoo.co.jp/jp/auction/h1252958540) | 一覧に送料の参考額がありましたが、公開詳細 HTML に配送先を選択した結果がありませんでした。説明には本数による料金計算や中継料の例外がありました。 |

実装が自動確定するのは、一覧で送料無料、詳細で出品者負担を確認でき、説明の配送条件が対象地域や個人配送に抵触しないと判定した出品です。落札者負担・着払い・配送条件が曖昧な料金は「不明」として通知対象外にします。送料計算は、確定したセット総額・1本単価・1個口単価と個口数を受け取れる構造にしてあります。地域別送料や複数個口の情報を公開ページから取得する処理は、確定額・課金単位・個口数を検証できるまで未対応です。[Yahoo! の送料負担の説明](https://support.yahoo-net.jp/PccAuctions/s/article/H000013026) [複数口配送の公式ヘルプ](https://support.yahoo-net.jp/SccAuctions/s/article/H000014503)

使用するのは通常検索と商品詳細の公開 URL です。Yahoo! の `robots.txt` が新着順の検索パラメーターを制限しているため、通常検索の取得範囲から未通知の出品を検出し、取得した出品時刻をもとに詳細確認の順序を決めます。ランキング外や設定したページ数・詳細件数を超えた出品は見つからないことがあります。サイトの HTML 変更や GitHub ランナーからのアクセス制限でも取得が止まる可能性があります。ブラウザ、自動ログイン、アクセス制限の回避は使用しません。[Yahoo! オークションの robots.txt](https://auctions.yahoo.co.jp/robots.txt)
