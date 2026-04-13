import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)

# 緊急停止ファイルやクールダウンの仕組みを調べる
i,o,e = ssh.exec_command('ls -la /home/kusanagi/scripts/.* 2>/dev/null | grep -v __pycache__', timeout=10)
o.channel.recv_exit_status()
print('=== 隠しファイル ===')
print(o.read().decode())

# novelove_core.pyの緊急停止関連定数を確認
i,o,e = ssh.exec_command('grep -n "COOLDOWN\|emergency\|cooldown\|EMERGENCY" /home/kusanagi/scripts/novelove_core.py /home/kusanagi/scripts/auto_post.py 2>/dev/null | head -30', timeout=10)
o.channel.recv_exit_status()
print('=== クールダウン関連 ===')
print(o.read().decode())

ssh.close()
