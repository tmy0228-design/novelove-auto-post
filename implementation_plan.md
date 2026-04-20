# 【計画】専売・独占タグの表記ゆれ完全修正プロジェクト

現在のシステムにある「FANZA独占」「らぶカル独占」という非公式な内部タグ名を廃止し、すべての箇所で**公式サイトの表記と同一**（FANZA専売、らぶカル専売）になるよう一斉置換・修正を行うための計画です。

また調査の結果、現在「新規投稿時に専売タグがそもそも付与されていない（v15.4.1の改修時のデグレ）」という深刻なバグも発見されたため、同時にこれを完全修復します。

## 🔴 User Review Required

> [!WARNING]
> **WPのカテゴリスラッグ（URL）について**
> 今回WordPress上のタグ名（表示名）を「FANZA独占」➔「FANZA専売」に変更しますが、**URLスラッグ（`/tag/fanza-exclusive/`）については変更せずそのまま維持する**ことを提案します。
> スラッグ名を変えると、過去にSNSや外部サイトでシェアされたURLがリンク切れ（404）になり、SEOの歴史的評価もリセットされてしまうためです。表示名だけを「FANZA専売」に直せば、見た目と検索エンジンの問題は100%解決します。

## 📝 Proposed Changes

---
### 1. `auto_post.py` (新規投稿システムのバグ修復＆仕様変更)
**概要**: 新規記事を投稿する際、DBの`is_exclusive = 1`フラグを見て自動で専売タグを付与するロジックが欠落していたため、公式表記仕様のマップとともに再実装します。

#### [MODIFY] `auto_post.py`
- L182〜L200付近（WordPressタグ組み立て部分）に以下のロジックを追加。
  - DBから渡された `is_exclusive == 1` の場合、サイト名に応じて以下を `tag_names` に自動追加する。
  - 判定マップ: `{"FANZA": "FANZA専売", "Lovecal": "らぶカル専売", "DLsite": "DLsite専売", "DigiKet": "DigiKet限定", "DMM.com": "DMM独占"}`

---
### 2. ローカルデータベース群の過去データ一斉置換
**概要**: すでに保存されている数千件の記事データ（`ai_tags` および `wp_tags` カラム）内に書き込まれてしまった「FANZA独占」「らぶカル独占」の文字列をクレンジング（置換）します。

#### [NEW] `refactor_exclusive_db_python.py` (使い捨てスクリプト)
- `novelove*.db` の全ファイルをループ処理。
- `UPDATE novelove_posts SET wp_tags = REPLACE(wp_tags, 'FANZA独占', 'FANZA専売')` 等のSQLを実行。
- これをやらないと、後でリライトツール(`nexus_rewrite.py`)を回した時に古い名称が復活してしまいます。

---
### 3. WordPress本番環境のタグ名リネーム（稼働中記事の修正）
**概要**: すでに投稿済みの約2,500件の記事に紐づいている「WordPressタグ」そのものの名前を変更します。

#### [Remote Execution] SSH経由での WP-CLI コマンド実行
- WP-CLIコマンドを使用して、対象タグの表示名を一瞬で置き換えます。これを行えば数千件の紐づきを保ったまま名称だけがリフレッシュされます。
  - `$ wp term update post_tag 138 --name="FANZA専売"`
  - `$ wp term update post_tag 139 --name="らぶカル専売"`

---
### 4. サイトのサイドバー・抽出ウィジェット項目の修正
**概要**: WordPressのサイドバーや絞り込み機能で使われている「特選タグ」の設定配列を直します。

#### [MODIFY] `functions_php_current.php`
- ローカルのバックアップであるPHP内の配列設定を修正します。
- 旧: `$exclusive_tags = ['DLsite専売', 'FANZA独占', 'FANZA専売', 'DMM専売', 'DigiKet限定', 'らぶカル独占'];`
- 新: `$exclusive_tags = ['DLsite専売', 'FANZA専売', 'DMM独占', 'DigiKet限定', 'らぶカル専売'];`
- その後、このファイルの中身を本番環境の `functions.php` (Cocoon Childテーマ) へSCPで転送して反映します。

---
### 5. ドキュメント仕様書のアップデート
**概要**: 今回の完全修正に合わせてルールブックを更新します。

#### [MODIFY] `SPECIFICATIONS.md`
- 第5章「タグ・カテゴリ設計書」の専売タグ一覧表を修正。
- FANZAの項目の「`FANZA独占`」を「`FANZA専売`」に修正し、らぶカルも同様に変更します。

## ❓ Open Questions

1. WPスラッグの維持（URLを変えない案）について、上記WARNINGの内容でよろしいでしょうか？
2. DMM（一般書籍）については現在「DMM独占」としていますが、公式サイトでは「独占先行」「DMM先行」などのバリエーションがあります。こちらは一度このまま（`DMM独占`）で今回進めてもよろしいでしょうか？

## ✅ Verification Plan

### 自動・スクリプト検証
- DBアップデート後に `SELECT COUNT(*) FROM novelove_posts WHERE wp_tags LIKE '%FANZA独占%'` を実行し `0` 件になることを確認。
- SSHリモートアクセスで `$ wp term list post_tag` を実行し、正しくリネームされたことを確認。
- ローカル環境で `python check_excl_api.py` 等を実行し、エラーが出ないか参照バグがないか確認。

### 手動確認
- 実際にWordPressの本番サイトにアクセスし、サイドバーの専売タグ一覧に「FANZA専売」が表示されているか確認。
- そのタグをクリックし、正しく記事一覧が表示される（リンク切れしていない）ことを確認。
