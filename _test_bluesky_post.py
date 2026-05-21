import paramiko
import codecs
import sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

test_script = r'''
import os
import sys
sys.path.insert(0, '/home/kusanagi/scripts')

# .env読み込み
from dotenv import load_dotenv
load_dotenv('/home/kusanagi/scripts/.env')

from novelove_bluesky import post_to_bluesky

result = post_to_bluesky(
    title="【テスト投稿】Bluesky連携動作確認",
    genre="comic_bl",
    excerpt="これはNoveloveの自動投稿システムからのテスト投稿です。正常に動作していることを確認するためのものです。",
    url="https://novelove.jp/",
    wp_tags_str="テスト",
    image_url="",
    is_r18=False
)

if result:
    print("SUCCESS: Bluesky投稿成功！")
else:
    print("FAILED: Bluesky投稿失敗")
'''

sftp = client.open_sftp()
with sftp.file('/tmp/test_bluesky.py', 'w') as f:
    f.write(test_script.encode('utf-8'))
sftp.close()

print("=== Bluesky テスト投稿を実行中... ===")
stdin, stdout, stderr = client.exec_command("cd /home/kusanagi/scripts && python3 /tmp/test_bluesky.py", timeout=30)
print(stdout.read().decode('utf-8', errors='replace'))
err = stderr.read().decode('utf-8', errors='replace')
if err:
    print("STDERR:", err)

client.close()
