import paramiko
import time
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)
ssh.exec_command('pkill -f streamlit')
time.sleep(3)
cmd = "su - kusanagi -c 'cd /home/kusanagi/scripts; nohup /opt/kusanagi/bin/python3 -m streamlit run nexus_dashboard.py --server.port=8501 --server.headless=true --server.baseUrlPath=nexus --server.enableCORS=false --server.enableXsrfProtection=false > /home/kusanagi/scripts/dashboard.log 2>&1 &'"
ssh.exec_command(cmd)
time.sleep(3)
stdin, stdout, stderr = ssh.exec_command('ps aux | grep streamlit')
print('=== Process Status ===\n' + stdout.read().decode())
ssh.close()
