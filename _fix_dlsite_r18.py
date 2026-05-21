"""
v18.4.0: DLsite既存pending在庫のR-18フラグを、HTMLバッジ(icon_ADL)に基づいて正確に修正するスクリプト。
旧ガバガバ判定で保存された site='DLsite:r18=0' or 'DLsite:r18=1' を正しい値に更新する。
"""
import sqlite3
import requests
from bs4 import BeautifulSoup
import time

DB_PATH = '/home/kusanagi/scripts/novelove_unified.db'

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# DLsiteのpending在庫のみ対象
rows = c.execute(
    "SELECT product_id, title, product_url, site FROM novelove_posts "
    "WHERE source_db='dlsite' AND status='pending' AND site LIKE 'DLsite:%'"
).fetchall()

print(f"対象: DLsite pending {len(rows)}件")
updated = 0
errors = 0

for row in rows:
    pid = row['product_id']
    url = row['product_url']
    old_site = row['site']
    
    if not url:
        print(f"  [SKIP] {pid}: product_url が空")
        continue
    
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code != 200:
            print(f"  [ERR] {pid}: HTTP {r.status_code}")
            errors += 1
            time.sleep(1)
            continue
        
        soup = BeautifulSoup(r.text, "html.parser")
        is_r18 = 1 if soup.select_one(".icon_ADL") else 0
        new_site = f"DLsite:r18={is_r18}"
        
        if old_site != new_site:
            c.execute("UPDATE novelove_posts SET site=? WHERE product_id=?", (new_site, pid))
            print(f"  [FIX] {pid}: {old_site} -> {new_site} ({row['title'][:25]}...)")
            updated += 1
        
        time.sleep(0.5)
    except Exception as e:
        print(f"  [ERR] {pid}: {e}")
        errors += 1
        time.sleep(1)

conn.commit()
conn.close()
print(f"\n完了: {updated}件修正 / {errors}件エラー / {len(rows)}件中")
