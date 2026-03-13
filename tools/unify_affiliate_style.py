#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests
import json
import time
import re
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# --- 環境変数の読み込み ---
env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(env_path): load_dotenv(env_path)
else: load_dotenv()

WP_SITE_URL     = "https://novelove.jp"
WP_USER         = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")

# 新しいボタンのスタイル（パステル薄ピンク）
NEW_BUTTON_STYLE = (
    "display:block;width:300px;margin:0 auto;padding:18px 0;"
    "background:#ffebf2;"
    "color:#d81b60 !important;text-decoration:none !important;"
    "font-weight:bold;font-size:1.1em;border-radius:50px;"
    "box-shadow:0 4px 10px rgba(216,27,96,0.15);border:2px solid #ffcfdf !important;"
    "text-align:center;line-height:1;outline:none !important;"
)

def get_button_html(url, label="作品の詳細を見る"):
    return (
        f'<div class="novelove-button-container" style="margin:35px 0;text-align:center;">'
        f'<a href="{url}" target="_blank" rel="nofollow noopener" style="{NEW_BUTTON_STYLE}">{label}</a>'
        f'</div>'
    )

def unify_post_style(post_id, title, content, post_url):
    """
    1つの記事の内容を解析し、最新ルールに書き換える
    """
    if not content:
        # contentが空の場合は処理できないが、一応警告
        print(f"    [Warning] Content is empty for ID: {post_id}")
        return None

    soup = BeautifulSoup(content, 'html.parser')
    changed = False

    # 1. すべてのボタン (novelove-button-container) を完全に削除
    # クラス名ベースで削除
    buttons = soup.find_all('div', class_='novelove-button-container')
    if buttons:
        for btn in buttons:
            btn.decompose()
        changed = True
        print(f"    [Remove] Deleted {len(buttons)} button containers")

    # 2. アフィリエイトURLの取得（末尾ボタン用）
    # ボタン削除後も残っているリンクからURLを探す
    aff_url = ""
    for a in soup.find_all('a'):
        href = a.get('href', '')
        if any(x in href for x in ['dmm.co.jp', 'dlsite.com', 'fanza.co.jp']):
            aff_url = href
            break
    
    # 3. 特定記事 (ID: 1070) へのテキストリンク個別追加
    # https://novelove.jp/b355iakta90726/
    if post_id == 1070:
        # 既存のリンクがあるか念のためチェック
        has_text_link = False
        for a in soup.find_all('a'):
            if 'の詳細をチェック！' in a.get_text():
                has_text_link = True
                break
        
        if not has_text_link:
            first_img = None
            for img in soup.find_all('img'):
                src = img.get('src', '')
                if src and "wp-content/uploads/icons/" not in src:
                    first_img = img
                    break
            if first_img and aff_url:
                text_link_html = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:5px; margin-bottom:15px;"><a href="{aff_url}" target="_blank" rel="nofollow noopener" style="text-decoration:none; color:#d81b60;">▶ 『{title}』の詳細をチェック！</a></p>'
                new_tag = BeautifulSoup(text_link_html, 'html.parser')
                # 画像の親のPタグの後、または画像そのものの後に挿入
                target = first_img.find_parent('p') or first_img
                target.insert_after(new_tag)
                changed = True
                print(f"    [Insert] Added missing text link for ID: 1070")

    # 4. 記事末尾（クレジットの前）に新しい薄ピンクボタンを配置
    if aff_url:
        new_button_html = get_button_html(aff_url)
        new_button = BeautifulSoup(new_button_html, 'html.parser')
        
        # クレジット表示（novelove-credit）を探す
        credit = soup.find('div', class_='novelove-credit')
        if credit:
            credit.insert_before(new_button)
        else:
            # クレジットがない場合は最後から2番目の位置（最後は空行などの可能性があるため）か、単純にappend
            soup.append(new_button)
        
        changed = True
        print(f"    [Insert] Added new pink button at bottom")

    if changed:
        # キャッシュ対策のダミーコメントを挿入
        timestamp_comment = f"\n<!-- updated_{int(time.time())} -->"
        return str(soup) + timestamp_comment
    return None

def main():
    auth = (WP_USER, WP_APP_PASSWORD)
    if not WP_USER or not WP_APP_PASSWORD:
        print("Error: WP_USER or WP_APP_PASSWORD not set.")
        return

    # 全記事更新（ページネーション対応）
    for page in range(1, 11):
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts?per_page=50&page={page}&context=edit", auth=auth)
        if r.status_code != 200: break
        posts = r.json()
        if not posts: break
        
        for post in posts:
            post_id = post["id"]
            title = post["title"]["rendered"]
            content = post["content"].get("raw", "")
            if not content:
                content = post["content"].get("rendered", "")
                print(f"  [Notice] Using rendered content for ID: {post_id}")
            
            post_url = post.get("link", "")
            
            # ランキング記事のフィルタリング（タイトルに「ランキング」が含まれる、またはURLに「ranking」が含まれる）
            if "ランキング" in title or "ranking" in post_url.lower():
                continue

            print(f"Processing ID: {post_id} | {title}...")
            new_content = unify_post_style(post_id, title, content, post_url)
            
            if new_content:
                ur = requests.post(
                    f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
                    auth=auth,
                    json={"content": new_content}
                )
                if ur.status_code == 200:
                    print(f"    => [SUCCESS] Updated")
                else:
                    print(f"    => [FAILED] {ur.status_code}")
            else:
                print(f"    => [SKIP] No changes")
            
            time.sleep(0.3)

if __name__ == "__main__":
    main()
