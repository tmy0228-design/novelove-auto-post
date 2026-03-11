import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")
auth = (WP_USER, WP_APP_PASSWORD)

def inspect_posts():
    params = {"status": "publish", "per_page": 5}
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    posts = r.json()
    for p in posts:
        print(f"\n--- ID: {p['id']} | Title: {p['title']['rendered']} ---")
        content = p["content"]["rendered"]
        # アフィリエイトリンク周辺のHTMLを抽出
        links = [line for line in content.split("\n") if "affiliate" in line or "覗いてみて" in line or "チェック！" in line]
        for l in links:
            print(f"LINK HTML: {l}")

if __name__ == "__main__":
    inspect_posts()
