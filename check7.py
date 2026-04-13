import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)
# 1. ログのパーミッション修正
ssh.exec_command('chown kusanagi:kusanagi /home/kusanagi/scripts/*.log')
# 2. その他エラーの抽出
i,o,e = ssh.exec_command('grep -iE "error|exception|traceback" /home/kusanagi/scripts/novelove.log | tail -n 30')
o.channel.recv_exit_status()
print('=== novelove.log Errors ===')
print(o.read().decode())
i,o,e = ssh.exec_command('grep -iE "error|exception|traceback" /home/kusanagi/scripts/logs/auto_post.log 2>/dev/null || echo "no log"')
o.channel.recv_exit_status()
print('=== auto_post.log Errors ===')
print(o.read().decode())
ssh.close()
