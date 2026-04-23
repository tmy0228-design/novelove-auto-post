# CLAUDE.md — AI共有ルールブック

> **⚠️ 必読 ⚠️ このファイルを最後まで読まずにコードを触ることは禁止です。**
> 以下の3ファイルは、作業開始前に必ず全文を読むこと：
> 1. **`CLAUDE.md`**（本ファイル）— プロジェクトの掟。AIの行動規範。
> 2. **`SPECIFICATIONS.md`** — 技術仕様書。全機能の設計詳細と仕様凍結事項。
> 3. **`CHANGELOG.md`** — 変更履歴。過去のバグと教訓が記録されている。
>
> **読まずに作業して壊した前科あり（v14.4事故）。繰り返すな。**

このファイルは、どのPCのどのAIアシスタントも最初に読むべき「プロジェクト憲法」です。
ロックではなく、**チームとして守るべきルールの共有**です。

---

## 🗺️ このプロジェクトについて

**Novelove（ノベラブ）** は、FANZAやらぶカル、DLsite等の同人・電子書籍作品を自動収集し、AIで記事を生成してWordPressに自動投稿するシステムです。

- **投稿サイト**: novelove.jp（WordPress / KUSANAGIサーバー）
- **ダッシュボード**: nexus (/nexus) でStreamlitが稼働（要Basic認証）
- **自動投稿**: 30分ごとにcronで `auto_post.py` が実行される

---

## 🏗️ ファイル構成と責務

| ファイル | 役割 |
|:--|:--|
| `auto_post.py` | 中核エンジン。取得→AI生成→WP投稿まで一気通貫 |
| `novelove_fetcher.py` | FANZA/DLsite/DigiKetのスクレイピング・RSS取得 |
| `novelove_core.py` | DB接続・共通ユーティリティ・設定値 |
| `novelove_soul.py` | AIライターキャラ設定・NG表現リスト・FACT_GUARD |
| `nexus_dashboard.py` | Streamlit管理ダッシュボード |
| `nexus_rewrite.py` | 既存記事のAIリライトエンジン |
| `nexus_revive.py` | 失敗・未投稿記事の自動再挑戦 |
| `nexus_healer.py` | DBの破損・異常データ修復 |
| `nexus_gsc.py` | Google Search Console連携 |
| `nexus_purge.py` | 低品質記事の自動削除 |
| `sync_db_wp.py` | DBとWPのステータス同期 |
| `update_affiliate_text.py` | 過去記事のアフィリエイトリンク一括更新 |

### import依存ツリー（これが壊れると全体が落ちる）

```text
novelove_soul.py
    ↑
novelove_core.py  ←  魂(soul)とインフラ(core)。全モジュールの基盤
    ├── novelove_fetcher.py  (取得ロジック)
    ├── novelove_writer.py   (AI執筆エンジン)
    └── novelove_ranking.py  (ランキング生成)
        ↑
auto_post.py      ←  これら全てを束ねるメインエンジン
        ↑
nexus_rewrite.py  ←  core + auto_post 等の関数を参照
        ↑
nexus_dashboard.py ← nexus系UI
```

---

## 🖥️ サーバー構成（Kusanagi）

- **場所**: `root@novelove.jp`（パスワード: `.env`参照、直接ファイル `check_cron.py` 等で確認可）
- **スクリプトディレクトリ**: `/home/kusanagi/scripts/`
- **Pythonインタープリタ**: `/opt/kusanagi/bin/python3`
- **環境変数ファイル**: `/home/kusanagi/scripts/.env`（gitignore対象）
- **DBファイル**: `/home/kusanagi/scripts/novelove.db` 他（gitignore対象）
- **認証ファイル**: `/home/kusanagi/scripts/gsc-service-account.json`（gitignore対象）

### cron設定（変更前に必ず確認）

```
*/30 * * * *  auto_post.py           → 自動投稿（30分ごと）
0 22 * * *    auto_post.py --ranking → ランキング記事（毎日22時・曜日でサイト自動判定）
15 8,20 * * * nexus_revive.py        → 失敗記事の再挑戦（朝8:15/夜8:15）
*/30 * * * *  auto_deploy.sh         → GitHub自動デプロイ（30分ごと）
30 3 * * *    nexus_gsc.py           → GSCデータ取得（毎日3:30）
```

### systemd（ダッシュボードサービス）

```ini
[Service]
WorkingDirectory=/home/kusanagi/scripts
ExecStart=/opt/kusanagi/bin/python3 -m streamlit run nexus_dashboard.py ...
```

---

## 🤖 自動デプロイの仕組み（`auto_deploy.sh`）

GitHubのmainブランチに変更がpushされると、30分以内にサーバーが自動で取り込む。

- **現在の方式**: GitHub APIでSHAを比較し、変更があれば対象ファイルを`curl`でダウンロード
- **デプロイ対象**: `auto_post.py`, `novelove_core.py`, `novelove_fetcher.py`, `novelove_soul.py`, `novelove_ranking.py`, `novelove_writer.py`, `nexus_purge.py`（nexus系は手動 `update_ui.py` を実行）
- **将来の予定**: `git pull` 方式に変更して全ファイルを自動同期

---

## 📋 AIが守るべきルール

### 🚨 最重要ルール（これを破ったAIは即アウト）

1. **ユーザーの明示的な許可なくコードを変更しない**
   - 仕様の確認・提案・説明は自由。だが**コードの編集・生成は、ユーザーが明確に「やって」「直して」「実装して」と言った場合のみ**。
   - 「こうしたほうが良さそう」と思っても、勝手に変更を加えない。まず提案し、承認を得ること。

2. **ビジネスロジックを独自判断で変更しない**
   - 「技術的に正しそう」でも、収益やユーザー体験に影響する判断をAIが勝手にしてはいけない。
   - 💀 **過去の大事故（v14.4）**: 「先行配信は期間限定だから専売じゃない → 除外しよう」とAIが勝手に判断し、独占タグを持つ作品の12%以上を巻き添えで除外した。これはアフィリエイト収益に直結する重大な機会損失だった。
   - 判断に迷ったら**必ずユーザーに質問する**。推測で行動しない。

3. **わからないことは自分の知識に頼らず、調べるか質問する**
   - DMM APIの仕様、WordPressの挙動、既存コードの意図など、不確かなことは**必ずAPIドキュメント・実コード・実データを確認**してから行動する。
   - 「たぶんこうだろう」で動くな。確認してから動け。

4. **ローカルPC上のデータベース（`novelove.db` 等）を信用せず、不用意に本番送信しない**
   - 💀 **過去の大事故（v15.2事故）**: サーバーで掃除済みの過去のゴミフラグ（`is_exclusive=1`）がローカルの古いDBには残っていた。それを知らずにローカル環境でリライト処理を回した結果、ローカルの古い状態を正として本番WordPressを上書きし、削除済みの専売タグを大量に大復活させた。
   - 対策: **本番のマスターデータ（最新の正）はサーバー上にしかない**。ローカルDBは「Dry-Run用のダミーデータ」として扱うこと。ローカルで検証する際は必ず事前にサーバーから最新DBをダウンロードするか、ローカルからのWordPress本番送信（書き込み）を遮断してからテストをすること。

5. **Windows環境由来の「文字化け（Mojibake）」でパニックにならないこと**
   - サーバー（Linux/UTF-8）のコマンド結果やDBの中身をローカルのWindowsターミナル（Shift-JIS/cp932）越しに出力した際、高確率で日本語が `` のように文字化けして表示される。
   - これは単なる**「ローカルPCの表示上の化け」**であり、サーバー上のDBやシステムが壊れたわけではない。絶対に「DBのデータが文字化けして壊れています！」と誤報して**ユーザーをパニクらせないこと**。
   - （※DigiKet等の外部サイト由来の古い文字コードは、既に `novelove_fetcher.py` 内の `r.content.decode('utf-8', fallback...)` でシステム的に100%解決・洗浄済みである。混在はあり得ない）
   - 文字化けが気になって作業に支障が出る場合は、PowerShell内で一時的に `chcp 65001` （UTF-8モード）を実行してから各種コマンドを実行すること。

### ✅ 必ずやること
1. **コード変更前に必ず仕様を確認する** — `SPECIFICATIONS.md` と `CHANGELOG.md` を必ず参照
2. **差分ベースで変更を提示する** — ファイル全体を書き直さない
3. **サーバー操作前にdry-run** — cron・SystemD変更は事前確認を徹底
4. **変更したら構文チェックを必ず行う** — `python -m py_compile <対象ファイル>`
5. **GitHubへのpushはコアファイルのみ** — `.gitignore` のルールを守る
6. **関数の戻り値や引数を変更した場合、全呼び出し元を確認する** — grepで全ファイル横断検索し、旧インターフェースの残骸を残さない (v14.5.1 の `generate_affiliate_url` の kwargs 抜けバグからの教訓)

### ✅ デプロイ完了チェックリスト（ローカル修正後に必ず実施）
ローカルでコードを修正した場合、以下を**すべて**実施しないと「完了」とは言えない：
1. `python -m py_compile <ファイル>` で構文チェック
2. **サーバー上の同ファイルにも同じ修正を適用**（`auto_deploy.sh` が拾うまで待つか、手動で `sed` / SFTP 等でデプロイ）
3. サーバー上で `grep` 等を使い、修正が正しく反映されたことを確認
4. `git add` → `git commit` → `git push origin main`
5. ドキュメント（`SPECIFICATIONS.md`, `CHANGELOG.md`, `CLAUDE.md`）を更新

### ❌ やってはいけないこと
1. **フォルダ分け（`engine/`, `nexus/`等）は勝手にやらない** — importパス・SystemD・cronの同時変更が必要になりリスクが高い
2. **テスト・調査用の一時ファイルをgit addしない** — `check_*.py`, `test_*.py`, `debug_*.py` 等は絶対にpushしない
3. **DBファイル・ログファイルをpushしない** — `.env`, `*.db`, `*.log`, `gsc-service-account.json` はgitignore対象
4. **`force push`は最終手段** — ログを消すので原則使用禁止
5. **サーバーのcrontabを確認せずに変更しない** — `check_cron.py` を実行して現状を把握してから
6. **「論理的に正しそう」という理由だけでフィルターを厳しくしない** — 過剰なフィルターは機会損失。迷ったらユーザーに確認
7. **サーバー上のファイルを「不要」と判断して削除しない** — 💀 **v14.5事故**: AIが「いらないもの」と判断してサーバーから `gsc-service-account.json`、`auto_deploy.sh` 等を削除した結果、GSCバッチ停止・自動デプロイ停止・投稿システム7時間以上停止の大事故が発生。サーバー上のファイルは一切削除禁止。必要なら**必ずユーザーに確認**すること。
8. **共通関数の引数を増やす際、特殊な渡し方（`**kwargs`等）を省略しない** — 💀 **v14.5.1バグ**: `generate_affiliate_url()` を全サイト共通で呼び出す形にリファクタした際、DLsiteのみ必須だった `pid` が渡されなくなり、全DLsite記事のアフィリンクが腐る事故が発生。共通化する際は**全パターンの引数が満たされているか**厳密にチェックすること。

### 🔒 サーバー必須ファイル一覧（削除厳禁）

サーバー `/home/kusanagi/scripts/` に以下のファイルが**必ず存在する**こと。
作業後に1つでも欠けていたら、それは事故である。

**コアPythonファイル（13個）:**
- `auto_post.py` / `novelove_fetcher.py` / `novelove_core.py` / `novelove_soul.py` / `novelove_writer.py`
- `nexus_dashboard.py` / `nexus_rewrite.py` / `nexus_revive.py` / `nexus_healer.py` / `nexus_gsc.py` / `nexus_purge.py`
- `novelove_ranking.py` / `sync_db_wp.py` / `update_affiliate_text.py`

**シェルスクリプト:**
- `auto_deploy.sh`（GitHub自動デプロイ。実行権限 `chmod +x` 必須）

**設定・認証ファイル（gitignore対象・git管理外）:**
- `.env`（全APIキー・認証情報）
- `gsc-service-account.json`（Google Search Console認証JSON）

**データベース:**
- `novelove.db` / `novelove_dlsite.db` / `novelove_digiket.db`

**.envに必須のキー（13個）:**
`GEMINI_API_KEY`, `WP_USER`, `WP_APP_PASSWORD`, `DMM_API_ID`, `DMM_AFFILIATE_API_ID`, `DMM_AFFILIATE_LINK_ID`, `DISCORD_WEBHOOK_URL`, `DLSITE_AFFILIATE_ID`, `DEEPSEEK_API_KEY`, `DIGIKET_AFFILIATE_ID`, `GSC_SERVICE_ACCOUNT_JSON`, `GSC_SITE_URL`, `OPENROUTER_API_KEY`

---

## 📝 現在進行中・決定済みの事項

| 状況 | 内容 |
|:--|:--|
| ✅ 完了 | ランキングシステムv15.0: Lovecal対応・週末寄せスケジュール・偏り解消・ピックアップ統一・12Hタイマー撤廃 |
| ✅ 完了 | ダッシュボードv15.1: ステータス変更を全5パターン対応・タブリセット問題をJS自動復元で修正・行選択rerun除去 |
| ✅ 完了 | 堅牢性強化v15.3.0: 潜在バグの一掃（ランキングDB衝突保護・APIエラー時の暴走防止・Discord通知強化） |
| ✅ 完了 | HTML骨格パターン多様化v16.0.0: Scaled Content Abuse対策 (A/B/C/Dの4パターン) |
| ✅ 完了 | API基盤刷新v17.1.0: Grok 4.1 Fast全記事統一＋メタ発言完全禁止 |
| ✅ 完了 | ペルソナ駆動型キャラv17.3.0: スラング直接指定を撤廃、公式サイト準拠の分厚いペルソナ設定に全面刷新。blockquote崩れ修正・AI出力サニタイズ追加 |
| 🔜 次のタスク | `auto_deploy.sh` を `git pull` 方式に書き換え（nexus系も自動デプロイ対象に） |

---

## 🔑 このファイル自体について

- このファイルは常に最新の状態を保つこと
- 重要な決定をした際は必ずこのファイルを更新してGitHubにpushすること
- 「どのPCのAIも同じ認識を持つ」ことがこのファイルの目的
