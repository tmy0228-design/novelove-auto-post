import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, look_for_keys=False, allow_agent=False, timeout=15)

script = """
import sqlite3

conn = sqlite3.connect('/home/kusanagi/scripts/novelove_unified.db')
c = conn.cursor()

c.execute('''
SELECT source_db, genre, 
       SUM(CASE WHEN site LIKE '%r18=1%' THEN 1 ELSE 0 END) as r18_count,
       SUM(CASE WHEN site LIKE '%r18=0%' THEN 1 ELSE 0 END) as non_r18_count,
       COUNT(*) as total
FROM novelove_posts
GROUP BY source_db, genre
''')

print("=== 全体統計 (R-18フラグの分布) ===")
print(f'%-10s | %-12s | %-5s | %-5s | %-5s' % ('source_db', 'genre', 'R-18', 'non', 'total'))
print('-'*45)
for row in c.fetchall():
    print(f'%-10s | %-12s | %-5d | %-5d | %-5d' % row)
    
print("\\n=== サンプル: DigiKet (R-18=0判定のもの) ===")
c.execute("SELECT title, site FROM novelove_posts WHERE source_db='digiket' AND site LIKE '%r18=0%' LIMIT 3")
for row in c.fetchall():
    print(f"- {row[0][:30]}... ({row[1]})")

print("\\n=== サンプル: DigiKet (R-18=1判定のもの) ===")
c.execute("SELECT title, site FROM novelove_posts WHERE source_db='digiket' AND site LIKE '%r18=1%' LIMIT 3")
for row in c.fetchall():
    print(f"- {row[0][:30]}... ({row[1]})")
"""

sftp = client.open_sftp()
with sftp.file('/tmp/check_db.py', 'w') as f:
    f.write(script.encode('utf-8'))
sftp.close()

stdin, stdout, stderr = client.exec_command("python3 /tmp/check_db.py")
print(stdout.read().decode('utf-8'))
client.close()
