import paramiko
import codecs, sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

DOC_ROOT = "/home/kusanagi/myblog/DocumentRoot"
PROFILE = "/home/kusanagi/myblog"

checks = [
    ("=== bcache状態 ===", f"cd {PROFILE} && kusanagi bcache status 2>&1"),
    ("=== fcache状態 ===", f"cd {PROFILE} && kusanagi fcache status 2>&1"),
    ("=== WP-Cron確認 ===", f"grep -i 'DISABLE_WP_CRON\\|WP_CRON' {DOC_ROOT}/wp-config.php 2>&1"),
    ("=== PHP-FPM設定 ===", "find /etc/opt/kusanagi/ -name '*.conf' -path '*/fpm*' -exec grep -l 'pm' {} \\; 2>&1"),
    ("=== PHP-FPM pm設定 ===", "find /etc/opt/kusanagi/ -name '*.conf' -path '*/fpm*' -exec grep -E '^(pm |pm\\.)' {} \\; 2>&1"),
    ("=== Nginx vhost設定 ===", f"ls /etc/nginx/conf.d/ 2>&1"),
    ("=== Nginx FastCGI Cache (vhost内) ===", f"grep -r 'fastcgi_cache\\|proxy_cache' /etc/nginx/conf.d/ 2>&1 | head -15"),
    ("=== Cocoonキャッシュ確認 ===", f"ls -la {DOC_ROOT}/wp-content/cache/ 2>&1 | head -10"),
    ("=== サーバー実測(ローカル5回) ===", 
     "for i in 1 2 3 4 5; do curl -o /dev/null -s -w \"$i: TTFB=%{time_starttransfer}s Total=%{time_total}s\\n\" https://novelove.jp/; done"),
    ("=== サーバー実測(記事ページ) ===",
     f"curl -o /dev/null -s -w 'TTFB: %{{time_starttransfer}}s Total: %{{time_total}}s' https://novelove.jp/d_768109/ 2>&1"),
]

for label, cmd in checks:
    print(label)
    _, o, e = client.exec_command(cmd)
    out = o.read().decode('utf-8', errors='replace').strip()
    err = e.read().decode('utf-8', errors='replace').strip()
    print(out if out else err if err else "(empty)")
    print()

client.close()
