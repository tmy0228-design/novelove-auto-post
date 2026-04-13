import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)

remote_script = '''
import sqlite3
import os

dbs = {
    "FANZA/らぶカル": "/home/kusanagi/scripts/novelove.db",
    "DLsite": "/home/kusanagi/scripts/novelove_dlsite.db",
    "DigiKet": "/home/kusanagi/scripts/novelove_digiket.db"
}

print("=== 最新の審査結果と在庫分布 ===")
for name, db_path in dbs.items():
    if not os.path.exists(db_path):
        continue
    
    conn = sqlite3.connect(db_path)
    pending = conn.execute("SELECT COUNT(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
    print(f"\\n■ {name} (投稿待ち在庫: {pending}件)")
    
    print("  [在庫(pending)のスコア内訳]")
    rows = conn.execute("SELECT desc_score, COUNT(*) FROM novelove_posts WHERE status='pending' GROUP BY desc_score ORDER BY desc_score DESC").fetchall()
    for r in rows:
        print(f"    - Score {r[0]}: {r[1]}件")
        
    print("  [直近7日間の全審査(除外含む)分布]")
    rows = conn.execute("SELECT desc_score, COUNT(*) FROM novelove_posts WHERE inserted_at >= datetime('now', '-7 days', 'localtime') GROUP BY desc_score ORDER BY desc_score DESC").fetchall()
    total = sum([r[1] for r in rows])
    if total > 0:
        for r in rows:
            print(f"    - Score {r[0]}: {r[1]}件 ({int((r[1]/total)*100)}%)")
    else:
        print("    - データなし")
    
    conn.close()
'''

stdin, stdout, stderr = ssh.exec_command('python3')
stdin.write(remote_script.encode('utf-8'))
stdin.close()

print(stdout.read().decode('utf-8'))
err = stderr.read().decode('utf-8')
if err: print("Error:", err)
ssh.close()
