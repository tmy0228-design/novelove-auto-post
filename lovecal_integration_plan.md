# らぶカル(Lovecal) 実装・統合に関する技術仕様書

本ドキュメントは、NoveloveシステムにおいてDMM新サービス「らぶカル（Lovecal）」の記事自動投稿（フルテキスト長文のスクレイピング含む）を実装するための、他AI・他開発者向けの引き継ぎ用詳細設計書です。

## 1. 概要と可否判定
- **可否**: 実装可能です。
- **背景**: APIのみであらすじを取得すると約100文字で切り捨てられるため、現在FANZA等で行っているのと同様に**「APIでURL取得 → 該当URLからあらすじのフルテキストをスクレイピング」**という2段構えのフローをとります。

## 2. APIの仕様（アイテム一覧の取得）
らぶカルのコンテンツはFANZA同人等と同じ「DMM Affiliate ItemList API」で取得可能です。

- **Endpoint**: `https://api.dmm.com/affiliate/v3/ItemList`
- **Request Parameters**:
  - `site`: `"FANZA"`
  - `keyword`: 引数指定（任意）
  - ※注意：らぶカル固有の `floor` コードが存在しないか未定義の場合があるため、既存の `doujin` からキーワード `らぶカル` 等で絞るか、またはAPIの戻り値の `floor_name` が `らぶカル（TL）` または `らぶカル（BL）` であるものを抽出してください。

## 3. スクレイピング仕様（フルテキストあらすじの取得）

APIから抽出した `URL` （例: `https://lovecul.dmm.co.jp/tl/-/detail/=/cid=...`）にアクセスし、あらすじ本文をスクレイピングする際の仕様です。

### 必須: 年齢確認(R-18)の突破
らぶカルは単独サブドメイン（`lovecul.dmm.co.jp`）で動いており、初回アクセス時は年齢確認ページへリダイレクトされます。これを回避するために、HTMLリクエスト時に以下のCookieを必ず付与してください。
```python
cookies = {"age_check_done": "1"}
headers = {"User-Agent": "Mozilla/5.0 ..."} # 適切なUAが必要
r = requests.get(page_url, headers=headers, cookies=cookies)
```

### パース（抽出）ロジック
従来のFANZA同人は `<div class="mg-b20 lh4">` 等にあらすじがありましたが、らぶカルは構造が異なります。BeautifulSoupを使用し、以下のセレクタを対象に抽出してください。
- **セレクタ**: `<p class="summary__txt">`
- **処理例**:
```python
soup = BeautifulSoup(r.content, "html.parser")
desc_p = soup.find("p", class_="summary__txt")
if desc_p:
    full_description = desc_p.get_text(separator="\n").strip()
```
これにより、約600〜1000文字の完全な長文あらすじが取得可能です。

## 4. プログラム修正箇所（実装手順）

### 1. `novelove_fetcher.py`
- **`FETCH_TARGETS`への追加**:
  既存のFANZA同人の配列の末尾に、らぶカル用のDictを追加してください。
  ```python
  {"site": "FANZA", "service": "doujin", "floor": "digital_doujin", "genre": "doujin_bl", "label": "らぶカル_BL", "keyword": "らぶカル"},
  {"site": "FANZA", "service": "doujin", "floor": "digital_doujin", "genre": "doujin_tl", "label": "らぶカル_TL", "keyword": "らぶカル"},
  ```
  *(※APIの仕様変更等によりfloor等が変わる可能性があるため、テストレスポンスを見ながら調整してください)*
- **スクレイピング関数の改修**:
  詳細ページへリクエストを送る関数（例：`fetch_fanza_doujin_items` または共通リクエスト部）にて、上記 `cookies={"age_check_done": "1"}` を常時送信するよう仕様変更してください。
- **DOM抽出条件の追加**:
  あらすじを抽出する箇所で「`<div class="mg-b20 lh4">` が無ければ、`<p class="summary__txt">` を探す」というフォールバック（分岐）を追加してください。

### 2. `auto_post.py`
- 新しくフェッチされたラベル名（`label="らぶカル_BL"` 等）を検知し、WordPress側のカテゴリ（BL／TL）やタグに正しく割り振られるように条件分岐（if-elif群）を微調整してください。 

## 5. 注意事項
- **アクセス負荷**: スクレイピング実行の際は、必ず1リクエストごとに `time.sleep(2)` 等の間隔を空け、サーバーへ過度な負荷（運営妨害等）とならないよう配慮してください。
