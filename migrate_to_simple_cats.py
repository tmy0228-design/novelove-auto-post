import requests
import os
import time
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
auth = (WP_USER, WP_APP_PASSWORD)

def get_all_posts():
    posts = []
    page = 1
    while True:
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params={"per_page": 100, "page": page, "status": "publish"}, timeout=20)
        data = r.json()
        if not data or not isinstance(data, list): break
        posts.extend(data)
        if len(data) < 100: break
        page += 1
    return posts

def migrate_and_cleanup():
    # 1. カテゴリIDの対応表
    # BL, BL R-18 -> 23
    # TL, TL R-18 -> 24
    cat_id_map = {
        28: 23, # BL R-18 -> BL
        29: 24, # TL R-18 -> TL
        25: 24, # 女性向け -> TL (暫定)
    }

    posts = get_all_posts()
    for post in posts:
        pid = post["id"]
        current_cats = post["categories"]
        new_cats = []
        changed = False

        for cid in current_cats:
            if cid in cat_id_map:
                new_cid = cat_id_map[cid]
                if new_cid not in new_cats:
                    new_cats.append(new_cid)
                changed = True
            else:
                if cid not in new_cats:
                    new_cats.append(cid)
        
        if changed:
            print(f"Updating Post {pid}: {current_cats} -> {new_cats}")
            requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{pid}", auth=auth, json={"categories": new_cats}, timeout=15)
            time.sleep(0.5)

    # 2. 不要なカテゴリの削除
    old_cat_ids = [28, 29, 25]
    for cid in old_cat_ids:
        r = requests.delete(f"{WP_SITE_URL}/wp-json/wp/v2/categories/{cid}", auth=auth, params={"force": True}, timeout=15)
        if r.status_code == 200:
            print(f"Successfully deleted category ID: {cid}")
        else:
            print(f"Category ID: {cid} already deleted or error: {r.status_code}")

if __name__ == "__main__":
    migrate_and_cleanup()
