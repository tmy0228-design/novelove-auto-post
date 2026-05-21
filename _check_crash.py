import paramiko
import codecs
import sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

# cronの実際の出力先ログを確認
print("=== novelove_auto.log 最終30行 ===")
stdin, stdout, stderr = client.exec_command("tail -30 /home/kusanagi/scripts/novelove_auto.log 2>/dev/null")
print(stdout.read().decode('utf-8', errors='replace'))

# WPの最終投稿日時（DB）
print("\n=== DB: 最後に投稿された記事 ===")
stdin, stdout, stderr = client.exec_command(
    "cd /home/kusanagi/scripts && python3 -c \""
    "import sqlite3; c=sqlite3.connect('novelove_unified.db'); c.row_factory=sqlite3.Row; "
    "r=c.execute(\\\"SELECT title, published_at, site FROM novelove_posts WHERE status='published' ORDER BY published_at DESC LIMIT 3\\\").fetchall(); "
    "[print(f'{row[1]} | {row[2]} | {row[0][:30]}') for row in r]\""
)
print(stdout.read().decode('utf-8', errors='replace'))

# importエラーがログに出ていないか
print("\n=== importエラーの有無 ===")
stdin, stdout, stderr = client.exec_command("grep -i 'ModuleNotFoundError\\|ImportError\\|No module' /home/kusanagi/scripts/novelove_auto.log 2>/dev/null | tail -5")
out = stdout.read().decode('utf-8', errors='replace')
print(out if out.strip() else "(importエラーなし)")

client.close()
