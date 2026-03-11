import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")

def list_wp_tags():
    if not WP_USER or not WP_APP_PASSWORD:
        print("WP credentials missing in .env")
        return

    auth = (WP_USER, WP_APP_PASSWORD)
    tags = []
    page = 1
    while True:
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/tags", auth=auth, params={"per_page": 100, "page": page})
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        tags.extend(data)
        page += 1
        if len(data) < 100:
            break
            
    print(f"Total tags found: {len(tags)}")
    for t in tags:
        print(f"  ID: {t['id']}, Name: {t['name']}")

if __name__ == "__main__":
    list_wp_tags()
