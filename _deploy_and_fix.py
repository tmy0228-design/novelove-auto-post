import paramiko
import codecs
import sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

# Step 1: Git pull
print("=== Step 1: サーバーに最新コードを反映 ===")
stdin, stdout, stderr = client.exec_command("cd /home/kusanagi/scripts && git reset --hard && git pull origin main")
print(stdout.read().decode('utf-8', errors='replace'))

# Step 2: DLsite既存在庫のR-18フラグ一括修正スクリプトをアップロード・実行
print("=== Step 2: DLsite既存在庫のR-18フラグ修正 ===")

script = '''
import sqlite3
import requests
from bs4 import BeautifulSoup
import time

DB_PATH = '/home/kusanagi/scripts/novelove_unified.db'
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

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
        continue
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code != 200:
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
print(f"\\n完了: {updated}件修正 / {errors}件エラー / {len(rows)}件中")
'''

sftp = client.open_sftp()
with sftp.file('/tmp/fix_dlsite_r18.py', 'w') as f:
    f.write(script.encode('utf-8'))
sftp.close()

stdin, stdout, stderr = client.exec_command("cd /home/kusanagi/scripts && python3 /tmp/fix_dlsite_r18.py", timeout=300)
print(stdout.read().decode('utf-8', errors='replace'))
err = stderr.read().decode('utf-8', errors='replace')
if err:
    print("STDERR:", err)

client.close()
print("=== 全作業完了 ===")
