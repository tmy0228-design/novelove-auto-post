import paramiko
import codecs
import sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

# 1. 最新のauto_postログにBluesky関連の出力があるか確認
print("=== 1. auto_post.log の Bluesky 関連ログ ===")
stdin, stdout, stderr = client.exec_command("grep -i 'bluesky\\|bsky' /home/kusanagi/scripts/auto_post.log 2>/dev/null | tail -20")
out = stdout.read().decode('utf-8', errors='replace')
print(out if out.strip() else "(Bluesky関連のログなし)")

# 2. 最近のcron実行状況
print("\n=== 2. 最近の投稿ログ（最新10行）===")
stdin, stdout, stderr = client.exec_command("tail -30 /home/kusanagi/scripts/auto_post.log 2>/dev/null")
print(stdout.read().decode('utf-8', errors='replace'))

# 3. atprotoライブラリがインストールされているか
print("\n=== 3. atproto ライブラリの確認 ===")
stdin, stdout, stderr = client.exec_command("python3 -c 'import atproto; print(atproto.__version__)' 2>&1")
print(stdout.read().decode('utf-8', errors='replace'))

# 4. .envにBluesky認証情報があるか
print("\n=== 4. .env の Bluesky 設定確認 ===")
stdin, stdout, stderr = client.exec_command("grep 'BLUESKY' /home/kusanagi/scripts/.env 2>/dev/null")
out = stdout.read().decode('utf-8', errors='replace')
if out.strip():
    # パスワードはマスクして表示
    for line in out.strip().split('\n'):
        if 'PASSWORD' in line:
            key = line.split('=')[0]
            print(f"{key}=*****(設定済み)")
        else:
            print(line)
else:
    print("(Bluesky設定なし！)")

# 5. 最後にWP投稿が成功した時刻
print("\n=== 5. 最後のWP投稿成功時刻 ===")
stdin, stdout, stderr = client.exec_command("grep '投稿成功' /home/kusanagi/scripts/auto_post.log 2>/dev/null | tail -3")
print(stdout.read().decode('utf-8', errors='replace'))

client.close()
