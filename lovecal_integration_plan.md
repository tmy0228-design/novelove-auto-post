# らぶカル(Lovecal) 実装・統合に関する技術仕様書

本ドキュメントは、NoveloveシステムにおいてDMM新サービス「らぶカル（Lovecal）」の記事自動投稿（フルテキスト取得含む）を実装するための、他AI・エンジニア向けの引き継ぎ用詳細設計書です。
※本設計は、現在の `novelove.db`、`nexus_dashboard.py` の構造を前提としています。

## 1. 概要と可否判定
- **可否**: 実装可能です。
- **データフロー**: DMM APIで基底データ（URL・画像・アフィリンク）を取得 → URLに再アクセスして抽出したフルテキストあらすじでDB更新。

## 2. システム全体の変更箇所マッピング

らぶカルを追加するには、現在のシステム構造上以下の **4つのファイル** に対する改修が必要です。

### ① `novelove_fetcher.py`（基幹取得ロジック）
- **`FETCH_TARGETS` の追加**
  既存のFANZA同人の配列にらぶカル用のDictを追加。
- **R-18 年齢確認 Cookieの設定（超重要）**
  `_make_fanza_session` またはスクレイピング時のリクエストヘッダに `cookies={"age_check_done": "1"}` を常時送信するよう仕様変更してください。
  ※画像取得エラーを防ぐため、Cookie付与ドメインに `".lovecul.dmm.co.jp"` を必ず追加すること。
- **DOM抽出条件のフォールバック**
  `_parse_dmm_desc` にて、「`<div class="mg-b20 lh4">` が無ければ、`<p class="summary__txt">` を探す」という分岐を追加。

### ② `auto_post.py`（WPへの投稿・タグ付けロジック）
- **カテゴリの振り分け調整**
  新しく付与されるラベル（例：`"らぶカル_BL"`）を検知し、WordPressのカテゴリID（BLもしくはTL）に正しく割り振られるように `if "らぶカル" in label:` の条件分岐を追記。

### ③ `nexus_dashboard.py`（管理UI）
- **ダッシュボード上の表示名・絞り込み対応**
  現在、Dashboard内の「DBソース判定」において `label == "FANZA"` といったハードコーディングがある場合（`df["_source_db"]`の生成部など）、らぶカルが未知のサイトとしてエラー・非表示にならないよう、ソース名に `らぶカル` を許容・分類するフィルタリング調整を追加。

### ④ `.env`（環境変数）
- 追加の対応は不要です（既存の `DMM_API_ID` と `DMM_AFFILIATE_API_ID` をそのまま流用可能なため）。

## 3. スクラッチ実装時のサンプルコード（BeautifulSoup部）

新規追加する `_parse_lovecal_desc()` または分岐処理のサンプルです。
```python
soup = BeautifulSoup(r.content, "html.parser")
desc_p = soup.find("p", class_="summary__txt")
if desc_p:
    full_description = desc_p.get_text(separator="\n").strip()
    return full_description
```
※必ずスクレイピング間の `time.sleep(2)` を順守すること。

---
以上が、Noveloveの現在のDB構造・サイト間連携を考慮した、らぶカル追加時の完全な変更スコープです。
