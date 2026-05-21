import paramiko
import codecs, sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

checks = [
    ("=== KUSANAGIプロファイル状態 ===", "kusanagi status 2>&1"),
    ("=== KUSANAGIキャッシュ設定 ===", "kusanagi bcache status 2>&1"),
    ("=== FastCGIキャッシュ ===", "kusanagi fcache status 2>&1"),
    ("=== PHP OPcache状態 ===", "php -r \"var_dump(opcache_get_status(false));\" 2>&1 | head -20"),
    ("=== PHP-FPMプロセス設定 ===", "cat /etc/opt/kusanagi/php/php-fpm.d/www.conf 2>/dev/null | grep -E '(pm\\.|pm =)' | head -10"),
    ("=== Nginx WorkerとCache ===", "grep -E '(worker_|fastcgi_cache|proxy_cache)' /etc/nginx/nginx.conf 2>/dev/null | head -10"),
    ("=== WP-Cron設定 ===", "grep DISABLE_WP_CRON /home/kusanagi/myblog/DocumentRoot/wp-config.php 2>/dev/null"),
    ("=== DBクエリキャッシュ ===", "mysql -e \"SHOW VARIABLES LIKE 'query_cache%';\" 2>&1 | head -5"),
    ("=== PHPメモリ制限 ===", "php -r \"echo ini_get('memory_limit');\" 2>&1"),
    ("=== Nginx FastCGI Cache有効か ===", "grep -r 'fastcgi_cache' /etc/nginx/conf.d/ 2>/dev/null | head -5"),
    ("=== KUSANAGIプロファイル一覧 ===", "kusanagi profile list 2>&1 | head -5"),
    ("=== サーバー応答テスト（実測）===", "curl -o /dev/null -s -w 'TTFB: %{time_starttransfer}s Total: %{time_total}s' https://novelove.jp/ 2>&1"),
]

for label, cmd in checks:
    print(label)
    _, o, e = client.exec_command(cmd)
    out = o.read().decode('utf-8', errors='replace').strip()
    err = e.read().decode('utf-8', errors='replace').strip()
    print(out if out else err if err else "(empty)")
    print()

client.close()
