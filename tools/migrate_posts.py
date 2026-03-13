import requests
import os
import json
import time
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
auth = (WP_USER, WP_APP_PASSWORD)

# 新構成
# BL (全年齢): 23
# BL R-18: 28
# TL (全年齢): 24
# TL R-18: 29

def get_all_posts():
    posts = []
    page = 1
    while True:
        try:
            r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params={"per_page": 100, "page": page, "status": "publish"}, timeout=20)
            data = r.json()
            if not data or not isinstance(data, list):
                break
            posts.extend(data)
            print(f"Fetched {len(posts)} posts...")
            if len(data) < 100:
                break
            page += 1
        except Exception as e:
            print(f"Error fetching posts: {e}")
            break
    return posts

def get_tags_for_post(tag_ids):
    if not tag_ids: return []
    try:
        # Just use names from the IDs (we could cache this)
        # For simplicity, we'll check common names
        return tag_ids
    except: return []

def migrate():
    # Fetch tag names once to map IDs to names
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/tags", auth=auth, params={"per_page": 100}, timeout=15)
    tags_data = r.json()
    tag_map = {t["id"]: t["name"] for t in tags_data}
    
    posts = get_all_posts()
    for post in posts:
        pid = post["id"]
        title = post["title"]["rendered"]
        current_cats = post["categories"]
        post_tags = [tag_map.get(tid, "") for tid in post["tags"]]
        content = post["content"]["rendered"]
        
        # 本文からも判定を補完
        full_text = (title + content).lower()
        
        # R-18 判定: タグ優先、次いでタイトル・本文の明確なキーワード
        is_r18 = ("R-18" in post_tags or "R18" in post_tags or "18禁" in post_tags or
                  "18禁" in title or "【R-18版】" in title or "【R18版】" in title or
                  "r18" in full_text or "r-18" in full_text or "成人向け" in full_text)
                  
        # ジャンル判定: タグを最優先。本文検索は「単語として独立している場合」や「明確な日本語名」に限定
        is_bl = ("BL" in post_tags or "BL小説" in post_tags or "BL同人" in post_tags or "BLコミック" in post_tags or
                 "ボーイズラブ" in full_text or "bl作品" in [tag_map.get(c, "") for c in current_cats])
                 
        is_tl = ("TL" in post_tags or "TL小説" in post_tags or "TLコミック" in post_tags or "乙女向け" in post_tags or 
                 "ティーンズラブ" in full_text or "tl作品" in [tag_map.get(c, "") for c in current_cats])
        
        # 両方にヒットした場合はタグの数や明確なキーワードがある方を優先（基本は TL を優先しないと BL が強く出やすい）
        if is_tl: 
            new_cat = 29 if is_r18 else 24
        elif is_bl:
            new_cat = 28 if is_r18 else 23
        
        if new_cat and new_cat not in current_cats:
            print(f"Updating Post {pid} ({title}): Cats {current_cats} -> [{new_cat}] (R18: {is_r18})")
            try:
                r_up = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{pid}", auth=auth, json={"categories": [new_cat]}, timeout=15)
                if r_up.status_code == 200:
                    print(f"  Successfully updated.")
                else:
                    print(f"  Failed: {r_up.text}")
            except Exception as e:
                print(f"  Error: {e}")
            time.sleep(0.5)
        else:
            print(f"Skipping Post {pid} ({title}): No change needed or category unclear.")

if __name__ == "__main__":
    migrate()
