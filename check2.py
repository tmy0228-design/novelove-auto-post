import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)
cmds = [
    'find /home/kusanagi/scripts -name "*.log" -mmin -30',
    'ls -la /home/kusanagi/scripts/logs/ 2>/dev/null || echo "no logs dir"',
    'journalctl -u cron --since "30 min ago" --no-pager -n 20 2>/dev/null || echo "no journal"',
    'crontab -l -u root',
    'tail -n 30 /var/log/cron 2>/dev/null || tail -n 30 /var/log/syslog 2>/dev/null | grep -i cron',
]
for c in cmds:
    i,o,e = ssh.exec_command(c)
    o.channel.recv_exit_status()
    out = o.read().decode().strip()
    if out:
        print('>>> ' + c[:60])
        print(out)
        print()
ssh.close()
