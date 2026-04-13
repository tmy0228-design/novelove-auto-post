import paramiko
import time

ssh_pass = "#Dama0228"
host = "novelove.jp"
user = "root"

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=ssh_pass)
    
    # Try finding the exact service name first
    stdin, stdout, stderr = ssh.exec_command("ls /etc/systemd/system/ | grep -i dashboard")
    services = stdout.read().decode().strip().split('\n')
    service_name = services[0] if services and services[0] else None
    
    if service_name:
        print(f"Found service: {service_name}. Restarting it...")
        ssh.exec_command(f"systemctl restart {service_name}")
    else:
        print("No systemd service found. Starting via nohup...")
        # Since we don't know the exact port without checking configs, we'll try to find it from history or just run it via poetry/pip
        # Typical command based on CLAUDE.md: /opt/kusanagi/bin/python3 -m streamlit run nexus_dashboard.py
        start_cmd = "cd /home/kusanagi/scripts && nohup /opt/kusanagi/bin/python3 -m streamlit run nexus_dashboard.py > /dev/null 2>&1 &"
        ssh.exec_command(start_cmd)
    
    time.sleep(2)
    # Check if streamlit is running
    stdin, stdout, stderr = ssh.exec_command("ps aux | grep streamlit | grep -v grep")
    print("Running streamlit processes:", stdout.read().decode())
    
    ssh.close()
    print("Restart process completed.")
except Exception as e:
    print(f"Error: {e}")
