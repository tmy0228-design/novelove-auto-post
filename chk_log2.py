import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)
i,o,e = ssh.exec_command('tail -50 /home/kusanagi/scripts/novelove.log', timeout=15)
o.channel.recv_exit_status()
print(o.read().decode())
ssh.close()
