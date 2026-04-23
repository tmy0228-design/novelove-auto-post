"""
パターンD記事（blockquote使用）の調査スクリプト（dry-run）
WP REST APIで全公開記事を検索し、blockquoteが含まれる記事を一覧表示するだけ。
修正は行わない。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from novelove_core import WP_SITE_URL, WP_USER, WP_APP_PASSWORD

AUTH = (WP_USER, WP_APP_PASSWORD)
BLOCKQUOTE_MARKER = '<blockquote style="border-left:4px solid #d81b60;'

def find_pattern_d_articles():
    found = []
    page = 1
    per_page = 100
    print(f"WP REST APIで全記事を検索中... ({WP_SITE_URL})")
    while True:
        resp = requests.get(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts",
            auth=AUTH,
            params={"status": "publish", "per_page": per_page, "page": page, "_fields": "id,slug,title,content"},
            timeout=30
        )
        if resp.status_code == 400:
            break  # ページ終端
        if resp.status_code != 200:
            print(f"エラー: status={resp.status_code}")
            break
        posts = resp.json()
        if not posts:
            break
        for post in posts:
            content = post.get("content", {}).get("rendered", "")
            if BLOCKQUOTE_MARKER in content:
                found.append({
                    "id": post["id"],
                    "slug": post["slug"],
                    "title": post["title"]["rendered"][:50]
                })
        total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
        print(f"  page {page}/{total_pages} 完了 (このページ{len(posts)}件, 発見累計{len(found)}件)")
        if page >= total_pages:
            break
        page += 1

    print(f"\n=== 結果: Dパターン記事 {len(found)}件 ===")
    for a in found:
        print(f"  ID={a['id']}  slug={a['slug']}  タイトル={a['title']}")
    return found

if __name__ == "__main__":
    find_pattern_d_articles()
