import paramiko
import codecs, sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

# wp-config.phpの場所を特定
print("=== wp-config.php の場所 ===")
_, o, _ = client.exec_command("find /home/kusanagi/ -name 'wp-config.php' -maxdepth 4 2>/dev/null")
paths = o.read().decode('utf-8', errors='replace').strip()
print(paths)

# WPのルートを特定
print("\n=== WP-CLIのパス確認 ===")
_, o, _ = client.exec_command("find /home/kusanagi/ -name 'wp-load.php' -maxdepth 4 2>/dev/null")
print(o.read().decode('utf-8', errors='replace').strip())

# Cocoonキャッシュディレクトリ
print("\n=== wp-contentの場所 ===")
_, o, _ = client.exec_command("find /home/kusanagi/ -name 'wp-content' -type d -maxdepth 4 2>/dev/null")
print(o.read().decode('utf-8', errors='replace').strip())

client.close()
