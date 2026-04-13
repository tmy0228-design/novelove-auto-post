import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)
script = '''
rm -f /home/kusanagi/scripts/emergency_stop.lock
su - kusanagi -c "cd /home/kusanagi/scripts; /opt/kusanagi/bin/python3 auto_post.py"
'''
i,o,e = ssh.exec_command(script)
o.channel.recv_exit_status()
print(o.read().decode())
print('=== ERROR if any ===')
print(e.read().decode())
ssh.close()
