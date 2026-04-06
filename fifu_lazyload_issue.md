# FIFU画像の遅延読み込み（Lazy Load）問題 — 調査・作業報告

## 📌 課題の概要

Lighthouse（PageSpeed Insights）でNoveloveサイトを診断したところ、**デスクトップ版で「画像配信を改善する — 推定削減サイズ 3,941 KiB」**という深刻な警告が検出された。  
原因は、**FIFU（Featured Image from URL）プラグインが生成する外部画像（DMM / DLsite / らぶカル）に `loading="lazy"` 属性が付与されていない**ため、ページ読み込み時に全画像が一括ダウンロードされていること。

---

## 🔍 調査結果

### 現在のサイト構成（パフォーマンス関連）

| 項目 | 状態 | 備考 |
|:---|:---|:---|
| KUSANAGIキャッシュ | `bcache: ON` / `fcache: ON` | 正常稼働 |
| EWWWの遅延読み込み | **OFF** | 意図的にOFF（Cocoon側に一本化） |
| EWWWのWebP変換 | ON | 正常稼働 |
| Cocoonの遅延読み込み（Lazy Load） | **ON** | 高速化タブで有効化済み |
| FIFU（Featured Image from URL） | 最適化・CDN共に **OFF** | CDNは有料化リスクあり使わない方針 |

### HTMLスキャン結果（トップページ `https://novelove.jp/`）

- `<img>` タグ合計: **28枚**
- `loading="lazy"` あり: **10枚**（WordPress標準・Cocoon管轄の画像）
- `loading="lazy"` **なし**: **18枚**（**全てFIFUが生成した外部画像**）

### 原因の特定

FIFUプラグインは `post_thumbnail_html` フィルターを使って、WordPressが標準で出力するサムネイルHTMLを**外部画像URLに丸ごとすり替え**ている。  
この際、WordPressやCocoonが事前に付与した `loading="lazy"` 属性が**消失**してしまう。

FIFUのソースコード（`includes/thumbnail.php`）を確認した結果、FIFUは独自のlazy load処理（`fifu-lazy="1"` / `fifu-data-src` 属性）を使っているが、これは**FIFUの「最適化」設定をONにしないと動作しない**。  
しかし「最適化」をONにすると**FIFUのCDN（海外サーバー）経由**になるため、以下のリスクがある：
- 無料枠の制限で画像が突然表示されなくなる
- 将来的に有料化（FIFU設定画面に「まもなく少額の料金が導入される可能性があります」と明記あり）

---

## ⚠️ 試行した対策と結果

### 試行1: `post_thumbnail_html` フィルターで `loading="lazy"` を強制付与

```php
// functions.php に追記（cocoon-child-master）
add_filter( 'post_thumbnail_html', function( $html ) {
    if ( strpos( $html, 'fifu-featured' ) !== false ) {
        $html = preg_replace( '/loading="[^"]*"/', '', $html );
        $html = str_replace( '<img ', '<img loading="lazy" ', $html );
    }
    return $html;
}, 99999 );
```

**結果: ❌ 効果なし**

FIFUが `post_thumbnail_html` フィルターの**最終出力後**にHTMLを書き換えている可能性が高い（JavaScript側での書き換え、またはフィルター外の独自出力パスを使用）。  
優先度を `20` → `99` → `99999` と段階的に上げたが、いずれも効果がなかった。

> **注意**: この試行コードは `functions.php` からロールバック済み（バックアップからの復元完了）。現在のfunctions.phpは作業前の状態に戻っている。

---

## 📋 次のステップ（未実施）

### 方針A: FIFUの出力パスを完全に特定する（推奨）

FIFUプラグインのソースコード（特に `includes/thumbnail.php`）を詳細に読み、以下を確認する必要がある：
1. `post_thumbnail_html` フィルター以外に画像を出力するパスがあるか
2. FIFUがJavaScript側で `<img>` タグを動的に書き換えていないか
3. FIFUの独自フィルター（`fifu_*`）にフックできるポイントがあるか

### 方針B: `wp_get_attachment_image_attributes` フィルターを使う

`post_thumbnail_html` ではなく、より上流の `wp_get_attachment_image_attributes` フィルターで属性を注入する。

### 方針C: テーマ側（Cocoon）の出力バッファで最終HTMLを書き換える

`ob_start` / `ob_end_flush` を使って、ページ全体のHTMLが出力される直前に、FIFUの `<img>` タグを一括で書き換える。  
**最も確実だが、パフォーマンスへの影響を要検証。**

```php
// 方針Cの実装イメージ
add_action('template_redirect', function() {
    ob_start(function($html) {
        return preg_replace(
            '/<img([^>]*fifu-featured[^>]*)>/i',
            '<img loading="lazy" $1>',
            $html
        );
    });
});
```

### 方針D: FIFU以外のプラグインに乗り換える

FIFUの代わりに、外部画像URLをアイキャッチに設定できる別プラグイン（例: `External URL` など）を検討する。ただし、既存の1000件以上の記事への影響を要確認。

---

## 📊 Lighthouse診断結果サマリー（2026年4月6日 16:24 JST / デスクトップ）

| 指標 | 値 | 評価 |
|:---|:---|:---|
| Total Blocking Time | 280 ミリ秒 | 🟠 |
| Cumulative Layout Shift | 0.032 | 🟢 |
| Speed Index | 1.3 秒 | 🟢 |

### 主な改善項目（インサイト）

| 項目 | 推定削減 | 深刻度 |
|:---|:---|:---|
| **画像配信を改善する** | **3,941 KiB** | 🔴 最重要 |
| 効率的なキャッシュ保存期間を使用する | 2,521 KiB | 🔴 |
| レンダリングをブロックしているリクエスト | 420 ミリ秒 | 🔴 |
| フォント表示 | 50 ミリ秒 | 🟠 |
| 使用していないCSSの削減 | 153 KiB | 🔴 |
| 使用していないJavaScriptの削減 | 267 KiB | 🟠 |
| 過大なネットワークペイロードの回避 | 合計 6,944 KiB | 🟠 |

### Nginxキャッシュヘッダーの問題

```
x-b-cache: BYPASS
x-f-cache: MISS
```

KUSANAGIの `bcache` / `fcache` はONだが、実際のレスポンスではキャッシュが**BYPASSまたはMISS**になっている。  
WordPressのログインCookieや動的コンテンツの影響で、キャッシュが有効に利用されていない可能性がある。

---

## 🛡️ 現在のサイト安全状態

- ✅ `functions.php` はバックアップから**復元済み**（作業前の状態）
- ✅ Cocoon設定は変更なし（Lazy Load: ON のまま）
- ✅ EWWW設定は変更なし（遅延読み込み: OFF / WebP変換: ON のまま）
- ✅ FIFU設定は変更なし（最適化: OFF / CDN: OFF のまま）
