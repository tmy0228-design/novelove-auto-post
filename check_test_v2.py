import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")

def check_post_details():
    auth = (WP_USER, WP_APP_PASSWORD)
    slug = "dlsite-bl-ranking-2026-03-w10-2"
    params = {"slug": slug, "_embed": 1}
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    if r.status_code == 200:
        posts = r.json()
        if posts:
            p = posts[0]
            print(f"Title: {p['title']['rendered']}")
            print(f"Excerpt (Meta Desc): {p['excerpt']['rendered']}")
            print(f"Content length: {len(p['content']['rendered'])}")
            
            # 吹き出しが含まれているか
            if "speech-bubble-left" in p['content']['rendered']:
                print("Speech bubbles found!")
            else:
                print("Speech bubbles NOT found - Check prompt effect.")
                
            # タグ
            embedded = p.get("_embedded", {})
            terms = embedded.get("wp:term", [])
            tag_names = []
            if len(terms) > 1:
                tags = terms[1]
                tag_names = [t["name"] for t in tags]
            print(f"Tags: {tag_names}")
        else:
            print(f"Post with slug '{slug}' not found.")
    else:
        print(f"Error: {r.status_code}")

check_post_details()
