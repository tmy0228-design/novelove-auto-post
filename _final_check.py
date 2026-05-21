import paramiko
import codecs
import sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

# cronと同じPython（/opt/kusanagi/bin/python3）でatprotoが使えるか
print("=== /opt/kusanagi/bin/python3 で atproto import ===")
stdin, stdout, stderr = client.exec_command("/opt/kusanagi/bin/python3 -c 'from atproto import Client; print(\"OK\")'")
out = stdout.read().decode('utf-8', errors='replace')
err = stderr.read().decode('utf-8', errors='replace')
print(out.strip() if out.strip() else err.strip())

# pip3がどのPythonのものか
print("\n=== pip3 install先の確認 ===")
stdin, stdout, stderr = client.exec_command("pip3 show atproto 2>/dev/null | head -5")
print(stdout.read().decode('utf-8', errors='replace'))

# /opt/kusanagi/bin/python3 のsite-packages
print("=== /opt/kusanagi/bin/python3 のパス ===")
stdin, stdout, stderr = client.exec_command("/opt/kusanagi/bin/python3 -c 'import sys; print(\"\\n\".join(sys.path))'")
print(stdout.read().decode('utf-8', errors='replace'))

# python3 のパス
print("=== python3 のパス ===")
stdin, stdout, stderr = client.exec_command("python3 -c 'import sys; print(\"\\n\".join(sys.path))'")
print(stdout.read().decode('utf-8', errors='replace'))

client.close()
