import requests
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")
auth = (WP_USER, WP_APP_PASSWORD)

def inspect_posts():
    params = {"status": "publish", "per_page": 20}
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    posts = r.json()
    for p in posts:
        print(f"\n--- ID: {p['id']} | Title: {p['title']['rendered']} ---")
        soup = BeautifulSoup(p["content"]["rendered"], "html.parser")
        
        # すべてのアフィリエイトリンク（通常、同人、ランキング問わず）を探す
        links = soup.find_all("a", href=True)
        for a in links:
            href = a["href"]
            if "dlsite.com" in href or "dmm.co.jp" in href or "dmm.com" in href:
                text = a.text.strip()
                parent = a.parent
                print(f"URL: {href}")
                print(f"TEXT: {text}")
                print(f"PARENT HTML: {parent}")
                # さらに外側のコンテナがあるか確認
                if parent.parent and parent.parent.name == "div":
                    print(f"WRAPPER HTML: {parent.parent}")

if __name__ == "__main__":
    inspect_posts()
