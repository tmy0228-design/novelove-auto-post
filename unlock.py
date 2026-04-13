import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)

# 緊急停止ロック解除
i,o,e = ssh.exec_command('rm -f /home/kusanagi/scripts/emergency_stop.lock && echo "OK: ロック解除完了"', timeout=10)
o.channel.recv_exit_status()
print(o.read().decode())

ssh.close()
