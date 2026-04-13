import os
import sys
import paramiko

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from novelove_core import SSH_PASS, logger

def update_lovecal_term_meta():
    term_id = 137
    seo_title = "らぶカル（LoveCal）のおすすめ同人BL/TL作品 | ノベラブ"
    meta_desc = "FANZAの同人新フロア「らぶカル（LoveCal）」で配信されているBL/TL（ボーイズラブ/ティーンズラブ）のおすすめ作品一覧です。最新の同人コミック・小説をチェック！"

    if not SSH_PASS:
        print("Error: SSH_PASS is not set.")
        sys.exit(1)

    print("Connecting via SSH...")
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect('novelove.jp', username='root', password=SSH_PASS, timeout=15)
        
        def escape_sh(s):
            return s.replace("'", "'\\''")

        doc_root = os.environ.get("WP_DOC_ROOT", "/home/kusanagi/myblog/DocumentRoot")

        # term body (slug) の更新 (ルール違反の日本語スラッグを修正)
        cmd_slug = f"cd {doc_root} && wp term update {term_id} --slug=lovecal --allow-root"
        print(f"Executing: {cmd_slug}")
        stdin, stdout, stderr = ssh.exec_command(cmd_slug)
        print("Stdout:", stdout.read().decode('utf-8'))
        print("Stderr:", stderr.read().decode('utf-8'))

        # term meta の更新
        cmd_seo = f"cd {doc_root} && wp term meta update {term_id} the_page_seo_title '{escape_sh(seo_title)}' --allow-root"
        print(f"Executing: {cmd_seo}")
        stdin, stdout, stderr = ssh.exec_command(cmd_seo)
        print("Stdout:", stdout.read().decode('utf-8'))
        print("Stderr:", stderr.read().decode('utf-8'))

        cmd_desc = f"cd {doc_root} && wp term meta update {term_id} the_page_meta_description '{escape_sh(meta_desc)}' --allow-root"
        print(f"Executing: {cmd_desc}")
        stdin, stdout, stderr = ssh.exec_command(cmd_desc)
        print("Stdout:", stdout.read().decode('utf-8'))
        print("Stderr:", stderr.read().decode('utf-8'))

        ssh.close()
        print("Completed successfully.")
    except Exception as e:
        print(f"SSH Exception: {e}")

if __name__ == "__main__":
    update_lovecal_term_meta()
