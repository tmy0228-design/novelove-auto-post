import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")

def check_post_v6():
    auth = (WP_USER, WP_APP_PASSWORD)
    # 最後に投稿された記事を取得
    params = {"per_page": 1, "_embed": 1}
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    if r.status_code == 200:
        posts = r.json()
        if posts:
            p = posts[0]
            print(f"Title: {p['title']['rendered']}")
            print(f"Link: {p['link']}")
            
            # カテゴリの確認
            cats = p['categories']
            print(f"Category IDs: {cats}")
            # 30 (ランキング) が含まれているか
            if 30 in cats:
                print("✅ Ranking Category (30) found!")
            else:
                print("❌ Ranking Category (30) NOT found.")
            
            # コンテンツの確認（ボタン部分）
            content = p['content']['rendered']
            idx = content.find('custom-button-container')
            if idx != -1:
                button_html = content[idx:idx+800]
                print("\n--- Button HTML Snippet ---")
                print(button_html)
                if "<br />" in button_html and "作品の詳細を見る" in button_html:
                    print("\n⚠️ Found <br /> tags inside/near button! Investigation needed.")
                else:
                    print("\n✅ No <br /> tags found inside button markup.")
            else:
                print("\n❌ Button container not found in content.")
        else:
            print("No posts found.")
    else:
        print(f"Error: {r.status_code}")

check_post_v6()
