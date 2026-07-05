import requests
import re
from novelove_core import WP_SITE_URL, WP_USER, WP_APP_PASSWORD

def check_posts():
    auth = (WP_USER, WP_APP_PASSWORD)
    url = f"{WP_SITE_URL}/wp-json/wp/v2/posts"
    params = {
        "per_page": 100,
        "status": "publish"
    }
    
    try:
        r = requests.get(url, auth=auth, params=params, timeout=20)
        posts = r.json()
    except Exception as e:
        print(f"Error fetching posts: {e}")
        return

    out_lines = []
    out_lines.append(f"Fetched {len(posts)} posts. Analyzing structure...")
    
    total_checked = 0
    non_conforming = 0
    
    for post in posts:
        title = post.get("title", {}).get("rendered", "")
        content = post.get("content", {}).get("rendered", "")
        categories = post.get("categories", [])
        
        # カテゴリを取得して、ランキングやまとめを除外
        slug = post.get("slug", "")
        if "curation" in slug or "ranking" in slug or "選" in title or "ランキング" in title:
            continue
            
        total_checked += 1
        
        # 1. 最初の吹き出しチェック
        has_bubble_at_start = "speech-bubble" in content[:200]
        # 2. 最初の h2 チェック
        h2_matches = list(re.finditer(r'<h2[^>]*>', content))
        has_h2 = len(h2_matches) > 0
        
        conforms = has_bubble_at_start and has_h2
        
        if not conforms:
            non_conforming += 1
            out_lines.append(f"Non-conforming post: {title} (ID: {post.get('id')})")
            out_lines.append(f"  - Bubble at start: {has_bubble_at_start}")
            out_lines.append(f"  - Has H2: {has_h2}")
            out_lines.append(f"  - HTML sample: {content[:300]}...")
            
    out_lines.append(f"\nAnalysis complete.")
    out_lines.append(f"Total checked (regular posts): {total_checked}")
    out_lines.append(f"Non-conforming posts: {non_conforming}")

    with open("scratch_check_results.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    print("Check complete. Results written to scratch_check_results.txt")

if __name__ == "__main__":
    check_posts()
