import requests
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")
auth = (WP_USER, WP_APP_PASSWORD)

def get_fanza_img_credit():
    return (
        f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;">\n'
        f'<a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/r18_135_17.gif" width="135" height="17" alt="WEB SERVICE BY FANZA" style="border:none;"></a>\n'
        f'</div>\n'
    )

def get_dmm_img_credit():
    return (
        f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;">\n'
        f'<a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/com_135_17.gif" width="135" height="17" alt="WEB SERVICE BY DMM.com" style="border:none;"></a>\n'
        f'</div>\n'
    )

def update_credits_to_img():
    print("WordPressの記事を取得しています...")
    params = {"status": "publish", "per_page": 100}
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    if r.status_code != 200:
        print(f"Error fetching posts: {r.status_code}")
        return
        
    posts = r.json()
    print(f"{len(posts)} 件の記事をスキャン開始します。\n")

    for p in posts:
        post_id = p["id"]
        title = p["title"]["rendered"]
        content = p["content"]["rendered"]
        
        soup = BeautifulSoup(content, "html.parser")
        modified = False
        
        # 1. 失敗した iframe を探して置換
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if "affiliate.dmm.com/api/credit.html" in src:
                # 親の .novelove-credit ごと置換するか、iframe だけ置換するか
                # 親があるはずなので親を探す
                parent = iframe.find_parent("div", class_="novelove-credit")
                
                # サイト判定
                is_fanza = "fanza.co.jp" in content or "FANZA" in title
                new_html = get_fanza_img_credit() if is_fanza else get_dmm_img_credit()
                new_soup = BeautifulSoup(new_html, "html.parser")
                
                if parent:
                    parent.replace_with(new_soup)
                else:
                    iframe.replace_with(new_soup)
                modified = True

        # 2. まだ残っているテキストクレジットを探して置換 (DLsite以外)
        if not modified:
            for p_tag in soup.find_all("p"):
                txt = p_tag.get_text()
                if "PRESENTED BY" in txt and "Novelove Affiliate Program" in txt:
                    if "FANZA" in txt or "DMM" in txt:
                        is_fanza = "FANZA" in txt
                        new_html = get_fanza_img_credit() if is_fanza else get_dmm_img_credit()
                        new_soup = BeautifulSoup(new_html, "html.parser")
                        p_tag.replace_with(new_soup)
                        modified = True
        
        if modified:
            print(f"ID: {post_id} | Title: {title} -> 公式画像クレジットへ更新完了。")
            up_r = requests.post(
                f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
                auth=auth,
                json={"content": str(soup)}
            )
            if up_r.status_code == 200:
                print("  -> ✅ WordPressを更新しました。")
            else:
                print(f"  -> ❌ 更新失敗: {up_r.status_code}")

if __name__ == "__main__":
    update_credits_to_img()
