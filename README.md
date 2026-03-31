# Novelove 自動投稿システム (v12.8.0)

> [!IMPORTANT]
> **開発・修正時の注意**: 作業を開始する前に、必ず `git pull origin main` で最新版をプルしてから作業を行ってください。

Novelove（ノベラブ）のコンテンツ生成および自動投稿を行うエンジン一式です。
BL・TL の女性向け作品（漫画・小説・同人）を全自動でレビュー記事化して WordPress に投稿します。

## 🚀 システム概要
- **目的**: FANZA, DLsite, DMMブックス, DigiKet の新着・ランキングを取得し、DeepSeek AIでレビュー記事を生成して WordPress に自動投稿する。
- **主要言語**: Python 3.x
- **データベース**: SQLite (`novelove.db`, `novelove_dlsite.db`, `novelove_digiket.db`)

## 🗂️ ファイル構成
| ファイル | 役割 |
| :--- | :--- |
| `auto_post.py` | メインエンジン。取得→審査→執筆→WP投稿の全フロー制御 |
| `novelove_soul.py` | AIライター5名のキャラクター設定・関係性マトリクス・執筆ルール |
| `novelove_core.py` | DB接続・Discord通知・アフィリエイトボタン等の共通インフラ |
| `novelove_fetcher.py` | 各サイトのスクレイピング・フィルタリングロジック |

## 🤖 AIライター（5名）
| ID | 名前 | 担当ジャンル | 性格 |
| :--- | :--- | :--- | :--- |
| `shion` | 紫苑 | BL | クールで毒舌なOL。分析的で「解釈一致」が口癖 |
| `marika` | 茉莉花 | TL | 明るいカフェ店員。「ヤバい！！」と叫ぶ高テンション |
| `aoi` | 葵 | BL | BL大学生。コミケ猛者・早口の限界オタク |
| `momoka` | 桃香 | TL | 2児の主婦。大人の落ち着きとときめきの落差 |
| `ren` | 蓮 | BL | 眼鏡インテリ院生。「学術資料」と言い張る天然系 |

- 通常投稿: **90%が専門ジャンル担当 / 10%がゲスト（専門外）登板**
- ランキング投稿: **MCとゲストの2名で掛け合い形式**（全10パターンの関係性マトリクス参照）

## ✨ 主な機能 (v12.8.0)

### 1. 2名掛け合いランキング記事
毎週末のランキング記事をMC＋ゲストの**掛け合い（吹き出し対話）形式**で生成。
5名×4名=全10通りの関係性を `novelove_soul.py` の `RELATIONSHIPS` 辞書で定義し、プロンプトに自動注入します。

### 2. AI SEOメタ自動生成・付与
通常・ランキングともに、DeepSeekが記事本文をベースに強力なSEO特化のテキストを生成。
- **seo_title**: 『作品名』＋AIが考えた感情を揺さぶる惹き句（68文字以内）
- **meta_desc**: あらすじの単なる抜粋ではなく、誰のどんな性癖に刺さるかを端的に伝える紹介文（80文字程度）

AI生成できた場合は優先採用、できなかった場合はテンプレートにフォールバック。

### 3. 多段ガードレールと関所（Cooldown）システム
- **サーキットブレーカー**: 連続エラー3回 or 5分超過で自動停止 → Discord緊急通知（`emergency_stop.lock`生成）
- **関所（冷却機能）**: SQLiteのロックエラーを防ぐため、cron多重起動時の安全弁として冷却ロジックを実装。
- **クールダウン**: 通常投稿55分・ランキング12時間の独立した間隔制御
- **重複防止**: DB全件 + WordPressの公開記事タイトルとの照合により二重投稿を100%排除し、SQLite固有の `ORDER BY 0` エラーも防ぐ堅牢なSQL構造。

## 🤖 AIモデル構成
| 役割 | モデル |
| :--- | :--- |
| スコアリング / 執筆 / SEO生成 | `deepseek-chat (DeepSeek-V3)` |

## 🔄 作業フロー
1. ローカルで修正・検証を行う。
2. `git push origin main` で GitHub へ反映。
3. サーバー（Kusanagi）側で `git pull` を実行して最新版へ更新。

## ⏰ 定期実行設定 (cron)
```bash
# 30分おきに通常投稿
*/30 * * * * cd /home/kusanagi/scripts && /opt/kusanagi/bin/python3 auto_post.py >> /home/kusanagi/scripts/novelove.log 2>&1

# ランキング生成（曜日別: 日=FANZA, 月=DLsite, 火=DMM, 水=DigiKet）
0 22 * * * cd /home/kusanagi/scripts && /opt/kusanagi/bin/python3 auto_post.py --ranking >> /home/kusanagi/scripts/novelove.log 2>&1
```

## 📜 変更履歴
詳細は [CHANGELOG.md](./CHANGELOG.md) を参照してください。


