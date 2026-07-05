import requests
import re
from novelove_core import WP_SITE_URL, WP_USER, WP_APP_PASSWORD

def analyze_sequence():
    auth = (WP_USER, WP_APP_PASSWORD)
    url = f"{WP_SITE_URL}/wp-json/wp/v2/posts"
    params = {
        "per_page": 50,  # 50件チェックすれば傾向は十分掴める
        "status": "publish"
    }
    
    try:
        r = requests.get(url, auth=auth, params=params, timeout=20)
        posts = r.json()
    except Exception as e:
        print(f"Error fetching posts: {e}")
        return

    out_lines = []
    out_lines.append("Analyzing regular posts sequence...")
    
    for post in posts:
        title = post.get("title", {}).get("rendered", "")
        content = post.get("content", {}).get("rendered", "")
        slug = post.get("slug", "")
        
        if "curation" in slug or "ranking" in slug or "選" in title or "ランキング" in title:
            continue
            
        out_lines.append(f"\n--- Post: {title} (ID: {post.get('id')}) ---")
        
        # HTMLタグの登場順を追跡
        sequence = []
        
        # 専売/セールバナー
        if "novelove-exclusive-banner" in content or "novelove-sale-banner" in content or "novelove-combined-banner" in content or "novelove-rank-banner" in content:
            sequence.append("BANNER")
            
        # ジャンルバッジ (<span ...>🎨...</span> または 📖... など)
        if "display:inline-block;" in content and ("🎨" in content or "📖" in content or "🎧" in content):
            sequence.append("GENRE_BADGE")
            
        # 最初の吹き出し
        bubble_pos = content.find("speech-bubble-left")
        if bubble_pos != -1:
            sequence.append("SPEECH_BUBBLE")
            
        # 最初の H2
        h2_match = re.search(r'<h2[^>]*>', content)
        if h2_match:
            sequence.append("H2")
            
        out_lines.append(f"Detected Sequence: {' -> '.join(sequence)}")
        
        # 吹き出しからH2までの間に何があるかを簡易抽出
        if bubble_pos != -1 and h2_match:
            h2_pos = h2_match.start()
            between_text = content[bubble_pos:h2_pos]
            # タグだけを抽出して表示
            tags = re.findall(r'<[^>]+>', between_text)
            out_lines.append(f"Tags between bubble and H2: {tags[:10]}")
            
            # 吹き出しの閉じタグ </div></div> の直後から最初の <h2> までの生HTMLを少し見せる
            # 吹き出しの構造: <div class="speech-bubble-left">...</div></div>
            # 閉じタグを探す
            close_match = re.search(r'</div>\s*</div>', content[bubble_pos:])
            if close_match:
                end_bubble_pos = bubble_pos + close_match.end()
                between_raw = content[end_bubble_pos:h2_pos].strip()
                out_lines.append(f"Raw HTML between end-of-bubble and H2: {repr(between_raw)}")
            else:
                out_lines.append("Could not find end of speech bubble")

    with open("scratch_sequence_results.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    print("Analysis complete. Results written to scratch_sequence_results.txt")

if __name__ == "__main__":
    analyze_sequence()
