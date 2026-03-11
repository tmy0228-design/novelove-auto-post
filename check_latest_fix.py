import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")

def check_latest_ranking():
    auth = (WP_USER, WP_APP_PASSWORD)
    params = {"per_page": 1}
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    if r.status_code == 200:
        posts = r.json()
        if posts:
            p = posts[0]
            print(f"Title: {p['title']['rendered']}")
            print(f"Link: {p['link']}")
            content = p['content']['rendered']
            
            # ボタンのHTMLを抽出して確認
            if 'custom-button-container' in content:
                print("Button found!")
                # スタイルの一部を表示
                start = content.find('style="display: inline-flex;')
                if start != -1:
                    print(f"Button style: {content[start:start+150]}...")
                else:
                    print("Flex style NOT found in button.")
                    
            if 'speech-bubble-left' in content:
                print("Speech bubbles found!")
        else:
            print("No posts found.")
    else:
        print(f"Error: {r.status_code}")

check_latest_ranking()
