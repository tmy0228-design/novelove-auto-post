import paramiko
import codecs
import sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

# 1. atproto をインストール
print("=== Step 1: atproto ライブラリをインストール ===")
stdin, stdout, stderr = client.exec_command("pip3 install atproto", timeout=120)
print(stdout.read().decode('utf-8', errors='replace'))
err = stderr.read().decode('utf-8', errors='replace')
if err:
    print("STDERR:", err)

# 2. インストール確認
print("\n=== Step 2: インストール確認 ===")
stdin, stdout, stderr = client.exec_command("python3 -c 'from atproto import Client, client_utils, models; print(\"OK: atproto import成功\")'")
print(stdout.read().decode('utf-8', errors='replace'))
err2 = stderr.read().decode('utf-8', errors='replace')
if err2:
    print("STDERR:", err2)

# 3. auto_post.py がimportエラーなく読み込めるか
print("\n=== Step 3: auto_post.py の import チェック ===")
stdin, stdout, stderr = client.exec_command("cd /home/kusanagi/scripts && python3 -c 'import auto_post; print(\"OK: auto_post.py import成功\")'")
print(stdout.read().decode('utf-8', errors='replace'))
err3 = stderr.read().decode('utf-8', errors='replace')
if err3:
    print("STDERR:", err3)

client.close()
