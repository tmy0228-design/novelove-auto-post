import paramiko
import codecs, sys
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('novelove.jp', username='root', password='#Dama0228', port=22, timeout=15)

DOC_ROOT = "/home/kusanagi/myblog/DocumentRoot"

# FIFUに登録されている画像URLのサンプル取得
print("=== FIFU画像URLサンプル（最新10件）===")
cmd = f"""cd {DOC_ROOT} && wp post meta list $(wp post list --post_type=post --posts_per_page=10 --format=ids --allow-root | tr ' ' '\n' | head -10 | tr '\n' ' ') --keys=fifu_image_url --format=table --allow-root 2>/dev/null"""
_, o, _ = client.exec_command(cmd)
print(o.read().decode('utf-8', errors='replace').strip()[:2000])

# 別の方法: 直接DB問い合わせ
print("\n=== DB直接: fifu_image_url サンプル ===")
cmd2 = f"""cd {DOC_ROOT} && wp db query "SELECT post_id, meta_value FROM wp_postmeta WHERE meta_key='fifu_image_url' ORDER BY post_id DESC LIMIT 15" --allow-root 2>&1"""
_, o, _ = client.exec_command(cmd2)
print(o.read().decode('utf-8', errors='replace').strip()[:3000])

# フルサイズ画像が何件あるか
print("\n=== フルサイズ画像の件数 ===")
# DLsite modpub (should be resize)
cmd3 = f"""cd {DOC_ROOT} && wp db query "SELECT COUNT(*) as dlsite_fullsize FROM wp_postmeta WHERE meta_key='fifu_image_url' AND meta_value LIKE '%modpub%img_main.jpg'" --allow-root 2>&1"""
_, o, _ = client.exec_command(cmd3)
print("DLsite modpub (フルサイズ):", o.read().decode('utf-8', errors='replace').strip())

# DMM pl.jpg (should be ps.jpg)  
cmd4 = f"""cd {DOC_ROOT} && wp db query "SELECT COUNT(*) as dmm_fullsize FROM wp_postmeta WHERE meta_key='fifu_image_url' AND meta_value LIKE '%ebook-assets%pl.jpg'" --allow-root 2>&1"""
_, o, _ = client.exec_command(cmd4)
print("DMM pl.jpg (フルサイズ):", o.read().decode('utf-8', errors='replace').strip())

# 正しくサムネになっている件数
cmd5 = f"""cd {DOC_ROOT} && wp db query "SELECT COUNT(*) as dlsite_thumb FROM wp_postmeta WHERE meta_key='fifu_image_url' AND meta_value LIKE '%resize%300x300.webp'" --allow-root 2>&1"""
_, o, _ = client.exec_command(cmd5)
print("DLsite resize (サムネ):", o.read().decode('utf-8', errors='replace').strip())

cmd6 = f"""cd {DOC_ROOT} && wp db query "SELECT COUNT(*) as dmm_thumb FROM wp_postmeta WHERE meta_key='fifu_image_url' AND meta_value LIKE '%ebook-assets%ps.jpg'" --allow-root 2>&1"""
_, o, _ = client.exec_command(cmd6)
print("DMM ps.jpg (サムネ):", o.read().decode('utf-8', errors='replace').strip())

# 全件数
cmd7 = f"""cd {DOC_ROOT} && wp db query "SELECT COUNT(*) as total FROM wp_postmeta WHERE meta_key='fifu_image_url'" --allow-root 2>&1"""
_, o, _ = client.exec_command(cmd7)
print("FIFU画像URL 総数:", o.read().decode('utf-8', errors='replace').strip())

client.close()
