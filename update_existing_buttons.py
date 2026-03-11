import requests
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")
auth = (WP_USER, WP_APP_PASSWORD)

# 新デザイン（統一デザイン）
AFFILIATE_BUTTON_STYLE = (
    "display:block;width:300px;margin:0 auto;padding:22px 0;"
    "background:linear-gradient(135deg,#ff4785 0%,#ff5f9e 100%);"
    "color:#fff !important;text-decoration:none !important;"
    "font-weight:bold;font-size:1.25em;border-radius:50px;"
    "box-shadow:0 4px 15px rgba(255,71,133,0.4);text-shadow:0 1px 2px rgba(0,0,0,0.2);"
    "text-align:center;line-height:1;border:none !important;outline:none !important;"
)

def get_affiliate_button_html(url, label="作品の詳細を見る"):
    return (
        f'<div class="novelove-button-container" style="margin:35px 0;text-align:center;">'
        f'<a href="{url}" target="_blank" rel="noopener" style="{AFFILIATE_BUTTON_STYLE}">'
        f'{label}</a></div>'
    )

def update_posts():
    print("WordPressの記事を取得しています...")
    # 最大100件取得
    params = {"status": "publish", "per_page": 100}
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    if r.status_code != 200:
        print(f"Error fetching posts: {r.status_code}")
        return
        
    posts = r.json()
    print(f"{len(posts)} 件の記事をスキャン開始します。\n")
    
    target_labels = ["詳細をチェック", "覗いてみて", "作品の詳細を見る"]

    for p in posts:
        post_id = p["id"]
        title = p["title"]["rendered"]
        soup = BeautifulSoup(p["content"]["raw"] if "raw" in p["content"] else p["content"]["rendered"], "html.parser")
        
        modified = False
        
        # すべてのアフィリエイトリンクを検索
        links = soup.find_all("a", href=True)
        for a in links:
            txt = a.get_text()
            if any(lab in txt for lab in target_labels):
                url = a["href"]
                # リンクテキストに応じてボタンラベルを選択
                btn_label = "作品の詳細はこちら" if "チェック" in txt else "作品の詳細を見る"
                
                # 親のコンテナ（p または div）を特定
                container = a.find_parent(["p", "div"])
                if container:
                    # 既に修正済み（novelove-button-container内）ならスキップ
                    parent_div = container.find_parent("div", class_="novelove-button-container")
                    if parent_div or "novelove-button-container" in container.get("class", []):
                        continue
                        
                    # 新しいボタンHTMLをBeautifulSoupオブジェクト化
                    new_btn_html = get_affiliate_button_html(url, btn_label)
                    new_soup = BeautifulSoup(new_btn_html, "html.parser")
                    
                    # コンテナごと差し替え
                    container.replace_with(new_soup)
                    modified = True
        
        if modified:
            print(f"ID: {post_id} | Title: {title} -> ボタン置換完了。")
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
    update_posts()
