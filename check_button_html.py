import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")

def check_button_html():
    auth = (WP_USER, WP_APP_PASSWORD)
    params = {"per_page": 1}
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    if r.status_code == 200:
        posts = r.json()
        if posts:
            p = posts[0]
            content = p['content']['rendered']
            # ボタン周りのHTMLをバチッと言い当てる
            idx = content.find('custom-button-container')
            if idx != -1:
                print(content[idx:idx+400])
check_button_html()
