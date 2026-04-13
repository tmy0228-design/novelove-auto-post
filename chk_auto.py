import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)

# 1. 最新ログ
i,o,e = ssh.exec_command('tail -20 /home/kusanagi/scripts/novelove_auto.log', timeout=15)
o.channel.recv_exit_status()
print('=== 最新ログ ===')
print(o.read().decode())

# 2. cron確認
i,o,e = ssh.exec_command('crontab -l 2>/dev/null | grep novelove', timeout=10)
o.channel.recv_exit_status()
print('=== cron ===')
print(o.read().decode())

# 3. 緊急停止ファイル確認
i,o,e = ssh.exec_command('ls -la /home/kusanagi/scripts/.emergency_stop* /home/kusanagi/scripts/.main.lock* /home/kusanagi/scripts/.cooldown* 2>/dev/null', timeout=10)
o.channel.recv_exit_status()
print('=== ロックファイル ===')
print(o.read().decode())

ssh.close()
