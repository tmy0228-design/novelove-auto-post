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
DB_DLSITE = "novelove_dlsite.db"

# タグIDマッピング
TAG_IDS = {
    "BL": 5,
    "BLコミック": 15,
    "BL同人": 10,
    "BL小説": 20,
    "DLsite": 26,
    "FANZA": 6,
    "R-18": 14,
    "TL": 7,
    "TLコミック": 22,
    "TL小説": 21,
    "ボイス": 27,
    "一般": 16,
    "乙女向け": 12,
    "同人": 19,
    "女性向け": 17
}

GENRE_RULE = {
    "BL": ["BL", "BL小説", "FANZA"],
    "TL": ["TL", "TL小説", "FANZA"],
    "doujin_bl": ["BL", "BL同人", "同人", "FANZA"],
    "doujin_tl": ["乙女向け", "同人", "FANZA"],
    "doujin_voice": ["同人", "FANZA"],
    "comic_bl": ["BL", "BLコミック", "一般"],
    "comic_tl": ["TL", "TLコミック", "一般"],
    "comic_women": ["女性向け", "一般"]
}

def get_product_info(slug):
    """DBから指定されたslug(product_id)の情報を取得する"""
    for db_path in [DB_FANZA, DB_DLSITE]:
        if not os.path.exists(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT genre, site FROM novelove_posts WHERE product_id=? COLLATE NOCASE", (slug,)).fetchone()
            conn.close()
            if row:
                return dict(row)
        except Exception as e:
            print(f"DB Error ({db_path}): {e}")
    return None

def calculate_tags(info):
    """ジャンルとサイト情報からタグIDリストを算出する"""
    genre = info.get("genre")
    site = info.get("site") or ""
    
    tag_names = GENRE_RULE.get(genre, [])
    
    # 追加ルール
    if site.startswith("DLsite"):
        if "DLsite" not in tag_names:
            tag_names.append("DLsite")
    
    if "r18=1" in site:
        if "R-18" not in tag_names:
            tag_names.append("R-18")
            
    # 名前をIDに変換
    tag_ids = [TAG_IDS[name] for name in tag_names if name in TAG_IDS]
    return list(set(tag_ids)) # 重複除去

def sync_all_tags():
    if not WP_USER or not WP_APP_PASSWORD:
        print("WP credentials missing in .env")
        return

    auth = (WP_USER, WP_APP_PASSWORD)
    
    # WordPressから全記事を取得
    print("--- Fetching all posts from WordPress ---")
    all_posts = []
    page = 1
    while True:
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params={"per_page": 100, "page": page, "status": "publish"})
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        all_posts.extend(data)
        print(f"  Page {page} fetched: {len(data)} posts")
        if len(data) < 100:
            break
        page += 1
        time.sleep(1)

    print(f"Total posts to process: {len(all_posts)}")

    # 各記事のタグを更新
    success_count = 0
    fail_count = 0
    skip_count = 0

    for p in all_posts:
        post_id = p["id"]
        slug = p["slug"]
        title = p["title"]["rendered"]
        
        info = get_product_info(slug)
        if not info:
            print(f"  [SKIP] No DB info for: {title} (ID: {post_id}, Slug: {slug})")
            skip_count += 1
            continue
            
        new_tag_ids = calculate_tags(info)
        current_tag_ids = p["tags"]
        
        # すでに同じならスキップ
        if set(new_tag_ids) == set(current_tag_ids):
            print(f"  [SAME] {title} (ID: {post_id})")
            success_count += 1
            continue

        print(f"  Updating: {title} (ID: {post_id})")
        print(f"    Tags: {current_tag_ids} -> {new_tag_ids}")
        
        ur = requests.post(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
            auth=auth,
            json={"tags": new_tag_ids}
        )
        
        if ur.status_code in (200, 201):
            success_count += 1
        else:
            print(f"    -> [ERROR] {ur.status_code}")
            fail_count += 1
        
        time.sleep(0.5)

    print("\n--- Process Summary ---")
    print(f"Success: {success_count}")
    print(f"Skipped: {skip_count}")
    print(f"Failed: {fail_count}")

if __name__ == "__main__":
    sync_all_tags()
