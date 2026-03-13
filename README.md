# Novelove Auto Post Engine (v8.3.1)

BL・TL・女性向けコンテンツ特化型 自動投稿・レビュー生成システム

---

## 🚀 Project Snapshot (for AI & Agents)

| Item | Value |
| :--- | :--- |
| **Primary AI** | **DeepSeek-V3 / R1** (Writing & Scoring) |
| **Target Sites** | FANZA, DMM.com (ebook), DLsite (Girls/BL) |
| **Platform** | WordPress (Cocoon Theme / Kusanagi VPS) |
| **Logic Core** | 0-5 Scoring, 3-Masking Levels, 5 Character Personas |
| **Truth Source** | **VPS Server Environment** (NOT GitHub Raw cache) |

---

## ⚠️ 運用上の鉄則 (Critical Rules)

### 1. 「真実は VPS にある」原則
GitHub の `raw` ファイル（CDN経由）は強烈なキャッシュにより、push直後でも**数分〜数十分古いコード**を返すことがあります。エージェントがGitHubのコードを直接Fetchして読み取ると、破壊的なデグレードを引き起こすリスクがあります。

**最新状態の確認は必ず以下のコマンドで行うこと：**
```bash
cd /home/kusanagi/scripts
git log --oneline -5
```
GitHub 上の表示ではなく、VPS 側で `git log` を叩いて得られる内容のみを「正信」とします。

### 2. 認証情報の管理
APIキーやWPパスワードはすべて `/home/kusanagi/scripts/.env` に秘匿されています。コード内に直接記述しないでください。

---

## 🧠 AI Intelligence Logic

### 1. 0-5点プレスコアリング (Pre-Scoring)
記事執筆前に、AIが作品情報を以下の基準で判定します。
- **4〜5点**: 合格。記事執筆へ移行。
- **0〜3点**: 不採用。特に「AIスコア4点」が現在の足切りラインです。
- **0点定義**: 非マンガ（ボイス単体、動画）、外国語作品、ジャンル違い。

### 2. 3段階マスキングシステム (Masking)
性的表現をWordPressのポリシー（およびSEO）に適合させるため、以下の処理を行います。
- `level=0`: 無加工。
- `level=1`: 軽度（直接的な単語を「●●●」やマイルドな表現に置換）。
- `level=2`: 強力（官能的な描写を「愛の雫」「秘めた部分」等へ文学的に置換）。

### 3. キャラクター・ペルソナ (Reviewers)
記事は以下の5人からランダムに選ばれたライターの視点で執筆されます。
- **紫苑 (Shion)**: クール毒舌腐女子OL。BL同人誌担当。
- **茉莉花 (Marika)**: 陽キャカフェ店員。TL・ボイス担当。
- **葵 (Aoi)**: オタク早口大学生。属性萌え重視。
- **桃香 (Momoka)**: 深夜にボイスを聴く主婦。大人目線。
- **蓮 (Ren)**: 眼鏡インテリ院生。学術的（建前）に分析。

---

## 🛠 Directory Structure

```text
/home/kusanagi/scripts/
├── auto_post.py             # メインエンジン (v8.3.1)
├── CHANGELOG.md             # 開発・修正の全履歴
├── README.md                # 本文書 (プロジェクトの聖典)
├── .env                     # 認証情報 (Git管理不可)
├── novelove.db              # FANZA/DMM用 投稿追跡DB
├── novelove_dlsite.db       # DLsite用 投稿追跡DB
└── tools/                   # メンテナンス用ツールキット
    ├── clean_db_noise.py    # DBのゴミ掃除
    ├── list_tags.py         # 現行タグ一覧
    └── migrate_posts.py     # 記事移行ユーティリティ等
```

---

## 🔄 開発の歴史と背景 (Context)

- **v8.3.1**: クレジットの iframe 化を試みるも、WordPress の表示制限により断念。**公式画像（img）リンクへ回帰**。
- **v8.3.0**: 10分おきの更新を「1時間おき」に最適化し、サーバー負荷とクールダウン速度を調整。
- **v8.2.0**: 複雑な24枠ローテーションを「重複なしの6枠」へ単純化し、運用の透明性を向上。

---
*Last Updated: 2026-03-13 / NoveLove Project*
