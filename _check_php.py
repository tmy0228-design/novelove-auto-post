import paramiko
import codecs, sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

# 現在のPHPバージョン確認
print("=== 現在のPHPバージョン ===")
_, o, _ = client.exec_command("php -v 2>&1 | head -1")
print(o.read().decode('utf-8', errors='replace').strip())

# KUSANAGIのPHP管理コマンド確認
print("\n=== KUSANAGI PHP管理コマンドのヘルプ ===")
_, o, _ = client.exec_command("kusanagi php --help 2>&1 || kusanagi php 2>&1")
print(o.read().decode('utf-8', errors='replace').strip())

# 利用可能なPHPバージョン一覧
print("\n=== インストール済みPHPバージョン ===")
_, o, _ = client.exec_command("ls /opt/kusanagi/bin/php* 2>/dev/null || rpm -qa | grep php 2>/dev/null | head -10")
print(o.read().decode('utf-8', errors='replace').strip())

# WordPressが使っているPHPパス
print("\n=== WP-CLIのPHP ===")
_, o, _ = client.exec_command("which php && php --version 2>&1 | head -1")
print(o.read().decode('utf-8', errors='replace').strip())

client.close()
