import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")

TARGET_POSTS = ["rj01570022", "rj01579048"]
CORRECT_TAG_IDS = [12, 19, 26] # 乙女向け, 同人, DLsite

def fix_specific_posts():
    if not WP_USER or not WP_APP_PASSWORD:
        print("WP credentials missing in .env")
        return

    auth = (WP_USER, WP_APP_PASSWORD)
    
    for slug in TARGET_POSTS:
        print(f"--- Processing: {slug} ---")
        # 1. 記事情報を取得
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params={"slug": slug})
        if r.status_code != 200 or not r.json():
            print(f"  [ERROR] Post not found for slug: {slug}")
            continue
            
        post = r.json()[0]
        post_id = post["id"]
        old_tags = post["tags"]
        
        print(f"  Current Tags: {old_tags}")
        print(f"  Setting Tags: {CORRECT_TAG_IDS}")
        
        # 2. タグを上書き
        ur = requests.post(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
            auth=auth,
            json={"tags": CORRECT_TAG_IDS}
        )
        
        if ur.status_code in (200, 201):
            print(f"  [SUCCESS] Updated {slug}")
        else:
            print(f"  [ERROR] {ur.status_code} for {slug}")

if __name__ == "__main__":
    fix_specific_posts()
