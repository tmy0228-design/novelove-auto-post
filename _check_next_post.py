import paramiko
import codecs
import sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

# 1. crontab確認
print("=== crontab（auto_post関連）===")
stdin, stdout, stderr = client.exec_command("crontab -l 2>/dev/null | grep auto_post")
print(stdout.read().decode('utf-8', errors='replace'))

# 2. 最後の投稿時刻
print("=== 最後のWP投稿成功時刻 ===")
stdin, stdout, stderr = client.exec_command("grep '投稿成功' /home/kusanagi/scripts/auto_post.log 2>/dev/null | tail -3")
out = stdout.read().decode('utf-8', errors='replace')
print(out if out.strip() else "(投稿成功ログなし)")

# 3. 最後のcron実行ログ
print("=== 最後のauto_post実行ログ ===")
stdin, stdout, stderr = client.exec_command("grep 'エンジン' /home/kusanagi/scripts/auto_post.log 2>/dev/null | tail -3")
out = stdout.read().decode('utf-8', errors='replace')
print(out if out.strip() else "(エンジン起動ログなし)")

# 4. ログの最終行
print("=== auto_post.log 最終20行 ===")
stdin, stdout, stderr = client.exec_command("tail -20 /home/kusanagi/scripts/auto_post.log 2>/dev/null")
print(stdout.read().decode('utf-8', errors='replace'))

# 5. pending在庫数
print("=== pending在庫数 ===")
stdin, stdout, stderr = client.exec_command("cd /home/kusanagi/scripts && python3 -c \"import sqlite3; c=sqlite3.connect('novelove_unified.db'); print('pending:', c.execute(\\\"SELECT count(*) FROM novelove_posts WHERE status='pending'\\\").fetchone()[0])\"")
print(stdout.read().decode('utf-8', errors='replace'))

client.close()
