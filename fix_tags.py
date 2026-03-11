import requests
import sqlite3
import os
import time
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")
DB_FANZA = "novelove.db"
DLsite_TAG_ID = 26

def fix_mismatched_tags():
    if not WP_USER or not WP_APP_PASSWORD:
        print("WP credentials missing in .env")
        return

    auth = (WP_USER, WP_APP_PASSWORD)
    
    # 1. タグID: 26 が付いている記事を全件取得 (上限を考慮し100件)
    params = {"tags": DLsite_TAG_ID, "per_page": 100}
    print(f"--- Fetching posts with tag ID: {DLsite_TAG_ID} ---")
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    if r.status_code != 200:
        print(f"Failed to fetch posts: {r.status_code}")
        return
        
    posts = r.json()
    print(f"Found {len(posts)} posts total with DLsite tag.")

    # 2. DBと照合
    if not os.path.exists(DB_FANZA):
        print(f"{DB_FANZA} not found.")
        return
        
    conn = sqlite3.connect(DB_FANZA)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    targets = []
    for p in posts:
        slug = p["slug"]
        row = c.execute("SELECT site, title FROM novelove_posts WHERE product_id=?", (slug,)).fetchone()
        
        if row:
            site = row["site"] or ""
            if "DLsite" not in site:
                # FANZA作品なのに DLsite タグが付いている
                targets.append({"id": p["id"], "tags": p["tags"], "title": row["title"]})

    print(f"Identified {len(targets)} FANZA posts to fix.")

    # 3. タグ削除実行
    for t in targets:
        post_id = t["id"]
        old_tags = t["tags"]
        # DLsite_TAG_ID を除外した新しいタグリストを作成
        new_tags = [tag for tag in old_tags if tag != DLsite_TAG_ID]
        
        print(f"  Fixing: {t['title']} (ID: {post_id})")
        print(f"    Tags: {old_tags} -> {new_tags}")
        
        ur = requests.post(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
            auth=auth,
            json={"tags": new_tags}
        )
        
        if ur.status_code in (200, 201):
            print("    -> Updated.")
        else:
            print(f"    -> [ERROR] {ur.status_code}")
        
        time.sleep(0.5)

    conn.close()
    print("--- Process Completed ---")

if __name__ == "__main__":
    fix_mismatched_tags()
