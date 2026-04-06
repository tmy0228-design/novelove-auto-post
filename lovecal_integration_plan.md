# らぶカル(Lovecal) 実装・統合に関する技術仕様書 (v2.0)

本ドキュメントは、NoveloveシステムにおいてDMM新サービス「らぶカル（Lovecal）」の記事自動投稿（フルテキスト取得含む）を実装するための、他AI・エンジニア向けの引き継ぎ用詳細設計書です。
※本設計は事前のサーバー検証（API・スクレイピング動作テスト）をクリアした実績ベースのものです。

## 1. 概要と可否判定
- **可否**: **完全実装可能**（既存のFANZA同人パイプラインにほぼそのまま乗ります）。
- **データフロー**: DMMアフィリエイトAPI（v3）で基底データ（URL・画像・アフィリンク・タグ）を取得 → URLに再アクセスして抽出したフルテキストあらすじでDB更新。

## 2. APIの基本パラメーター
検証の結果、DMM API側にらぶカル専用のフロアが新設されていることが確認できました。
- **BL取得用**: `site=FANZA`, `service=doujin`, `floor=digital_doujin_bl`
- **TL取得用**: `site=FANZA`, `service=doujin`, `floor=digital_doujin_tl`

## 3. システム全体の変更箇所（5箇所の差分）

らぶカルを追加するには、以下のファイルに対する改修が必要です。

### ① `novelove_fetcher.py`（基幹取得ロジック）
- **`FETCH_TARGETS` の追加**
  既存リストの末尾に、上記APIパラメーターベースのDictを2つ（BL/TL）追加する。
  ```python
  {"site": "FANZA", "service": "doujin", "floor": "digital_doujin_bl", "genre": "doujin_bl", "label": "らぶカル_BL", "keyword": None},
  {"site": "FANZA", "service": "doujin", "floor": "digital_doujin_tl", "genre": "doujin_tl", "label": "らぶカル_TL", "keyword": None},
  ```

- **R-18 年齢確認 Cookieの設定（必須）**
  `_make_fanza_session()` 内の `domain=domain` のループ対象に `".lovecul.dmm.co.jp"` を必ず追加すること。これを忘れると年齢確認に阻まれスクレイピングに失敗します。

- **ボイス作品のスキップ判定（重要）**
  らぶカルのBL/TLフロアには「シチュエーションボイス（ASMR）」が大量に混ざっています。Noveloveは漫画・小説専門のため、`item.get("imageURL", {}).get("large", "")` の中に `"/voice/"` が含まれている場合は `continue` するロジックを追加してください。

- **`floor_code`判定ロジックのバグ回避（重要）**
  `fetch_and_stock_all()` 内で `save_genre` を書き換える判定処理（L819付近）において、
  `elif fc == "digital_doujin":` となっている箇所を、
  `elif fc.startswith("digital_doujin"):` 
  に変更してください（`digital_doujin_bl` 等を拾わせるため）。

### ② `auto_post.py`（WPへの投稿・タグ付けロジック）
- **カテゴリの振り分け調整**
  新しく付与されるラベル（例：`"らぶカル_BL"`）を検知し、WordPressのカテゴリID（BLもしくはTL）に正しく割り振られるように `if "らぶカル" in label:` などの条件分岐を追記。

### ③ `nexus_dashboard.py`（管理UI）
- **ダッシュボード上の表示名・絞り込み対応**
  `_source_db` の生成部において、ソース名に `らぶカル` を許容・分類するフィルタリング調整を追加。

## 4. DOM抽出ルール（BeautifulSoup部）
らぶカルはページそのものはNext.js/SPA等ではありませんが、あらすじのクラス名が異なります。
`_parse_dmm_desc()` もしくは `scrape_description()` 内で以下の抽出を有効化してください。
（現状のコードはすでに `.summary__txt` をフォールバックとして持っているため、そのまま機能する可能性が高いです）

```python
# 商品ページ（.lovecul.dmm.co.jp/...）のあらすじ抽出
desc_p = soup.find("p", class_="summary__txt")
if desc_p:
    full_description = desc_p.get_text(separator="\n").strip()
    return full_description
```

## 5. 専売・独占タグについて
APIのレスポンス（`item.get("stock")` や `iteminfo`）から通常のFANZAと同様に判定可能です。万が一拾えない場合は、スクレイピング時に `soup.find_all(string=re.compile(r'専売|独占|先行'))` で拾うフォールバックが機能します。

---
以上が、Noveloveの現在のDB構造・サイト間連携を考慮した、らぶカル追加時の完全な変更スコープ（v2）です。
