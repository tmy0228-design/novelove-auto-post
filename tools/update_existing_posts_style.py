#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests
import json
import time
import re

# === 設定 ===
WP_SITE_URL     = "https://novelove.jp"
WP_USER         = "tomomin"
WP_APP_PASSWORD = "FDn0z9epvJDer5v5inalAFPj"

# カテゴリIDとラベルの対応
# 23: BL作品 -> BL小説 or BL同人 or BLコミック
# 24: TL作品 -> TL小説 or 乙女向け同人 or TLコミック
# 25: 女性向け -> 女性向けコミック or 女性向けボイス作品 or PCゲーム
CAT_TO_GENRE = {
    23: "BL",
    24: "TL",
    25: "女性向け"
}

def get_badge_html(site_display, genre_label):
    icon = "📖"
    if "ボイス" in genre_label: icon = "🎧"
    elif "コミック" in genre_label or "漫画" in genre_label: icon = "🎨"
    elif "同人" in genre_label: icon = "📚"
    
    return f'''
<p style="text-align:center; margin-bottom:20px;">
<span style="background:#fefefe; border:1px solid #ddd; padding:6px 16px; border-radius:25px; font-weight:bold; color:#444; box-shadow:0 2px 4px rgba(0,0,0,0.05); display:inline-block;">{icon} {site_display} {genre_label}</span>
</p>'''

def update_all_posts():
    auth = (WP_USER, WP_APP_PASSWORD)
    
    page = 1
    total_updated = 0
    total_skipped = 0

    while True:
        # 1. 記事一覧取得
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts?per_page=50&page={page}", auth=auth, timeout=30)
        if r.status_code != 200:
            break
        
        posts = r.json()
        if not posts:
            break
        
        print(f"--- Processing Page {page} ---")
        
        for post in posts:
            post_id = post["id"]
            title = post["title"]["rendered"]
            content = post["content"]["raw"] if "raw" in post["content"] else post["content"]["rendered"]
            categories = post["categories"]
            
            # すでにバッジがあるかチェック
            if 'border-radius:25px; font-weight:bold; color:#444;' in content:
                # print(f"  [Skip] Badge exists: {title}")
                total_skipped += 1
                continue
            
            # ニュースカテゴリ(4)は除外
            if 4 in categories:
                continue

            # 2. ジャンル判定
            genre_label = "作品"
            if 23 in categories: genre_label = "BL作品"
            elif 24 in categories: genre_label = "TL作品"
            elif 25 in categories: genre_label = "女性向け作品"
            
            # さらに本文から詳細を推測
            if "同人" in content: genre_label = genre_label.replace("作品", "同人")
            elif "コミック" in content or "漫画" in content: genre_label = genre_label.replace("作品", "コミック")
            elif "小説" in content: genre_label = genre_label.replace("作品", "小説")
            elif "ボイス" in content: genre_label = "女性向けボイス作品"
            elif "ゲーム" in content: genre_label = "PCゲーム"

            # 3. サイト判定
            site_display = "FANZA" # デフォルト
            if "dlsite.com" in content.lower() or "dlsite" in title.lower():
                site_display = "DLsite"
            elif "dmm.com" in content.lower() or "fanza.com" in content.lower():
                site_display = "FANZA"
            
            # 4. バッジ挿入
            badge = get_badge_html(site_display, genre_label)
            new_content = badge + "\n" + content
            
            # 5. 更新実行
            update_r = requests.post(
                f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
                auth=auth,
                json={"content": new_content},
                timeout=20
            )
            
            if update_r.status_code == 200:
                print(f"  [Success] Updated: {title}")
                total_updated += 1
            else:
                print(f"  [Failed] {title} ({update_r.status_code})")
            
            time.sleep(0.5)

        page += 1
        if page > 20: # 安全のため上限
            break

    print(f"\nCompleted. Updated: {total_updated}, Skipped: {total_skipped}")

if __name__ == "__main__":
    update_all_posts()
