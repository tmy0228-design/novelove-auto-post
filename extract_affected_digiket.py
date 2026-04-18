import sqlite3
import os

def identify_misclassified_digiket():
    db_path = 'novelove_digiket.db'
    if not os.path.exists(db_path):
        return "DigiKet DB not found."

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # DigiKet target=8 fix: Finding items marked as BL but looking like TL
    TL_KEYWORDS = ['TL', 'ティーンズラブ', '乙女', '少女', '女性向け', 'レディース', 'TL漫画', 'TL小説', 'ティーアイ', '乙女向け']
    
    cur.execute("""
        SELECT product_id, title, genre, site, status, description, product_url, published_at 
        FROM novelove_posts 
        WHERE genre='comic_bl' AND site LIKE 'DigiKet%'
    """)
    rows = cur.fetchall()
    
    affected = []
    for row in rows:
        pid, title, genre, site, status, desc, p_url, published_at = row
        text = f"{title} {desc}".lower()
        if any(kw.lower() in text for kw in TL_KEYWORDS):
            affected.append({
                "product_id": pid,
                "title": title,
                "status": status,
                "published_at": published_at,
                "product_url": p_url
            })
            
    conn.close()
    return affected

print("### DigiKet Misclassification Check (comic_bl -> comic_tl candidates) ###")
results = identify_misclassified_digiket()
if isinstance(results, str):
    print(results)
else:
    print(f"Total: {len(results)} items found.")
    for item in results:
        print(f"- [{item['status']}] {item['product_id']}: {item['title']}")
        print(f"  URL: {item['product_url']}")
        if item['published_at']:
            print(f"  Published: {item['published_at']}")
