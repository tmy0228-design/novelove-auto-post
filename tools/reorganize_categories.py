import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
auth = (WP_USER, WP_APP_PASSWORD)

def get_or_create_category(name, slug):
    try:
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/categories", auth=auth, params={"slug": slug}, timeout=15)
        hits = r.json()
        if isinstance(hits, list) and hits:
            # 既存カテゴリの名前を更新
            cat_id = hits[0]["id"]
            if hits[0]["name"] != name:
                r_up = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/categories/{cat_id}", auth=auth, json={"name": name}, timeout=15)
                print(f"Updated category name: {name} (ID: {cat_id})")
            else:
                print(f"Category exists: {name} (ID: {cat_id})")
            return cat_id
            
        # 新規作成
        payload = {"name": name, "slug": slug}
        r_new = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/categories", auth=auth, json=payload, timeout=15)
        res = r_new.json()
        cat_id = res.get("id")
        print(f"Created category: {name} (ID: {cat_id})")
        return cat_id
    except Exception as e:
        print(f"Error handling category {name}: {e}")
        return None

if __name__ == "__main__":
    targets = [
        {"name": "BL", "slug": "bl"},
        {"name": "TL", "slug": "tl"},
        {"name": "ランキング", "slug": "ranking"},
        {"name": "セール", "slug": "sale"},
    ]
    
    cat_map = {}
    for target in targets:
        cid = get_or_create_category(target["name"], target["slug"])
        if cid:
            cat_map[target["name"]] = cid
            
    print("\n[Result] Category Mapping:")
    print(json.dumps(cat_map, indent=4, ensure_ascii=False))
