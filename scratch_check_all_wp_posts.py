import requests
import re
import sys
import time
from novelove_core import WP_SITE_URL, WP_USER, WP_APP_PASSWORD

def check_all_posts():
    auth = (WP_USER, WP_APP_PASSWORD)
    url = f"{WP_SITE_URL}/wp-json/wp/v2/posts"
    
    # 最初のページを叩いて総記事数を取得
    params = {
        "per_page": 100,
        "page": 1,
        "status": "publish"
    }
    
    try:
        r = requests.head(url, auth=auth, params=params, timeout=20)
        total_posts = int(r.headers.get("X-WP-Total", 0))
        total_pages = int(r.headers.get("X-WP-TotalPages", 0))
    except Exception as e:
        print(f"Error getting headers: {e}")
        return

    out_lines = []
    out_lines.append(f"WordPress Total Posts: {total_posts}")
    out_lines.append(f"Total Pages to fetch (100 per page): {total_pages}")
    
    total_checked = 0
    non_conforming = []
    
    for page in range(1, total_pages + 1):
        print(f"Fetching page {page}/{total_pages}...")
        params["page"] = page
        
        # リトライ機構付きでリクエスト
        for attempt in range(3):
            try:
                res = requests.get(url, auth=auth, params=params, timeout=30)
                if res.status_code == 200:
                    posts = res.json()
                    break
                else:
                    print(f"  Attempt {attempt+1} failed: status={res.status_code}")
                    time.sleep(2)
            except Exception as e:
                print(f"  Attempt {attempt+1} failed: {e}")
                time.sleep(2)
        else:
            print(f"Failed to fetch page {page} after 3 attempts. Skipping.")
            continue
            
        for post in posts:
            title = post.get("title", {}).get("rendered", "")
            content = post.get("content", {}).get("rendered", "")
            slug = post.get("slug", "")
            post_id = post.get("id")
            
            # ランキングやまとめ記事を除外
            if "curation" in slug or "ranking" in slug or "選" in title or "ランキング" in title:
                continue
                
            total_checked += 1
            
            # 構造チェック: 最初の <h2> を探す
            h2_match = re.search(r'<h2[^>]*>', content)
            has_h2 = h2_match is not None
            
            # 吹き出しがあるかチェック
            has_bubble = "speech-bubble" in content
            
            if not has_h2 or not has_bubble:
                non_conforming.append({
                    "id": post_id,
                    "title": title,
                    "slug": slug,
                    "has_h2": has_h2,
                    "has_bubble": has_bubble,
                    "snippet": content[:300] if len(content) > 300 else content
                })
                
        time.sleep(0.5)  # APIサーバー負荷軽減
        
    out_lines.append(f"Analysis Complete.")
    out_lines.append(f"Total Regular Posts Checked: {total_checked}")
    out_lines.append(f"Total Non-conforming Posts: {len(non_conforming)}")
    
    if non_conforming:
        out_lines.append("\n=== Non-conforming Posts List ===")
        for idx, item in enumerate(non_conforming, 1):
            out_lines.append(f"{idx}. ID: {item['id']} | Title: {item['title']} | Slug: {item['slug']}")
            out_lines.append(f"   - Has H2: {item['has_h2']}")
            out_lines.append(f"   - Has Bubble: {item['has_bubble']}")
            out_lines.append(f"   - Snippet: {repr(item['snippet'])}")
    else:
        out_lines.append("\nAll regular posts conform to the target HTML structure (have speech bubble and H2)!")

    with open("scratch_all_check_results.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    print(f"Verification complete. Total checked: {total_checked}. Non-conforming: {len(non_conforming)}.")
    print("Results written to scratch_all_check_results.txt")

if __name__ == "__main__":
    check_all_posts()
