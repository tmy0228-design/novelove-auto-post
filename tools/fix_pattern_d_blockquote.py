"""
パターンD記事の一括修正スクリプト
スキャン時に取得したレンダリング済みHTMLをそのまま置換してPOSTする。
（個別GET不要で KUSANAGI bcache の問題を回避）
"""
import sys
import os
import re
import json
import base64
import urllib.request
import urllib.parse
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()

from novelove_core import WP_SITE_URL

WP_USER         = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
AUTH_HEADER     = "Basic " + base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()

BLOCKQUOTE_MARKER = '<blockquote style="border-left:4px solid #d81b60;'
BLOCKQUOTE_PATTERN = re.compile(
    r'<blockquote\s+style="border-left:4px solid #d81b60;[^"]*">'
)
DIV_REPLACEMENT = '<div class="novelove-quote" style="border-left:4px solid #d81b60; padding:12px 20px; margin:20px 0; background:#fff5f9; color:#555;">'


def wp_get_list(page):
    """認証なしで公開記事リストを取得（bcacheから返る公開済みコンテンツを使用）"""
    url = (f"{WP_SITE_URL}/wp-json/wp/v2/posts?"
           + urllib.parse.urlencode({
               "status": "publish", "per_page": 100, "page": page,
               "_fields": "id,title,content"
           }))
    req = urllib.request.Request(url, headers={"User-Agent": "Novelove-Backfill/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")), resp.headers


def wp_post_update(post_id, new_content):
    """認証付きでコンテンツを更新"""
    url = f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}"
    payload = json.dumps({"content": new_content}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Authorization": AUTH_HEADER,
        "Content-Type": "application/json",
        "User-Agent": "Novelove-Backfill/1.0"
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def main():
    print(f"USER={WP_USER}, PASS={'OK' if WP_APP_PASSWORD else 'EMPTY'}")
    if not WP_APP_PASSWORD:
        print("エラー: WP_APP_PASSWORDが設定されていません。終了します。")
        return

    ok = 0
    skipped = 0
    errors = 0
    page = 1

    print("\nスキャン＆修正を同時実行中...")
    while True:
        try:
            posts, headers = wp_get_list(page)
        except Exception as e:
            print(f"  リスト取得失敗 page={page}: {e}")
            break
        if not posts:
            break

        total_pages = int(headers.get("X-WP-TotalPages", 1))
        print(f"  page {page}/{total_pages} ({len(posts)}件)...")

        for post in posts:
            post_id = post["id"]
            title   = post["title"]["rendered"][:40]
            content = post.get("content", {}).get("rendered", "")

            if BLOCKQUOTE_MARKER not in content:
                continue  # スキップ（Dパターンでない）

            # 置換実行
            new_content = BLOCKQUOTE_PATTERN.sub(DIV_REPLACEMENT, content)
            new_content = new_content.replace("</blockquote>", "</div>")

            if new_content == content:
                print(f"  [SKIP] ID={post_id} 変更なし: {title}")
                skipped += 1
                continue

            # 更新
            try:
                status = wp_post_update(post_id, new_content)
                if status in (200, 201):
                    print(f"  [OK]   ID={post_id} 修正完了: {title}")
                    ok += 1
                else:
                    print(f"  [NG]   ID={post_id} status={status}: {title}")
                    errors += 1
            except Exception as e:
                print(f"  [NG]   ID={post_id} エラー: {e}: {title}")
                errors += 1
            time.sleep(0.3)

        if page >= total_pages:
            break
        page += 1

    print(f"\n=== 完了: 修正={ok}件 / スキップ={skipped}件 / エラー={errors}件 ===")


if __name__ == "__main__":
    main()
