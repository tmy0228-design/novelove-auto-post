import paramiko
import sys
import codecs
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)
    
    print("--- サーバー側で最新のGitHubコードをPullします ---")
    stdin, stdout, stderr = client.exec_command("cd /home/kusanagi/scripts && git reset --hard && git clean -fd && git pull origin main")
    
    print(stdout.read().decode('utf-8', errors='replace'))
    err = stderr.read().decode('utf-8', errors='replace')
    if err:
        print("エラー出力:", err)
        
    client.close()
    print("--- サーバー適用完了 ---")
except Exception as e:
    print("エラーが発生しました:", e)
