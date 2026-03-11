import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")

def check_wp_posts():
    if not WP_USER or not WP_APP_PASSWORD:
        print("WP credentials missing in .env")
        return

    auth = (WP_USER, WP_APP_PASSWORD)
    
    # DLsiteの作品ID（RJから始まるもの）を含む記事を検索、または本文に novelove-001 を含むものを検索
    params = {
        "search": "novelove-001",
        "per_page": 100
    }
    
    print("--- Searching WordPress posts for 'novelove-001' ---")
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    
    if r.status_code != 200:
        print(f"Failed to fetch posts: {r.status_code}")
        print(r.text)
        return
        
    posts = r.json()
    print(f"Found {len(posts)} posts with 'novelove-001'")
    
    for p in posts:
        # タグの確認
        tag_ids = p.get("tags", [])
        tags_r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/tags", auth=auth, params={"include": ",".join(map(str, tag_ids))})
        tag_names = [t["name"] for t in tags_r.json()]
        
        print(f"ID: {p['id']}, Title: {p['title']['rendered']}")
        print(f"  Link: {p['link']}")
        print(f"  Tags: {tag_names}")
        # FANZAタグが含まれているかチェック
        if "FANZA" in tag_names:
            print("  [ALERT] Contains 'FANZA' tag")

check_wp_posts()
