import sqlite3
import os

def check_db(db_path, db_name):
    if not os.path.exists(db_path):
        print(f"Skipping {db_name}: Not found")
        return []

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    results = []

    # 1. Check for mismatched content (AI Hallucination)
    # This checks if a TL article mentions BL in its introduction, or vice versa.
    cur.execute("SELECT product_id, title, genre, site, status, published_at, wp_post_url FROM novelove_posts WHERE status='published'")
    rows = cur.fetchall()
    
    for row in rows:
        pid, title, genre, site, status, pub_date, url = row
        # We need to fetch the content to check for hallucinations
        # But content might be large, so let's just fetch it for check
        cur.execute("SELECT content FROM novelove_posts WHERE product_id=?", (pid,))
        content = cur.fetchone()[0] or ""

        is_bl_genre = "bl" in genre.lower()
        is_tl_genre = "tl" in genre.lower()
        
        # Hallucination Check
        if is_tl_genre and ("BL漫画" in content or "ボーイズラブ" in content):
            results.append({
                "type": "Hallucination (TL -> BL)",
                "db": db_name,
                "pid": pid,
                "title": title,
                "genre": genre,
                "site": site,
                "url": url
            })
        elif is_bl_genre and ("TL漫画" in content or "ティーンズラブ" in content or "乙女向け" in content):
            results.append({
                "type": "Hallucination (BL -> TL)",
                "db": db_name,
                "pid": pid,
                "title": title,
                "genre": genre,
                "site": site,
                "url": url
            })

    # 2. Specific DigiKet target=8 misclassification (DB metadata error)
    if "digiket" in db_name.lower():
        TL_KEYWORDS = ['TL', 'ティーンズラブ', '乙女', '少女', '女性向け', 'レディース', 'TL漫画', 'TL小説']
        cur.execute("SELECT product_id, title, genre, original_tags, description, product_url FROM novelove_posts WHERE genre='comic_bl' AND site LIKE 'DigiKet%'")
        dk_rows = cur.fetchall()
        for row in dk_rows:
            pid, title, tags, desc, p_url = row
            text = f"{title} {tags} {desc}".lower()
            if any(kw.lower() in text for kw in TL_KEYWORDS):
                results.append({
                    "type": "Metadata Error (DigiKet BL -> TL)",
                    "db": db_name,
                    "pid": pid,
                    "title": title,
                    "genre": genre,
                    "site": site,
                    "url": p_url
                })
    
    conn.close()
    return results

dbs = [
    ("novelove.db", "Main/FANZA"),
    ("novelove_digiket.db", "DigiKet"),
    ("novelove_dlsite.db", "DLsite")
]

all_affected = []
for path, name in dbs:
    all_affected.extend(check_db(path, name))

print(f"Total affected articles found: {len(all_affected)}")
print("-" * 50)
for a in all_affected:
    print(f"[{a['type']}] {a['db']} | {a['pid']} | {a['title']}")
    print(f"   URL: {a['url']}")
