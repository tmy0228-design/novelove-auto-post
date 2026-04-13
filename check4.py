import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)
i,o,e = ssh.exec_command('tail -n 100 /home/kusanagi/scripts/novelove.log')
o.channel.recv_exit_status()
print(o.read().decode()[-3000:])
ssh.close()
