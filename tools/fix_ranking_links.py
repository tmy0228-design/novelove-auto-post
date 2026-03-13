import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
import re

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

WP_SITE_URL = os.environ.get('WP_SITE_URL', 'https://novelove.jp')
WP_USER = os.environ.get('WP_USER')
WP_APP_PASSWORD = os.environ.get('WP_APP_PASSWORD')

if not all([WP_USER, WP_APP_PASSWORD]):
    logger.error("WORDPRESS credentials not found in .env")
    exit(1)

auth = (WP_USER, WP_APP_PASSWORD)

def fix_ranking_post(post_id):
    logger.info(f"Processing post ID: {post_id}")
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}?context=edit", auth=auth)
    if r.status_code != 200:
        logger.error(f"Failed to fetch post {post_id}: {r.status_code}")
        return

    post_data = r.json()
    raw_content = post_data['content']['raw']
    soup = BeautifulSoup(raw_content, 'html.parser')
    changed = False

    # 1. すべてのボタンコンテナを削除
    buttons = soup.find_all('div', class_='novelove-button-container')
    if buttons:
        logger.info(f"  Removing {len(buttons)} button containers.")
        for btn in buttons:
            btn.decompose()
        changed = True

    # 2. すべてのランキング項目をループして画像下のテキストリンクを確認・修正
    items = soup.find_all('div', class_='ranking-item')
    for idx, item in enumerate(items):
        # 作品タイトルを取得（h3タグから）
        title_tag = item.find('h3')
        title = title_tag.get_text().strip() if title_tag else "作品"
        
        # アフィリエイトURLを取得（画像リンクから）
        img_a = item.find('a', href=re.compile(r'fanza|dmm|dlsite'))
        aff_url = img_a['href'] if img_a else ""
        
        if not aff_url:
            continue

        # 既存のテキストリンクを探す
        # 「の詳細をチェック」を含むリンクを探す
        found_link = False
        for a in item.find_all('a'):
            if 'の詳細をチェック' in a.get_text():
                # タイトルが「作品」になっている不備を修正
                if '『作品』' in a.get_text():
                    logger.info(f"  Fixing placeholder title in link for item {idx+1}")
                    a.string = f"▶ 『{title}』の詳細をチェック！"
                    changed = True
                found_link = True
                break
        
        # テキストリンクがない場合は追加（画像の下）
        if not found_link:
            logger.info(f"  Adding missing text link for item {idx+1}")
            text_link_html = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:10px; margin-bottom:15px;"><a href="{aff_url}" target="_blank" rel="nofollow" style="text-decoration:none; color:#d81b60;">▶ 『{title}』の詳細をチェック！</a></p>'
            # 画像の次の要素として挿入（通常はdiv[text-align:center]の中のa[img]の次か、そのdiv自体）
            img_div = item.find('div', style=re.compile(r'text-align:\s*center'))
            if img_div:
                new_tag = BeautifulSoup(text_link_html, 'html.parser')
                img_div.insert_after(new_tag)
                changed = True

    if changed:
        new_content = str(soup)
        update_res = requests.post(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
            auth=auth,
            json={'content': new_content}
        )
        if update_res.status_code == 200:
            logger.info(f"  Successfully updated post {post_id}")
        else:
            logger.error(f"  Failed to update post {post_id}: {update_res.status_code}")
    else:
        logger.info(f"  No changes needed for post {post_id}")

def main():
    # カテゴリ 30 (ランキング) の記事を取得
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts?categories=30&per_page=100", auth=auth)
    if r.status_code != 200:
        logger.error(f"Failed to fetch ranking posts: {r.status_code}")
        return

    posts = r.json()
    logger.info(f"Found {len(posts)} ranking posts to check.")
    for p in posts:
        fix_ranking_post(p['id'])

if __name__ == "__main__":
    main()
