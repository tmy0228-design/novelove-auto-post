import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)
stdin, stdout, stderr = ssh.exec_command('find /etc -name "*nginx*.conf" | xargs grep -i 8501')
print('=== Nginx confs for 8501 ===\n' + stdout.read().decode())
stdin, stdout, stderr = ssh.exec_command('find /home/kusanagi/*/log/nginx -name "*error.log" -o -name "*_error.log" | xargs tail -n 20')
print('=== Nginx errors ===\n' + stdout.read().decode())
ssh.close()
