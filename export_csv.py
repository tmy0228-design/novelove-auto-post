import sqlite3
import csv
import os

db_path = r'C:\Users\PC\.gemini\antigravity\playground\novelove-github\novelove.db'
desktop_path = r'C:\Users\PC\Desktop\novelove_truncated_articles.csv'

conn = sqlite3.connect(db_path)
c = conn.cursor()

query = """
SELECT product_id, title, site, published_at, LENGTH(description), description, wp_post_url 
FROM novelove_posts 
WHERE status='published' 
AND LENGTH(description) < 150 
AND (description LIKE '%…%' OR description LIKE '%...%') 
ORDER BY published_at DESC
"""

c.execute(query)
rows = c.fetchall()

with open(desktop_path, 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['商品ID', 'タイトル', 'サイト', '投稿日時', '文字数', 'あらすじプレビュー', 'WordPress記事URL'])
    for row in rows:
        # 改行文字をスペースに置き換えてCSVを見やすくする
        clean_desc = row[5].replace('\n', ' ').replace('\r', '') if row[5] else ""
        writer.writerow([row[0], row[1], row[2], row[3], row[4], clean_desc, row[6]])

conn.close()

print(f"CSV exported to {desktop_path} successfully: {len(rows)} rows.")
