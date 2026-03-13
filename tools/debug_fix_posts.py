#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests
import json
import time
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

def fix_post_content(post_id, title, html_content):
    """
    全ボタン削除（色不問） -> 末尾に1つ配置
    1070番のみ画像下リンク追加
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    changed = False

    # 1. すべてのボタンを削除（クラス名 + aタグの特定ワードで徹底的に）
    # novelove-button-container クラスを持つものをすべて削除
    btns = soup.find_all('div', class_='novelove-button-container')
    if btns:
        for b in btns: b.decompose()
        changed = True
    
    # クラス名がない場合でも、「作品の詳細」というテキストを持つaタグを含むdivを削除（念のため）
    for a in soup.find_all('a'):
        txt = a.get_text()
        if '作品の詳細' in txt and a.find_parent('div'):
            parent = a.find_parent('div')
            parent.decompose()
            changed = True

    # 2. アフィリエイトURLの取得
    aff_url = ""
    for a in soup.find_all('a'):
        href = a.get('href', '')
        if any(x in href for x in ['dmm.co.jp', 'dlsite.com', 'fanza.co.jp']):
            aff_url = href
            break
    
    # 3. ID: 1070 への個別対応（画像下リンク追加）
    if post_id == 1070:
        has_text_link = any('の詳細をチェック！' in a.get_text() for a in soup.find_all('a'))
        if not has_text_link and aff_url:
            first_img = None
            for img in soup.find_all('img'):
                if img.get('src') and 'wp-content/uploads/icons/' not in img.get('src'):
                    first_img = img
                    break
            if first_img:
                text_link = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:5px; margin-bottom:15px;"><a href="{aff_url}" target="_blank" rel="nofollow noopener" style="text-decoration:none; color:#d81b60;">▶ 『{title}』の詳細をチェック！</a></p>'
                target = first_img.find_parent('p') or first_img
                target.insert_after(BeautifulSoup(text_link, 'html.parser'))
                changed = True

    # 4. 記事末尾へのボタン配置
    if aff_url:
        btn_html = get_button_html(aff_url)
        credit = soup.find('div', class_='novelove-credit')
        if credit:
            credit.insert_before(BeautifulSoup(btn_html, 'html.parser'))
        else:
            soup.append(BeautifulSoup(btn_html, 'html.parser'))
        changed = True

    if changed:
        # タイムスタンプを入れてキャッシュを回避
        return str(soup) + f"\n<!-- build_time: {int(time.time())} -->"
    return None

def force_update_post(post_id):
    auth = (WP_USER, WP_APP_PASSWORD)
    print(f"Investigating ID: {post_id}...")
    
    # context=edit で取得
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}?context=edit", auth=auth)
    if r.status_code != 200:
        print(f"Failed to fetch {post_id}: {r.status_code}")
        return
    
    post = r.json()
    title = post["title"]["rendered"]
    # rawがあれば優先、なければrenderedを使う
    content = post.get("content", {}).get("raw", "")
    if not content:
        content = post.get("content", {}).get("rendered", "")
        print("  - Raw content empty. Using rendered.")

    new_content = fix_post_content(post_id, title, content)
    
    if new_content:
        # 更新
        # 注意: contentだけでなく、もし他のフィールド（メタ）が必要な場合もあるが、まずはcontent
        res = requests.post(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
            auth=auth,
            json={"content": new_content}
        )
        if res.status_code == 200:
            print(f"  => SUCCESS: Updated ID {post_id}")
        else:
            print(f"  => FAILED: {res.status_code} {res.text[:200]}")
    else:
        print(f"  => SKIP: No changes needed for ID {post_id}")

if __name__ == "__main__":
    # まず問題の2記事を狙い撃ち
    for pid in [213, 1070]:
        force_update_post(pid)
