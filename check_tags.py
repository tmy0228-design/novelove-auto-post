import requests
import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")
DB_FANZA = "novelove.db"

def check_mismatched_tags():
    if not WP_USER or not WP_APP_PASSWORD:
        print("WP credentials missing in .env")
        return

    auth = (WP_USER, WP_APP_PASSWORD)
    
    # 1. タグID: 26 (DLsite) が付いている記事を取得
    params = {"tags": 26, "per_page": 100}
    print(f"--- Fetching posts with tag ID: 26 (DLsite) from {WP_SITE_URL} ---")
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    if r.status_code != 200:
        print(f"Failed to fetch posts: {r.status_code}")
        return
        
    posts = r.json()
    print(f"Found {len(posts)} posts with DLsite tag.")

    # 2. novelove.db (FANZA用) と照合
    if not os.path.exists(DB_FANZA):
        print(f"{DB_FANZA} not found.")
        return
        
    conn = sqlite3.connect(DB_FANZA)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    mismatched = []
    for p in posts:
        slug = p["slug"]
        # DBで site 情報を確認
        row = c.execute("SELECT site, title FROM novelove_posts WHERE product_id=?", (slug,)).fetchone()
        
        if row:
            site = row["site"] or ""
            if "DLsite" not in site:
                # FANZA作品なのに DLsite タグが付いている
                mismatched.append({"id": p["id"], "title": row["title"], "slug": slug, "site": site})
        else:
            # novelove.db にない = FANZA作品ではない可能性があるが、指示では site NOT LIKE 'DLsite%' を確認せよとのこと
            # 一旦保留し、DLsite用のDBも確認するか、slugの形式等で判断
            pass

    print(f"\nTotal mismatched posts identified: {len(mismatched)}")
    for m in mismatched[:10]:
        print(f"  ID: {m['id']}, Slug: {m['slug']}, Site: {m['site']}, Title: {m['title']}")
        
    conn.close()

if __name__ == "__main__":
    check_mismatched_tags()
