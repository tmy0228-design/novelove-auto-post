import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
auth = (WP_USER, WP_APP_PASSWORD)

def list_categories():
    try:
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/categories", auth=auth, params={"per_page": 100}, timeout=15)
        categories = r.json()
        print(f"{'ID':<10} {'Name':<30} {'Slug':<30} {'Count':<10}")
        print("-" * 80)
        for cat in categories:
            print(f"{cat['id']:<10} {cat['name']:<30} {cat['slug']:<30} {cat['count']:<10}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    list_categories()
