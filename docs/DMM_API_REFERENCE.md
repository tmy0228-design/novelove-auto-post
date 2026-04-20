# DMM / FANZA アフィリエイト API v3 リファレンス（Novelove向け）

> **最終調査日**: 2026-04-20
> **公式ドキュメント**: https://affiliate.dmm.com/api/v3/itemlist.html
> **ベースURL**: `https://api.dmm.com/affiliate/v3/`

---

## 目次

1. [API一覧と概要](#1-api一覧と概要)
2. [認証・共通パラメータ](#2-認証共通パラメータ)
3. [商品情報API（ItemList）— 最重要](#3-商品情報apiitemlist-最重要)
4. [フロアAPI（FloorList）](#4-フロアapifloorlist)
5. [ジャンル検索API（GenreSearch）](#5-ジャンル検索apigenresearch)
6. [メーカー検索API（MakerSearch）](#6-メーカー検索apimakersearch)
7. [シリーズ検索API（SeriesSearch）](#7-シリーズ検索apiсериessearch)
8. [作者検索API（AuthorSearch）](#8-作者検索apiauthorsearch)
9. [女優検索API（ActressSearch）](#9-女優検索apiactresssearch)
10. [フロア・サイト体系の整理](#10-フロアサイト体系の整理)
11. [セール情報の取得に関する重大な制限事項](#11-セール情報の取得に関する重大な制限事項)
12. [Noveloveでの現在の利用状況](#12-noveloveでの現在の利用状況)
13. [将来的な活用候補](#13-将来的な活用候補)

---

## 1. API一覧と概要

| API名 | エンドポイント | 用途 | Novelove利用 |
|---|---|---|---|
| 商品情報API | `/ItemList` | 商品の検索・詳細取得 | ✅ 使用中 |
| フロアAPI | `/FloorList` | サービス・フロアのコード一覧取得 | ⚪ 未使用（手動管理） |
| ジャンル検索API | `/GenreSearch` | ジャンルタグの名前・ID一覧取得 | 💡 活用候補 |
| メーカー検索API | `/MakerSearch` | 出版社・レーベル一覧取得 | 💡 活用候補 |
| シリーズ検索API | `/SeriesSearch` | シリーズ名・ID一覧取得 | 💡 活用候補 |
| 作者検索API | `/AuthorSearch` | 作者名・読み・ID一覧取得 | 💡 活用候補 |
| 女優検索API | `/ActressSearch` | AV女優情報取得 | ❌ 無関係 |

---

## 2. 認証・共通パラメータ

すべてのAPIリクエストに必要な共通パラメータ。

| パラメータ | 必須 | 説明 |
|---|---|---|
| `api_id` | ○ | DMMアフィリエイト登録時に発行されたAPI ID |
| `affiliate_id` | ○ | 末尾が `990`〜`999` のアフィリエイトID |
| `output` | - | `json` または `xml`（デフォルト: `json`） |

> **注意**: `affiliate_id` の末尾が `990`〜`999` の範囲外だとエラーになる。

---

## 3. 商品情報API（ItemList）— 最重要

Noveloveのメインエンジン。新着取得・ランキング・セール判定すべてがこのAPI経由。

### リクエストURL
```
https://api.dmm.com/affiliate/v3/ItemList
```

### リクエストパラメータ

| パラメータ | 物理名 | 必須 | 説明 |
|---|---|---|---|
| サイト | `site` | ○ | `DMM.com`（一般）または `FANZA`（アダルト） |
| サービス | `service` | ○ | `ebook`（電子書籍）、`doujin`（同人）等 |
| フロア | `floor` | - | `bl`, `tl`, `comic`, `novel`, `digital_doujin_bl` 等 |
| 取得件数 | `hits` | - | 初期20、最大100 |
| 検索開始位置 | `offset` | - | 初期1、最大50000 |
| ソート順 | `sort` | - | `rank`（人気）/ `date`（新着）/ `price`（高い順）/ `-price`（安い順）/ `review`（評価順）/ `match`（マッチング） |
| キーワード | `keyword` | - | UTF-8で指定 |
| 商品ID | `cid` | - | `content_id` を直接指定 |
| 絞り込み項目 | `article` | - | `actress` / `author` / `genre` / `series` / `maker` |
| 絞り込みID | `article_id` | - | 各検索APIから取得可能なフロアID |
| 発売日以降 | `gte_date` | - | ISO形式（例: `2026-01-01T00:00:0`）、この日付以降の発売商品に限定 |
| 在庫有りのみ | `mono_stock` | - | `reserve`（予約）/ `stock`（在庫あり）/ `mono`（通販） |

### レスポンス構造（重要フィールド抜粋）

```
result
├── total_count    ... 全体件数
├── items[]
│   ├── content_id ... 商品ID（例: "b876ashkm04726", "d_724719"）
│   ├── product_id ... 商品番号
│   ├── title      ... 商品タイトル
│   ├── URL        ... 商品ページURL
│   ├── affiliateURL ... アフィリエイトリンクURL
│   ├── date       ... 発売日・配信開始日
│   ├── imageURL
│   │   ├── large  ... 大きい画像URL
│   │   └── small  ... 小さい画像URL
│   ├── prices
│   │   ├── price      ... 販売価格（※常に現在の販売価格）
│   │   ├── list_price  ... 定価 ⚠️ 同人のみ出力、商業では欠落
│   │   └── deliveries  ... 配信タイプ別価格 ⚠️ 同人のみ出力
│   ├── campaign[]  ... ⚠️ キャンペーン情報（同人のみ出力）
│   │   ├── title      ... "50%OFF" 等
│   │   ├── date_begin ... 開始日
│   │   └── date_end   ... 終了日
│   ├── iteminfo
│   │   ├── genre[]   ... ジャンル（name, id）
│   │   ├── series[]  ... シリーズ（name, id）
│   │   ├── author[]  ... 作者（name, id）
│   │   └── maker[]   ... メーカー/出版社（name, id）
│   ├── review
│   │   ├── count   ... レビュー数
│   │   └── average ... 平均評価
│   ├── tachiyomi   ... 立ち読み情報
│   ├── volume      ... ページ数・巻数
│   ├── floor_code  ... フロアコード
│   └── floor_name  ... フロア名
```

### ⚠️ フロア別のレスポンス差異（実測データに基づく決定的事実）

**2026-04-20 に全6フロア × 100件 = 600件を実測検証した結果：**

| フロア | `list_price` | `campaign` | `deliveries` |
|---|---|---|---|
| FANZA BL（商業） | ❌ **0/100件** | ❌ **0/100件** | ❌ なし |
| FANZA TL（商業） | ❌ **0/100件** | ❌ **0/100件** | ❌ なし |
| DMM comic（商業） | ❌ **0/100件** | ❌ **0/100件** | ❌ なし |
| DMM novel（商業） | ❌ **0/100件** | ❌ **0/100件** | ❌ なし |
| らぶカル BL（同人） | ✅ **100/100件** | ✅ **35/100件** | ✅ あり |
| らぶカル TL（同人） | ✅ **100/100件** | ✅ **64/100件** | ✅ あり |

**結論**: 商業電子書籍（ebook）フロアでは、定価・セール情報・配信詳細が**一切出力されない**。
API仕様書には `list_price`（定価）や `campaign` が記載されているが、これは `doujin`（同人）フロア専用の仕様である。

---

## 4. フロアAPI（FloorList）

サービスとフロアのコード体系を取得するAPI。

### エンドポイント
```
https://api.dmm.com/affiliate/v3/FloorList
```

### 用途
- 使用可能な `service` / `floor` の組み合わせ一覧を動的に取得
- 新しいフロアが追加された時の自動検知

### Noveloveでの利用状況
現在は不使用。フロアコードはソースコード内にハードコーディングしている（変更頻度が極めて低いため）。

---

## 5. ジャンル検索API（GenreSearch）

### エンドポイント
```
https://api.dmm.com/affiliate/v3/GenreSearch
```

### リクエストパラメータ
| パラメータ | 説明 |
|---|---|
| `floor_id` | フロアID（FloorAPIから取得可能） |
| `initial` | 50音（UTF-8）で絞り込み |
| `hits` | 取得件数（初期100、最大500） |
| `offset` | 検索開始位置 |

### レスポンス
```
result
├── genre[]
│   ├── genre_id  ... ジャンルID
│   ├── name      ... ジャンル名（例: "ボーイズラブ"）
│   └── ruby      ... 読み仮名
```

### 💡 活用アイデア
- ジャンルの正式名マスターデータとして使い、タグの表記ゆれを解消
- ItemListの `article=genre` + `article_id` と連携して、特定ジャンルの新着を精密に取得

---

## 6. メーカー検索API（MakerSearch）

### エンドポイント
```
https://api.dmm.com/affiliate/v3/MakerSearch
```

### リクエストパラメータ
| パラメータ | 説明 |
|---|---|
| `floor_id` | フロアID |
| `initial` | 50音（UTF-8）で絞り込み |
| `hits` | 取得件数（初期100、最大500） |

### レスポンス
```
result
├── maker[]
│   ├── maker_id  ... メーカーID
│   ├── name      ... メーカー名（例: "ビーボーイコミックス"）
│   └── ruby      ... 読み仮名
```

### 💡 活用アイデア
- **「レーベル別一覧ページ」の自動生成**: BL/TLファンは推しレーベルで作品を追う人が多い
- SEOで「レーベル名 + 新作」を狙える

---

## 7. シリーズ検索API（SeriesSearch）

### エンドポイント
```
https://api.dmm.com/affiliate/v3/SeriesSearch
```

### リクエストパラメータ
| パラメータ | 説明 |
|---|---|
| `floor_id` | フロアID |
| `initial` | 50音（UTF-8）で絞り込み |
| `hits` | 取得件数（初期100、最大500） |

### レスポンス
```
result
├── series[]
│   ├── series_id ... シリーズID
│   ├── name      ... シリーズ名
│   └── ruby      ... 読み仮名
```

### 💡 活用アイデア
- 「○○シリーズ 全巻まとめ」ページの自動生成（将来のサイト拡張時）
- ItemListの `article=series` + `article_id` と連携して、シリーズ単位の情報取得

---

## 8. 作者検索API（AuthorSearch）

### エンドポイント
```
https://api.dmm.com/affiliate/v3/AuthorSearch
```

### リクエストパラメータ
| パラメータ | 説明 |
|---|---|
| `floor_id` | フロアID |
| `initial` | 50音（UTF-8）で絞り込み |
| `hits` | 取得件数（初期100、最大500） |

### レスポンス
```
result
├── author[]
│   ├── author_id ... 作者ID
│   ├── name      ... 作者名
│   └── ruby      ... 読み仮名
```

### 💡 活用アイデア
- 「作者名50音インデックス」の自動生成
- ItemListの `article=author` + `article_id` と連携して、同一作者の全作品を取得

---

## 9. 女優検索API（ActressSearch）

### エンドポイント
```
https://api.dmm.com/affiliate/v3/ActressSearch
```

Noveloveはコミック/ノベル系サイトのため **使用する場面なし**。

---

## 10. フロア・サイト体系の整理

### Noveloveが使用しているフロア一覧

| ブランド名 | site | service | floor | 用途 |
|---|---|---|---|---|
| FANZA BL（商業） | `FANZA` | `ebook` | `bl` | 商業BLコミック |
| FANZA TL（商業） | `FANZA` | `ebook` | `tl` | 商業TLコミック |
| DMM コミック（一般） | `DMM.com` | `ebook` | `comic` | 一般向け（BL/TL含む） |
| DMM ノベル（一般） | `DMM.com` | `ebook` | `novel` | 一般向け小説 |
| らぶカル BL（同人） | `FANZA` | `doujin` | `digital_doujin_bl` | 同人BL |
| らぶカル TL（同人） | `FANZA` | `doujin` | `digital_doujin_tl` | 同人TL |

### スクレイピングで使用する商品一覧ページのフロア指定

| ブランド名 | ドメイン | フロアパラメータ / カテゴリ |
|---|---|---|
| DMM BL（一般） | `book.dmm.com` | `floor=Gbl` |
| DMM TL（一般） | `book.dmm.com` | `floor=Gtl` |
| FANZA BL（商業） | `book.dmm.co.jp` | `category=670008` |
| FANZA TL（商業） | `book.dmm.co.jp` | `category=670009` |

### セール一覧ページの構造

```
{ベースURL}?{フロア指定}&sale=discount&discount_rate=50&page={ページ番号}
```

- `sale=discount` ... 値引き中の作品のみ表示
- `discount_rate=50` ... 50%OFF以上に絞り込み（30に変更すると30%以上）
- `page=N` ... ページネーション（1ページ最大120件）

---

## 11. セール情報の取得に関する重大な制限事項

### 問題の全容

DMM公式APIの `campaign` フィールドは、**同人（doujin）フロア専用**であり、
商業電子書籍（ebook）フロアではフィールド自体がレスポンスに含まれない。

同様に `list_price`（定価）も同人フロアでのみ出力され、
商業フロアでは `price`（現在の販売価格）のみが返される。

このため、APIだけでは商業作品のセール状態を**一切判定できない**。

### 採用した解決策（ハイブリッド方式）

```
セール判定フロー:
  ├── らぶカル（同人） → API の campaign フィールドで判定 ✅
  └── DMM/FANZA（商業） → Webスクレイピングで判定 ✅
       └── sale=discount&discount_rate=50 のページをクロール
       └── /product/{ID}/ 形式のリンクからIDを正規表現で抽出
       └── 商品が尽きるまでページ送り（最大200ページ）
```

### このハイブリッド方式の実装箇所
- **ファイル**: `nexus_revive.py`
- **関数**: `fetch_fanza_sale_product_ids()`

---

## 12. Noveloveでの現在の利用状況

### 使用中の機能

| 用途 | 使用API/手法 | 実装ファイル |
|---|---|---|
| 新着作品の取得 | ItemList (`sort=date`) | `novelove_fetcher.py` |
| ランキング取得 | ItemList (`sort=rank`) | `nexus_revive.py` |
| セール取得（同人） | ItemList (`campaign` フィールド) | `nexus_revive.py` |
| セール取得（商業） | Webスクレイピング | `nexus_revive.py` |
| あらすじ取得 | Webスクレイピング（JSON-LD / HTMLクラス） | `novelove_fetcher.py` |
| 専売判定 | ItemList (`iteminfo.genre` 内の "専売" タグ) | `novelove_fetcher.py` |
| ジャンルタグ取得 | ItemList (`iteminfo.genre`) | `novelove_fetcher.py` |

---

## 13. 将来的な活用候補

### 優先度: 高

| 活用案 | 使用API | 期待効果 |
|---|---|---|
| レーベル別カテゴリページの自動生成 | MakerSearch | SEO強化（「レーベル名 + 新作」で上位表示） |
| ジャンルタグのマスター管理・表記ゆれ解消 | GenreSearch | サイト内タグの品質向上 |

### 優先度: 中

| 活用案 | 使用API | 期待効果 |
|---|---|---|
| シリーズまとめページの自動生成 | SeriesSearch | 回遊率向上、内部リンク強化 |
| 作者50音インデックス | AuthorSearch | 辞書的なページ生成、SEOロングテール |

### 優先度: 低

| 活用案 | 使用API | 期待効果 |
|---|---|---|
| フロア一覧の動的取得 | FloorList | 新フロア追加時の自動検知（現状は手動で十分） |
