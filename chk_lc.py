import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('novelove.jp', username='root', password='#Dama0228', timeout=15)
cmd = "/opt/kusanagi/bin/python3 -c \"import sqlite3; c=sqlite3.connect('/home/kusanagi/scripts/novelove.db'); rows=c.execute(\\\"SELECT status, COUNT(*) FROM novelove_posts WHERE product_url LIKE '%lovecul%' GROUP BY status\\\").fetchall(); [print(f'{r[0]}: {r[1]}件') for r in rows]; c.close()\""
i,o,e = ssh.exec_command(cmd, timeout=15)
o.channel.recv_exit_status()
print(o.read().decode())
print(e.read().decode())
ssh.close()
