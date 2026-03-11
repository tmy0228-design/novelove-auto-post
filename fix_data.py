import sqlite3
import os
import requests
import re
from dotenv import load_dotenv
import time

load_dotenv()

DB_DLSITE = "novelove_dlsite.db"
WP_SITE_URL = "https://novelove.jp"
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")

def fix_data():
    if not WP_USER or not WP_APP_PASSWORD:
        print("WP credentials missing in .env")
        return

    auth = (WP_USER, WP_APP_PASSWORD)
    
    # 1. DBの修正 (novelove-001 -> novelove)
    print("--- Fixing Database: novelove_dlsite.db ---")
    if os.path.exists(DB_DLSITE):
        conn = sqlite3.connect(DB_DLSITE)
        c = conn.cursor()
        
        # 件数確認
        count = c.execute("SELECT COUNT(*) FROM novelove_posts WHERE site LIKE 'DLsite%' AND affiliate_url LIKE '%novelove-001%';").fetchone()[0]
        print(f"Found {count} items in DB with wrong ID.")
        
        if count > 0:
            c.execute("UPDATE novelove_posts SET affiliate_url=REPLACE(affiliate_url, '?affiliate_id=novelove-001', '?affiliate_id=novelove') WHERE site LIKE 'DLsite%';")
            conn.commit()
            print("DB Update successful.")
        conn.close()
    else:
        print("novelove_dlsite.db not found.")

    # 2. WordPressの修正
    print("\n--- Fixing WordPress Posts ---")
    
    # タグIDの取得（DLsiteタグを探す、なければ作る）
    def get_tag_id(name):
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/tags", auth=auth, params={"search": name})
        hits = r.json()
        if hits and isinstance(hits, list):
            for h in hits:
                if h["name"] == name: return h["id"]
        # 作成
        r = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/tags", auth=auth, json={"name": name})
        return r.json().get("id")

    dlsite_tag_id = get_tag_id("DLsite")
    fanza_tag_id = get_tag_id("FANZA")
    print(f"Tag IDs - DLsite: {dlsite_tag_id}, FANZA: {fanza_tag_id}")

    # 記事の検索（novelove-001 を含む記事）
    params = {"search": "novelove-001", "per_page": 100}
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    if r.status_code != 200:
        print("Failed to fetch posts")
        return
        
    posts = r.json()
    print(f"Processing {len(posts)} posts on WordPress...")

    for p in posts:
        post_id = p["id"]
        title = p["title"]["rendered"]
        content = p["content"]["raw"] if "raw" in p["content"] else p["content"]["rendered"]
        tags = p["tags"]
        
        print(f"  Fixing: {title} (ID: {post_id})")
        
        # URL置換
        new_content = content.replace("?affiliate_id=novelove-001", "?affiliate_id=novelove")
        
        # タグ修正
        new_tags = [t for t in tags if t != fanza_tag_id]
        if dlsite_tag_id not in new_tags:
            new_tags.append(dlsite_tag_id)
            
        # 更新
        update_data = {
            "content": new_content,
            "tags": new_tags
        }
        ur = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}", auth=auth, json=update_data)
        if ur.status_code in (200, 201):
            print("    -> Updated.")
        else:
            print(f"    -> [ERROR] Failed to update: {ur.status_code}")
        
        time.sleep(1)

if __name__ == "__main__":
    fix_data()
