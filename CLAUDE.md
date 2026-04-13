# CLAUDE.md — AI共有ルールブック

このファイルは、どのPCのどのAIアシスタントも最初に読むべき「プロジェクト憲法」です。
ロックではなく、**チームとして守るべきルールの共有**です。

---

## 🗺️ このプロジェクトについて

**Novelove（ノベラブ）** は、FANZAやDLsite等の同人・電子書籍作品を自動収集し、AIで記事を生成してWordPressに自動投稿するシステムです。

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

```
novelove_soul.py
    ↑
novelove_core.py  ←  novelove_soul のみ参照
    ↑
novelove_fetcher.py  ←  novelove_core のみ参照
auto_post.py      ←  soul + core + fetcher 全部参照
    ↑
nexus_rewrite.py  ←  novelove_core + auto_post から関数を参照
    ↑
nexus_dashboard.py  ←  nexus_rewrite を内部でimport
nexus_revive.py   ←  novelove_core + novelove_fetcher
nexus_healer/gsc/purge.py  ←  novelove_core のみ
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
0 10 * * *    auto_post.py --ranking → ランキング記事（毎日10時）
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
- **デプロイ対象**: `auto_post.py`, `novelove_core.py`, `novelove_fetcher.py`, `novelove_soul.py`（nexus系は手動 `update_ui.py` を実行）
- **将来の予定**: `git pull` 方式に変更して全ファイルを自動同期

---

## 📋 AIが守るべきルール

### ✅ 必ずやること
1. **コード変更前に必ず仕様を確認する** — `SPECIFICATIONS.md` と `CHANGELOG.md` を必ず参照
2. **差分ベースで変更を提示する** — ファイル全体を書き直さない
3. **サーバー操作前にdry-run** — cron・SystemD変更は事前確認を徹底
4. **変更したら `auto_post.py` の構文チェックを必ず行う** — `python -m py_compile auto_post.py`
5. **GitHubへのpushはコアファイルのみ** — `.gitignore` のルールを守る

### ❌ やってはいけないこと
1. **フォルダ分け（`engine/`, `nexus/`等）は勝手にやらない** — importパス・SystemD・cronの同時変更が必要になりリスクが高い
2. **テスト・調査用の一時ファイルをgit addしない** — `check_*.py`, `test_*.py`, `debug_*.py` 等は絶対にpushしない
3. **DBファイル・ログファイルをpushしない** — `.env`, `*.db`, `*.log`, `gsc-service-account.json` はgitignore対象
4. **`force push`は最終手段** — ログを消すので原則使用禁止
5. **サーバーのcrontabを確認せずに変更しない** — `check_cron.py` を実行して現状を把握してから

---

## 📝 現在進行中・決定済みの事項

| 状況 | 内容 |
|:--|:--|
| ✅ 完了 | GitHub上の一時ファイル(check_*.py等)を全削除 |
| ✅ 完了 | `.gitignore` を大幅強化（一時ファイル・DBを確実に除外） |
| ✅ 完了 | Discord通知に `あらすじ{desc_c_len}文字` を復元 (`auto_post.py`) |
| ✅ 完了 | リライト時のAIスコア再計算機能 (`nexus_rewrite.py`) |
| ✅ 完了 | ダッシュボード一覧テーブルの列幅・列順を全面再調整（v13.0.7） |
| ✅ 完了 | 日付列を右側にまとめて配置し、重要情報の視認性を向上（v13.0.7） |
| ✅ 完了 | 詳細パネルに手動ステータス変更機能を追加（watching/pending/excluded 双方向変更対応）（v13.0.7） |
| ✅ 完了 | ローカル期待値スコアリングシステムを導入。投稿・在庫破棄の優先順位を「発売日＋情報量＋人気タグ」ベースで最適化（v13.1.0） |
| ✅ 完了 | ダッシュボードにローカル期待値スコアの表示カラムを追加（v13.1.1） |
| ✅ 完了 | FANZA同人から「らぶカル (Lovecal)」を完全分離・独立化し、バッジ・タグ・内部リンクを専用化（v13.7.0） |
| ✅ 完了 | v13.6.0巻き戻しで復活した `sqlite3.Row` バグと緊急停止の無限ループを根本治療（v13.7.0） |
| ✅ 完了 | pyflakesを用いたコアファイルのデッドコード（未使用import/変数）の完全パージ（v13.7.0） |
| ✅ 完了 | DMM/FANZA/らぶカルの専売タグを3サイト完全分離。DMM独占(151)/FANZA独占(138)/らぶカル独占(139)（v14.5.0） |
| ✅ 完了 | 専売判定から「先行」除外ロジックを完全削除。純粋にAPI「専売」「独占」の有無のみで判定（v14.5.0） |
| ✅ 完了 | `scrape_description()` の戻り値タプルバグ修正（v14.5.0） |
| ✅ 完了 | サイドバー専売ボタンに0件非表示チェック追加。全タグページにSEOメタ情報設定（v14.5.0） |
| 🔜 次のタスク | `auto_deploy.sh` を `git pull` 方式に書き換え（nexus系も自動デプロイ対象に） |

---

## 🔑 このファイル自体について

- このファイルは常に最新の状態を保つこと
- 重要な決定をした際は必ずこのファイルを更新してGitHubにpushすること
- 「どのPCのAIも同じ認識を持つ」ことがこのファイルの目的
