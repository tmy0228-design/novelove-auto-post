import requests
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")
auth = (WP_USER, WP_APP_PASSWORD)

def get_dmm_credit_html():
    return (
        f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;">\n'
        f'<iframe src="https://affiliate.dmm.com/api/credit.html" width="100%" height="30" frameborder="0" scrolling="no"></iframe>\n'
        f'</div>\n'
    )

def update_credits():
    print("WordPressの記事を取得しています...")
    # 最大100件取得
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
        
        # サイト判定（FANZAまたはDMMが含まれるか）
        # タグやカテゴリからも判定できるが、一旦本文内のリンクで判定
        is_dmm = "dmm.co.jp" in content or "dmm.com" in content or "fanza.co.jp" in content
        if not is_dmm:
            continue
            
        soup = BeautifulSoup(content, "html.parser")
        modified = False
        
        # 既存のテキストクレジットを探す
        # <p style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee; font-size:0.8em; color:#bbb;">
        # PRESENTED BY FANZA / Novelove Affiliate Program
        # </p>
        for p_tag in soup.find_all("p"):
            txt = p_tag.get_text()
            if "PRESENTED BY" in txt and "Novelove Affiliate Program" in txt:
                # DMM系サイトが含まれている場合のみ iframe に置換
                if "FANZA" in txt or "DMM" in txt:
                    new_credit_html = get_dmm_credit_html()
                    new_soup = BeautifulSoup(new_credit_html, "html.parser")
                    p_tag.replace_with(new_soup)
                    modified = True
                    
        # もし既存のクレジットが見つからないがDMM記事である場合、文末に追加することも検討できるが
        # 今回は置換をメインとする
        
        if modified:
            print(f"ID: {post_id} | Title: {title} -> クレジット置換完了。")
            # 更新実行
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
    update_credits()
