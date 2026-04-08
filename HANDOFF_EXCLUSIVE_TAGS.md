# 専売タグシステム 修復・引継ぎ書（v13.7.2）

> **作成日**: 2026-04-09  
> **対象**: 全サイトの独占・専売タグ（`is_exclusive` フラグ + WordPress タグ）

---

## 1. 背景と経緯

v13.5.1 で「FANZA独占」→「FANZA専売」にタグ名を誤って変更したことで、WordPress のフィルターメニューとの不整合が発生し、全記事の専売タグが消失する重大障害が発生。その修復過程で以下のバグも連鎖的に発見・修正した。

## 2. 正式タグ名（絶対に変更禁止）

| サイト | 正式タグ名 | 判定方法 |
| :--- | :--- | :--- |
| **DLsite** | `DLsite専売` | HTMLの `type_exclusive` クラス or `title="専売"` 属性 |
| **FANZA同人** | `FANZA独占` | HTMLの `c_icon_exclusive` クラス（要 `age_check_done` cookie） |
| **FANZA商業** | `FANZA独占` | `__NEXT_DATA__` JSON内の `"独占販売"` or `span` テキスト `独占` |
| **DigiKet** | `DigiKet限定` | HTMLの `digiket.gif` バッジ画像 or `<a>DiGiket限定</a>` |
| **らぶカル** | `らぶカル独占` | URLに `lovecul.dmm` を含む場合に `c_icon_exclusive` で判定 |

> ⚠️ **「FANZA専売」は誤称**。仕様書（SPECIFICATIONS.md §2-3）では「FANZA独占」が正式。

---

## 3. 各サイトの修復状況

### 3-1. DLsite（✅ 完了）

| 項目 | 状態 |
|---|---|
| ローカルDB `is_exclusive` | ✅ 正確（65件） |
| サーバーDB `is_exclusive` | ✅ ローカルから同期済み |
| WPタグ `DLsite専売` | ✅ 65件付与済み |
| 判定ロジック | ✅ 正常動作確認済み（RJ01400975, RJ01538553で検証） |

**注意点**: DBの `site` カラムは `DLsite:r18=0` / `DLsite:r18=1` の形式。タグ付与スクリプトでは部分一致（`dlsite` を含む）で判定する必要がある。

### 3-2. FANZA同人（✅ 完了）

| 項目 | 状態 |
|---|---|
| ローカルDB `is_exclusive` | ✅ 正確（12件） |
| サーバーDB `is_exclusive` | ✅ ローカルから同期済み |
| WPタグ `FANZA独占` | ✅ 12件付与済み |
| 判定ロジック | ✅ 正常（`_make_fanza_session()` で `age_check_done=1` cookie付与が必須） |

**注意点**: DBの `site` カラムは `FANZA:r18=1`。一部の作品はURLが `lovecul.dmm.co.jp` だが `site=FANZA:r18=1` のケースがある（→ URL判定で `らぶカル独占` に正しく振り分けられる）。

### 3-3. らぶカル（✅ 完了）

| 項目 | 状態 |
|---|---|
| ローカルDB `is_exclusive` | ✅ 正確（13件: Lovecal:r18=1 が10件 + lovecul URL持ちFANZA:r18=1 が3件） |
| サーバーDB `is_exclusive` | ✅ ローカルから同期済み |
| WPタグ `らぶカル独占` | ✅ 13件付与済み |
| 判定ロジック | ✅ URLに `lovecul.dmm` を含む場合に自動でタグ名を「らぶカル独占」に切り替え |

### 3-4. DigiKet（🔄 修正中 — バックグラウンドで実行中）

| 項目 | 状態 |
|---|---|
| ローカルDB `is_exclusive` | 🔄 再スクレイピングで修正中（`tools/backfill_digiket_exclusive.py`） |
| サーバーDB `is_exclusive` | 🔄 上記スクリプトが同期する |
| WPタグ `DigiKet限定` | 🔄 上記スクリプトが付与する |
| 判定ロジック | ✅ 修正済み（v13.7.2: 戻り値の受け漏れバグ修正） |

**発見されたバグ（v13.7.2で修正）**:
- `fetch_digiket_items()` L983 で `scrape_digiket_description()` を呼ぶ際、6値返す関数を5値で受けており、6番目の `is_exclusive` が**常に破棄**されていた。
- 代替の正規表現判定も、サイドバーメニュー「DiGiket限定作品」に全ページでマッチする設計欠陥があり、例外処理で `False` にフォールバックしていた。
- **結果**: v12.7.0（初実装）から約2週間、DigiKet限定タグが一切付与されていなかった。

**確認方法**:
```
# ログ確認（ローカル）
type tools\backfill_digiket.log

# サーバー側ログ確認
ssh root@210.131.218.83 "tail -20 /home/kusanagi/scripts/fix_digiket_tags.log"

# WPタグ件数確認
python tools\count_wp_tags.py
```

---

## 4. 修正したファイル一覧

| ファイル | 修正内容 |
|---|---|
| `novelove_fetcher.py` L983 | DigiKet: `scrape_digiket_description()` の戻り値を6値で受け取るように修正 |
| `novelove_fetcher.py` L997 | DigiKet: 冗長な正規表現フォールバック判定を廃止 |
| `auto_post.py` L1144 | 「FANZA専売」→「FANZA独占」に修正（仕様書準拠） |
| `SPECIFICATIONS.md` §2-3 | 正式タグ名テーブル、判定ロジック詳細、事故教訓、DigiKetバグ詳細を追記 |
| `CHANGELOG.md` | v13.7.2 エントリを追加 |

---

## 5. DB内の `site` カラム値マッピング（重要）

タグ付与スクリプトを書く時、完全一致ではなく**部分一致**で判定する必要がある。

| DB上の `site` 値 | 正しいタグ名 |
|---|---|
| `DLsite:r18=0` | `DLsite専売` |
| `DLsite:r18=1` | `DLsite専売` |
| `FANZA:r18=1` (URL=dmm.co.jp) | `FANZA独占` |
| `FANZA:r18=1` (URL=lovecul.dmm) | `らぶカル独占` |
| `Lovecal:r18=1` | `らぶカル独占` |
| `DigiKet` / `DigiKet:r18=0` / `DigiKet:r18=1` | `DigiKet限定` |

---

## 6. サーバーへのデプロイ手順（次回作業者向け）

修正済みファイルをサーバーにデプロイする手順:

1. **GitHub push**（このMDと一緒に実施済み）
2. **サーバーで git pull**:
   ```
   ssh root@210.131.218.83 "cd /home/kusanagi/scripts && git pull"
   ```
3. **動作確認**: 次回の `fetch_digiket_items()` 実行時に、DigiKet限定作品の `is_exclusive` が正しく1にセットされることを確認。

---

## 7. 運用ツール

| ツール | 用途 |
|---|---|
| `tools/count_wp_tags.py` | WP上の専売タグ件数を一覧表示 |
| `tools/backfill_digiket_exclusive.py` | DigiKet既存記事のis_exclusiveを再判定してWPタグ付与 |
| `tools/restore_exclusive_tags_v2.py` | ローカルDBのis_exclusiveをもとにWPタグを全件再付与 |
| `tools/check_all_site_labels.py` | 全DBのsiteカラム値を一覧表示 |

---

## 8. 最終タグ件数（2026-04-09 07:20 時点）

| タグ | WP上 | ローカルDB | 備考 |
|---|---|---|---|
| `DLsite専売` | 65 | 65 | ✅ |
| `FANZA独占` | 12 | 12 | ✅ |
| `らぶカル独占` | 13 | 13 | ✅ |
| `DigiKet限定` | 0 | 0→🔄修正中 | バックフィル実行中 |
| `FANZA専売`(誤名) | 0 | - | ✅ 除去済み |
