import requests
import sqlite3
import os
import time
import re
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")
DB_FANZA = "novelove.db"
DB_DLSITE = "novelove_dlsite.db"

# タグIDマッピング
TAG_IDS = {
    "BL": 5, "BLコミック": 15, "BL同人": 10, "BL小説": 20, "DLsite": 26,
    "FANZA": 6, "R-18": 14, "TL": 7, "TLコミック": 22, "TL小説": 21,
    "ボイス": 27, "一般": 16, "乙女向け": 12, "同人": 19, "女性向け": 17
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

def get_product_info_from_db(slug):
    for db_path in [DB_FANZA, DB_DLSITE]:
        if not os.path.exists(db_path): continue
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT genre, site FROM novelove_posts WHERE product_id=? COLLATE NOCASE", (slug,)).fetchone()
            conn.close()
            if row: return dict(row)
        except: pass
    return None

def estimate_info_from_wp(post):
    """DBにない場合、WPの情報からジャンルとサイトを推定する"""
    content = post["content"]["rendered"]
    categories = post["categories"]
    
    # サイト判定
    site = "FANZA" # デフォルト
    if "dlsite" in content.lower() or post["slug"].startswith("rj"):
        site = "DLsite"
    if "r18=1" in content or "R18版" in post["title"]["rendered"] or "18禁" in post["title"]["rendered"]:
        site += ":r18=1"

    # ジャンル判定
    genre = "BL" # デフォルト
    if 23 in categories: # BL作品
        genre = "BL"
    elif 24 in categories: # TL作品
        genre = "TL"
    
    # より詳細な判定（本文のバッジなどから）
    if "同人" in content:
        genre = "doujin_bl" if genre == "BL" else "doujin_tl"
    if "ボイス" in content:
        genre = "doujin_voice"
    if "コミック" in content or "漫画" in content:
        genre = "comic_bl" if genre == "BL" else "comic_tl"

    return {"genre": genre, "site": site}

def calculate_tags(info):
    genre = info.get("genre")
    site = info.get("site") or ""
    tag_names = GENRE_RULE.get(genre, ["その他"]) # pcgame等がない場合の予備
    
    if site.startswith("DLsite"):
        if "DLsite" not in tag_names: tag_names.append("DLsite")
    if "r18=1" in site:
        if "R-18" not in tag_names: tag_names.append("R-18")
            
    return list(set([TAG_IDS[name] for name in tag_names if name in TAG_IDS]))

def final_sync():
    if not WP_USER or not WP_APP_PASSWORD: return
    auth = (WP_USER, WP_APP_PASSWORD)
    
    print("--- Fetching all posts ---")
    all_posts = []
    page = 1
    while True:
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params={"per_page": 100, "page": page})
        if r.status_code != 200: break
        data = r.json()
        if not data: break
        all_posts.extend(data)
        if len(data) < 100: break
        page += 1
        time.sleep(1)

    print(f"Processing {len(all_posts)} posts...")
    for p in all_posts:
        info = get_product_info_from_db(p["slug"])
        source = "DB"
        if not info:
            info = estimate_info_from_wp(p)
            source = "WP_ESTIMATE"
            
        new_tag_ids = calculate_tags(info)
        if set(new_tag_ids) == set(p["tags"]):
            print(f"  [SAME] {p['title']['rendered'][:30]}... ({source})")
            continue

        print(f"  Updating: {p['title']['rendered'][:30]}... ({source})")
        requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{p['id']}", auth=auth, json={"tags": new_tag_ids})
        time.sleep(0.3)

if __name__ == "__main__":
    final_sync()
    print("--- Completed ---")
