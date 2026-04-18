import sqlite3
import re

conn = sqlite3.connect('novelove.db')
c = conn.cursor()

query = """
SELECT title, LENGTH(description), description, published_at, site, product_url 
FROM novelove_posts 
WHERE status='published' 
AND LENGTH(description) BETWEEN 90 AND 140 
ORDER BY published_at DESC LIMIT 20
"""
c.execute(query)
rows = c.fetchall()

print(f"Count: {len(rows)}\\n")
for row in rows:
    title = row[0][:20].replace('\\n', '')
    desc = row[2]
    desc_len = row[1]
    site = row[4]
    pub_date = row[3][:10] if row[3] else "Unknown"
    
    is_omitted = "..." in desc or "…" in desc
    mark = "[OMITTED]" if is_omitted else "[OK]"
    print(f"- {mark} {pub_date} [{site}] | {desc_len} chars | {title}...")

conn.close()
