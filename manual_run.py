import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)

# 手動で auto_post.py を実行（クールダウン無視フラグは無いため、現在のステータスを確認）
i,o,e = ssh.exec_command('/opt/kusanagi/bin/python3 /home/kusanagi/scripts/auto_post.py', timeout=30)
o.channel.recv_exit_status()
print(o.read().decode())
print(e.read().decode())

ssh.close()
