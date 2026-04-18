import paramiko
HOST = 'novelove.jp'
USER = 'root'
PASS = '#Dama0228'
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=10)
c.exec_command('pkill -f auto_post_urgent.py')
c.close()
print('Killed remotely')
