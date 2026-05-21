import paramiko
import codecs, sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

WP_CONFIG = "/home/kusanagi/myblog/wp-config.php"
DOC_ROOT = "/home/kusanagi/myblog/DocumentRoot"

# Step 1: 現在のwp-config.phpにWP-Cron設定があるか確認
print("=== Step 1: 現在のWP-Cron設定確認 ===")
_, o, _ = client.exec_command(f"grep -n 'DISABLE_WP_CRON\\|WP_CRON' {WP_CONFIG} 2>&1")
existing = o.read().decode('utf-8', errors='replace').strip()
print(existing if existing else "WP-Cron設定なし（デフォルト=有効）")

# Step 2: WP-Cronを無効化（wp-config.phpに追記）
print("\n=== Step 2: WP-Cron無効化を追記 ===")
if "DISABLE_WP_CRON" not in (existing or ""):
    # "That's all" コメントの前に挿入
    cmd = f"""sed -i "/\\/\\* That's all/i define('DISABLE_WP_CRON', true);" {WP_CONFIG} 2>&1"""
    _, o, e = client.exec_command(cmd)
    err = e.read().decode('utf-8', errors='replace').strip()
    if err:
        # 別の方法: "require_once" の前に挿入
        cmd2 = f"""sed -i "/require_once/i define('DISABLE_WP_CRON', true);" {WP_CONFIG} 2>&1"""
        _, o2, e2 = client.exec_command(cmd2)
        err2 = e2.read().decode('utf-8', errors='replace').strip()
        print(f"方法2: {err2 if err2 else '成功'}")
    else:
        print("追記成功")
else:
    print("既に設定済み、スキップ")

# 確認
_, o, _ = client.exec_command(f"grep -n 'DISABLE_WP_CRON' {WP_CONFIG} 2>&1")
print("確認:", o.read().decode('utf-8', errors='replace').strip())

# Step 3: OS crontabに毎分のWP-Cron実行を追加
print("\n=== Step 3: OS crontab設定 ===")
_, o, _ = client.exec_command("crontab -l 2>&1")
current_cron = o.read().decode('utf-8', errors='replace').strip()
print("現在のcrontab:")
print(current_cron)

cron_line = f"*/1 * * * * cd {DOC_ROOT} && /opt/kusanagi/php/bin/php wp-cron.php > /dev/null 2>&1"

if "wp-cron" not in current_cron:
    print("\nWP-Cron用エントリを追加...")
    cmd_add = f'(crontab -l 2>/dev/null; echo "{cron_line}") | crontab - 2>&1'
    _, o, e = client.exec_command(cmd_add)
    out = o.read().decode('utf-8', errors='replace').strip()
    err = e.read().decode('utf-8', errors='replace').strip()
    print(out if out else err if err else "追加成功")
else:
    print("既にwp-cronエントリあり、スキップ")

# 最終確認
print("\n=== 最終確認 ===")
_, o, _ = client.exec_command("crontab -l 2>&1")
print(o.read().decode('utf-8', errors='replace').strip())

# Step 4: 動作確認
print("\n=== Step 4: サイト応答テスト ===")
_, o, _ = client.exec_command("curl -o /dev/null -s -w 'TTFB: %{time_starttransfer}s' https://novelove.jp/ 2>&1")
print(o.read().decode('utf-8', errors='replace').strip())

client.close()
