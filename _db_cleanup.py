import paramiko
import codecs, sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=30)

DOC_ROOT = "/home/kusanagi/myblog/DocumentRoot"

# リビジョン数の確認
print("=== 現在のリビジョン数 ===")
_, o, _ = client.exec_command(f"cd {DOC_ROOT} && wp post list --post_type=revision --format=count --allow-root 2>&1")
count = o.read().decode('utf-8', errors='replace').strip()
print(f"リビジョン件数: {count}")

# DBサイズ確認（削除前）
print("\n=== DB サイズ（削除前）===")
_, o, _ = client.exec_command(f"cd {DOC_ROOT} && wp db size --allow-root 2>&1")
print(o.read().decode('utf-8', errors='replace').strip())

# リビジョン削除
print("\n=== リビジョン削除実行 ===")
_, o, e = client.exec_command(f"cd {DOC_ROOT} && wp post delete $(wp post list --post_type=revision --format=ids --allow-root) --force --allow-root 2>&1")
out = o.read().decode('utf-8', errors='replace').strip()
err = e.read().decode('utf-8', errors='replace').strip()
if out:
    lines = out.splitlines()
    print(f"削除完了: {len(lines)} 件")
    print(lines[-1] if lines else "")
else:
    print("削除対象なし or エラー:", err)

# DB最適化（OPTIMIZE TABLE）
print("\n=== DB 最適化（OPTIMIZE TABLE）===")
_, o, _ = client.exec_command(f"cd {DOC_ROOT} && wp db optimize --allow-root 2>&1")
print(o.read().decode('utf-8', errors='replace').strip()[:300])

# DBサイズ確認（削除後）
print("\n=== DB サイズ（削除後）===")
_, o, _ = client.exec_command(f"cd {DOC_ROOT} && wp db size --allow-root 2>&1")
print(o.read().decode('utf-8', errors='replace').strip())

client.close()
