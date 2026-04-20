# ランキング & セール対象抽出ロジック — 全サイト詳細リファレンス

> **最終調査日**: 2026-04-20
> **実装ファイル**: `nexus_revive.py`
> **実行頻度**: cron で 8:30 / 20:30 の1日2回

---

## 目次

1. [サイト横断マップ（全体像）](#1-サイト横断マップ全体像)
2. [DMM ブックス（一般商業）](#2-dmm-ブックス一般商業)
3. [FANZA ブックス（アダルト商業）](#3-fanza-ブックスアダルト商業)
4. [らぶカル（FANZA同人）](#4-らぶカルfanza同人)
5. [DLsite](#5-dlsite)
6. [DigiKet](#6-digiket)
7. [タグの付与・剥奪フロー](#7-タグの付与剥奪フロー)
8. [IDの形式・照合ルール](#8-idの形式照合ルール)
9. [既知の制限事項・注意点](#9-既知の制限事項注意点)

---

## 1. サイト横断マップ（全体像）

### ランキング取得

| サイト | 取得手法 | 取得元URL/API | 件数 | ID形式 |
|---|---|---|---|---|
| **DMM（一般）** | API | `api.dmm.com/v3/ItemList` sort=rank | 各フロア20件 | `b000ehftx95853` 等 |
| **FANZA（商業）** | API | 同上 | 各フロア20件 | `b876ashkm04726` 等 |
| **らぶカル（同人）** | API | 同上 | 各フロア20件 | `d_724719` 等 |
| **DLsite** | スクレイピング | `/ranking/week` ページ | 各フロア30件 | `rj123456` / `bj123456` |
| **DigiKet** | XML API | `api.digiket.com/xml/api/getxml.php` sort=week | 各ターゲット30件 | `itm0012345` |

### セール取得

| サイト | 取得手法 | 取得元URL/API | 件数上限 | ID形式 |
|---|---|---|---|---|
| **DMM（一般）** | スクレイピング | `book.dmm.com/list/?floor=G{bl,tl}&sale=discount&discount_rate=50` | 最大200ページ×120件 | `b000ehftx95853` 等 |
| **FANZA（商業）** | スクレイピング | `book.dmm.co.jp/list/?category={670008,670009}&sale=discount&discount_rate=50` | 最大200ページ×120件 | `b876ashkm04726` 等 |
| **らぶカル（同人）** | API | `api.dmm.com/v3/ItemList` → `campaign` フィールド判定 | 各フロア100件 | `d_724719` 等 |
| **DLsite** | スクレイピング | `/fsr/=/campaign/1/` ページ | 最大10ページ×100件 | `rj123456` / `bj123456` |
| **DigiKet** | スクレイピング | `/result/_data/limit=300/camp=on/` ページ | 各フロア300件 | `itm0012345` |

---

## 2. DMM ブックス（一般商業）

### ランキング

- **関数名**: `fetch_fanza_ranking_product_ids()`（FANZA関数と共用）
- **手法**: DMM公式API v3 (`ItemList`)
- **対象フロア**:
  - `site=DMM.com, service=ebook, floor=comic` — 一般コミック
  - `site=DMM.com, service=ebook, floor=novel` — 一般ノベル
- **パラメータ**: `hits=20, sort=rank`
- **ID抽出方法**: レスポンスJSON → `items[].content_id`
- **件数**: 各フロア最大20件（合計最大40件）

### セール

- **関数名**: `fetch_fanza_sale_product_ids()`（FANZA関数内のスクレイピング部分）
- **手法**: Webスクレイピング（`requests.Session` + `BeautifulSoup`）
- **対象URL**:
  - `https://book.dmm.com/list/?floor=Gbl&sale=discount&discount_rate=50`（BL）
  - `https://book.dmm.com/list/?floor=Gtl&sale=discount&discount_rate=50`（TL）
- **セッション設定**:
  - Cookie: `age_check_done=1`, `ckcy=1`（年齢確認バイパス）
  - User-Agent: 標準的なブラウザUA
- **ページネーション**: `&page=N`（1〜200ページ、商品が尽きたら自動終了）
- **ID抽出方法**: HTMLから `<a href="/product/{ID}/">` を正規表現 `/product/([^/]+)/` で抽出
- **sleep**: ページ間に1秒のウェイト
- **APIでセールが取れない理由**: 商業ebookフロアでは `campaign` フィールドが常に未出力（実測確認済み）

---

## 3. FANZA ブックス（アダルト商業）

### ランキング

- **関数名**: `fetch_fanza_ranking_product_ids()`（DMM関数と共用）
- **手法**: DMM公式API v3 (`ItemList`)
- **対象フロア**:
  - `site=FANZA, service=ebook, floor=bl` — FANZA BLコミック
  - `site=FANZA, service=ebook, floor=tl` — FANZA TLコミック
- **パラメータ**: `hits=20, sort=rank`
- **ID抽出方法**: レスポンスJSON → `items[].content_id`
- **件数**: 各フロア最大20件（合計最大40件）

### セール

- **関数名**: `fetch_fanza_sale_product_ids()`（DMM関数内のスクレイピング部分）
- **手法**: Webスクレイピング
- **対象URL**:
  - `https://book.dmm.co.jp/list/?category=670008&sale=discount&discount_rate=50`（BL）
  - `https://book.dmm.co.jp/list/?category=670009&sale=discount&discount_rate=50`（TL）
- **仕組み**: DMM一般と完全に同じ（セッション設定・ページネーション・ID抽出すべて共通）
- **ドメインの違い**: `book.dmm.co.jp`（FANZA）vs `book.dmm.com`（DMM一般）
- **フロア指定の違い**: `category=670008/670009`（FANZA）vs `floor=Gbl/Gtl`（DMM一般）

---

## 4. らぶカル（FANZA同人）

### ランキング

- **関数名**: `fetch_fanza_ranking_product_ids()`（DMM/FANZA関数と共用）
- **手法**: DMM公式API v3 (`ItemList`)
- **対象フロア**:
  - `site=FANZA, service=doujin, floor=digital_doujin_bl` — 同人BL
  - `site=FANZA, service=doujin, floor=digital_doujin_tl` — 同人TL
- **パラメータ**: `hits=20, sort=rank`
- **ID抽出方法**: レスポンスJSON → `items[].content_id`
- **件数**: 各フロア最大20件（合計最大40件）

### セール

- **関数名**: `fetch_fanza_sale_product_ids()`（API部分）
- **手法**: DMM公式API v3 (`ItemList`) — **APIのみで完結**
- **判定方法**: レスポンスJSON → `items[].campaign` フィールドが存在し、空でなければセール中と判定
- **campaign レスポンス例**:
  ```json
  {
    "date_begin": "2026-04-16T12:00:00Z",
    "date_end": "",
    "title": "50%OFF"
  }
  ```
- **追加情報（APIで取得可能）**:
  - `prices.price` — 現在の販売価格（セール価格）
  - `prices.list_price` — 定価（商業では出力されないが同人では出力される）
- **件数**: 各フロア最大100件（合計最大200件）
- **APIだけで済む理由**: 同人フロアは出版社を経由しないため、DMMのシステム上セール情報がAPIにも公開されている

---

## 5. DLsite

### ランキング

- **関数名**: `fetch_dlsite_ranking_product_ids()`
- **手法**: 週間ランキングページのHTMLスクレイピング
- **対象URL**:
  - `https://www.dlsite.com/girls/ranking/week` — 女性向け（TL含む）
  - `https://www.dlsite.com/bl/ranking/week` — BL
- **ID抽出方法**: HTMLから正規表現 `((?:RJ|BJ|VJ)\d{6,10})` でRJ/BJ/VJコードを抽出
- **件数**: 各URL上位30件（出現順に重複除去 → 先頭30件を採用）
- **ヘッダー**: `novelove_core.py` の共通 `HEADERS` を使用

### セール

- **関数名**: `fetch_dlsite_sale_product_ids(published_pids)`
- **手法**: セール検索ページのHTMLスクレイピング
- **対象URL（計4フロア）**:
  - `https://www.dlsite.com/girls/fsr/=/campaign/1/order/trend/per_page/100/` — 女性向け同人
  - `https://www.dlsite.com/bl/fsr/=/campaign/1/order/trend/per_page/100/` — BL同人
  - `https://www.dlsite.com/girls-pro/fsr/=/campaign/1/order/trend/per_page/100/` — 女性向け商業
  - `https://www.dlsite.com/bl-pro/fsr/=/campaign/1/order/trend/per_page/100/` — BL商業
- **ページネーション**: URLに `page/{N}/` を付加（最大10ページ = 1000件/フロア）
- **終了判定**: 重複除去後の作品数が50件未満になったら最終ページと判断
- **ID抽出方法**: 正規表現 `((?:RJ|BJ|VJ)\d{6,10})` でRJ/BJ/VJコードを抽出
- **引数 `published_pids`**: 現在のコードでは使われていないが、将来のDB突合最適化用に残されている
- **DLsite裏JSON APIについて**: `discount` / `campaign` フィールドは常にNoneを返す仕様のため使用不可（v14.6.0で確認し、スクレイピングに切り替え済み）

---

## 6. DigiKet

### ランキング

- **関数名**: `fetch_digiket_ranking_product_ids()`
- **手法**: DigiKet公式XML API
- **エンドポイント**: `https://api.digiket.com/xml/api/getxml.php`
- **パラメータ**:
  - `target=8` — 商業BL
  - `target=6` — 商業TL
  - `target=2` — 同人
  - `sort=week` — 週間ランキング
- **ID抽出方法**: XMLレスポンスから正規表現 `ITM(\d+)` でITM番号を抽出
- **出力形式**: `itm{番号}`（小文字、接頭辞付き）
- **件数**: 各ターゲット上位30件（出現順に重複除去 → 先頭30件を採用）
- **エンコーディング**: UTF-8（errors=ignore）

### セール

- **関数名**: `fetch_digiket_sale_product_ids()`
- **手法**: セール専用ページのHTMLスクレイピング
- **対象URL**:
  - `https://www.digiket.com/b/result/_data/limit=300/camp=on/sort=camp_end/` — 女性向け同人
  - `https://www.digiket.com/bl/result/_data/limit=300/camp=on/sort=camp_end/` — BL商業
- **URL内パラメータの意味**:
  - `camp=on` — 本物のセール中作品のみに絞込（最重要）
  - `sort=camp_end` — キャンペーン終了日順でソート
  - `limit=300` — 最大300件取得
- **ID抽出方法**: HTMLから正規表現 `ITM(\d+)` でITM番号を抽出
- **出力形式**: `itm{番号}`（小文字、接頭辞付き）
- **エンコーディング**: EUC-JP（errors=ignore） ← DigiKet独自の古い仕様
- **ヘッダー**: `novelove_core.py` の共通 `HEADERS` を使用

---

## 7. タグの付与・剥奪フロー

### 使用するWordPressタグ

| タグ名 | slug | 用途 |
|---|---|---|
| 🔥期間限定セール | `sale` | セール中の作品に自動付与 |
| 🏆売れ筋作品 | `best-seller` | ランキング上位の作品に自動付与 |

### 処理フロー（`run_nexus()` 関数）

```
Step 0: WPタグID確保
  └── get_or_create_tag("期間限定セール", "sale")
  └── get_or_create_tag("売れ筋作品", "best-seller")

Step 1: 自社DB全published記事のproduct_id一覧を取得
  └── 3つのDB（FANZA / DLsite / DigiKet）を横断検索

Step 2: 各サイトから情報取得（すべて隔離実行）
  ├── FANZA/DMM/らぶカル セール取得 → all_sale_ids へ
  ├── FANZA/DMM/らぶカル ランキング取得 → all_ranking_ids へ
  ├── DLsite セール取得 → all_sale_ids へ
  ├── DLsite ランキング取得 → all_ranking_ids へ
  ├── DigiKet セール取得 → all_sale_ids へ
  └── DigiKet ランキング取得 → all_ranking_ids へ

Step 3: DB突合 → 差分だけWP APIを叩く
  ├── 「新たにタグを付ける」対象を特定
  ├── 「タグを剥がす」対象を特定
  └── 変更がある記事だけに update_post_tags() を実行

Step 4: Discord通知（変更サマリー）
```

### 隔離設計の仕組み

各サイトの取得は `try/except` で完全に隔離されており、1サイトがエラーで死んでも他のサイトの処理は続行される。
エラーが発生した場合はDiscordに即通知される。

---

## 8. IDの形式・照合ルール

### 各サイトの商品ID形式

| サイト | ID例 | 正規表現パターン | DB格納形式 |
|---|---|---|---|
| DMM/FANZA（商業） | `b876ashkm04726` | `/product/([^/]+)/` | 小文字 |
| らぶカル（同人） | `d_724719` | `content_id` そのまま | 小文字 |
| DLsite | `RJ123456` | `((?:RJ\|BJ\|VJ)\d{6,10})` | 小文字（`rj123456`） |
| DigiKet | `ITM0012345` | `ITM(\d+)` | 小文字（`itm0012345`） |

### 照合ルール

- **すべてのIDは小文字に変換して格納・比較される**（`.lower()`）
- WP記事のslug = product_id（小文字）で一致検索
- セット演算（`set` の `in` 演算子）で O(1) の高速突合

---

## 9. 既知の制限事項・注意点

### DMM/FANZA（商業）

- APIでは `campaign` / `list_price` が**一切返されない**
  → 50%OFF以上のセール品のみをスクレイピングで取得
- スクレイピングは `sale=discount` パラメータに依存しており、DMM側のURL仕様変更で壊れる可能性あり
- 30%OFF等の小規模セールは現在取得対象外（`discount_rate=50` で50%以上に絞込中）

### らぶカル（同人）

- APIの `campaign` フィールドは**同人フロアでしか機能しない**
- `hits=100` の上限があるため、100件を超えるセール対象がある場合は取りこぼしが発生する可能性あり
  → 今のところ100件を超えるケースは稀

### DLsite

- 裏JSON APIの `discount` / `campaign` は**常にNone**（v14.6.0で確認済み）
  → HTMLページのスクレイピングに完全移行済み
- ページネーション終了判定は「重複除去後50件未満」というヒューリスティック
  → 本来はHTMLのページネーションリンクの有無で判定すべきだが、現状動作に問題なし
- `girls` / `bl`（同人）と `girls-pro` / `bl-pro`（商業）の4フロアを巡回

### DigiKet

- **最も不安定なサイト**（サーバーダウン頻度が高い）
  → エラーに最も寛容な設計（他サイトへの影響遮断を最優先）
- HTMLエンコーディングが **EUC-JP**（2020年代でこれは珍しい）
- XML APIは公式だが、ドキュメントが乏しく仕様変更の告知がない
- セールURLの `camp=on` は非公式パラメータの可能性があり、予告なく無効化されるリスクあり

### 全体

- セール/ランキングの「付与」は即時だが、「剥奪」はセール終了後の次回バッチ実行（最大12時間のタイムラグ）
- WP REST API の認証は Basic Auth（アプリケーションパスワード）方式
- 1回の実行で最大数千件のWP API通信が発生する可能性があるため、WPサーバーの負荷に注意
