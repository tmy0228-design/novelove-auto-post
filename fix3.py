import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)
script = '''
import subprocess, os
result = subprocess.run(['openssl', 'passwd', '-apr1', '#Dama0228'], capture_output=True, text=True)
pw_hash = result.stdout.strip()
with open('/home/kusanagi/scripts/.htpasswd_nexus', 'w') as f:
    f.write('admin:' + pw_hash + chr(10))
print('hash=' + pw_hash)
with open('/home/kusanagi/scripts/.htpasswd_nexus') as f:
    print('file=' + f.read())
'''
i,o,e = ssh.exec_command('python3 -c "' + script.replace('"', '\\"') + '"')
o.channel.recv_exit_status()
print(o.read().decode())
print(e.read().decode())
i,o,e = ssh.exec_command('systemctl reload nginx')
o.channel.recv_exit_status()
print('nginx reloaded')
ssh.close()
print('DONE')
