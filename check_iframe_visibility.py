import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")
auth = (WP_USER, WP_APP_PASSWORD)

def check_fanza_post():
    # 複数件取得してFANZA系を探す
    params = {"status": "publish", "per_page": 20}
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    posts = r.json()
    
    found = False
    for p in posts:
        content = p["content"]["rendered"]
        if "fanza.co.jp" in content or "dmm.co.jp" in content:
            print(f"ID: {p['id']} | Title: {p['title']['rendered']}\n")
            # クレジット周辺を抽出
            if "iframe" in content:
                start = content.find("<iframe")
                end = content.find("</iframe>") + 9
                print("--- IFRAME DETECTED ---")
                print(content[start:end])
                print("-----------------------")
            else:
                print("--- NO IFRAME FOUND ---")
                # 文末の方を確認
                print(content[-500:])
            found = True
            break
            
    if not found:
        print("FANZA/DMM posts not found in the recent 20 posts.")

if __name__ == "__main__":
    check_fanza_post()
