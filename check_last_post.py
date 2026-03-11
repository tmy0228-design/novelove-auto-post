import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")

def get_last_post():
    auth = (WP_USER, WP_APP_PASSWORD)
    params = {"per_page": 1}
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    if r.status_code == 200:
        posts = r.json()
        if posts:
            p = posts[0]
            print(f"Title: {p['title']['rendered']}")
            print(f"Slug: {p['slug']}")
            print(f"Link: {p['link']}")
            
            # タグの名前を取得
            tag_ids = p.get('tags', [])
            tag_names = []
            for tid in tag_ids:
                tr = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/tags/{tid}", auth=auth)
                if tr.status_code == 200:
                    tag_names.append(tr.json().get('name'))
            print(f"Tags: {tag_names}")
            
        else:
            print("No posts found.")
    else:
        print(f"Error: {r.status_code}")

get_last_post()
