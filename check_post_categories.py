import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
auth = (WP_USER, WP_APP_PASSWORD)

def check_all_cats():
    # Fetch categories
    r_cats = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/categories", auth=auth, params={"per_page": 100}, timeout=15)
    cat_map = {c["id"]: c["name"] for c in r_cats.json()}
    
    # Fetch posts
    r_posts = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params={"per_page": 100}, timeout=15)
    posts = r_posts.json()
    
    print(f"{'Post ID':<10} {'Categories':<30} {'Title'}")
    print("-" * 80)
    for p in posts:
        cat_names = [cat_map.get(cid, str(cid)) for cid in p["categories"]]
        print(f"{p['id']:<10} {str(cat_names):<30} {p['title']['rendered']}")

if __name__ == "__main__":
    check_all_cats()
