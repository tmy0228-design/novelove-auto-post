import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)
i,o,e = ssh.exec_command('cd /home/kusanagi/scripts && git pull origin main', timeout=30)
o.channel.recv_exit_status()
print(o.read().decode())
print(e.read().decode())
ssh.close()
