import sqlite3
import requests
import re
import sys
import time
import subprocess
from novelove_core import WP_SITE_URL, WP_USER, WP_APP_PASSWORD, DB_FILE_UNIFIED
# format_author_detail は新パースロジックに統合済みのため不要

def get_db_conn():
    return sqlite3.connect(DB_FILE_UNIFIED)

def build_specs_html(release_date, author_detail, cast_info, page_count, fallback_author=None, is_dlsite=False, is_voice=False):
    specs = []
    
    # 発売日の追加
    if release_date and isinstance(release_date, str) and len(release_date) >= 4:
        formatted_date = release_date[:10].replace("-", "/")
        specs.append(f"発売日: {formatted_date}")
    
    def clean_txt(t):
        if not t: return ""
        return t.replace("\r", "").replace("\n", "").replace("\xa0", " ").strip()

    # 著者詳細のパース（完全版 v2）
    # 全パターン対応: VALID_ROLES バリデーション / 日付・時刻ゴミ排除 /
    # コロンなし部分は直前の役割を引き継ぎ / 掲載終了等のゴミ値排除
    _VALID_ROLES = frozenset(['著者', 'サークル', '出版社', 'レーベル', 'シナリオ', 'イラスト', '声優(CV)', '原作', 'WA'])
    _DATE_RE = re.compile(r'\d{4}[-/]\d{2}[-/]\d{2}')
    _TIME_RE = re.compile(r'\d{2}:\d{2}:\d{2}')
    _GARBAGE = ('掲載終了', '情報')
    _ROLE_ORDER = ['著者', 'シナリオ', 'イラスト', '原作', 'WA', 'サークル', '出版社', 'レーベル', '声優(CV)']

    if author_detail:
        author_detail = clean_txt(author_detail)
        _raw_parts = [p.strip() for p in author_detail.split(',') if p.strip()]
        _role_to_names = {}
        _last_role = None

        for _part in _raw_parts:
            # ① 日付・時刻パターンはゴミとして除外
            if _DATE_RE.search(_part) or _TIME_RE.search(_part):
                continue

            if ':' in _part:
                _role, _name = _part.split(':', 1)
                _role = _role.strip()
                _name = _name.strip()
                # ② 未知の役割名（発売日など）は除外
                if _role not in _VALID_ROLES:
                    continue
                # ③ ゴミ値（掲載終了・情報など）は除外
                if any(_g in _name for _g in _GARBAGE):
                    continue
                if not _name:
                    continue
                _last_role = _role
                _role_to_names.setdefault(_role, [])
                if _name not in _role_to_names[_role]:
                    _role_to_names[_role].append(_name)
            else:
                # ④ コロンなし → 直前の役割を引き継ぐ（例: 著者:A,B,C）
                _name = _part
                if not _name or any(_g in _name for _g in _GARBAGE):
                    continue
                _role = _last_role or '著者'
                _role_to_names.setdefault(_role, [])
                if _name not in _role_to_names[_role]:
                    _role_to_names[_role].append(_name)

        _seen = set()
        for _r in _ROLE_ORDER:
            if _r in _role_to_names:
                specs.append(f"{_r}: {' / '.join(_role_to_names[_r])}")
                _seen.add(_r)
        for _r, _names in _role_to_names.items():
            if _r not in _seen:
                specs.append(f"{_r}: {' / '.join(_names)}")
    elif fallback_author:
        fallback_author = clean_txt(fallback_author)
        if is_dlsite and "/" in fallback_author:
            sub_parts = [p.strip() for p in fallback_author.split("/") if p.strip()]
            if len(sub_parts) >= 2:
                specs.append(f"レーベル: {sub_parts[0]}")
                specs.append(f"著者: {sub_parts[1]}")
            else:
                specs.append(f"サークル: {fallback_author}")
        else:
            if is_dlsite:
                specs.append(f"サークル: {fallback_author}")
            else:
                specs.append(f"著者: {fallback_author}")
        
    # 声優
    if cast_info:
        specs.append(f"声優(CV): {cast_info}")
        
    # ページ数 / 音声本数
    if page_count:
        try:
            pg_val = int(page_count)
            if pg_val > 0:
                if is_voice:
                    specs.append(f"{pg_val}本")
                else:
                    specs.append(f"{pg_val}P")
        except (ValueError, TypeError):
            pass
        
    if not specs:
        return ""
        
    specs_text = " ｜ ".join(specs)
    
    html = f"""<!-- NOVELOVE_SPECS_START -->
<div class="novelove-specs" style="background:#fafafa; border-top:1px solid #eee; border-bottom:1px solid #eee; padding:6px 10px; margin:12px 0; font-size:0.85em; color:#666; text-align:center; line-height:1.5;">
  {specs_text}
</div>
<!-- NOVELOVE_SPECS_END -->\n"""
    return html

def run_ssh_command(client, cmd, stdin_data=None):
    # リストコマンドを安全に文字列化して実行する
    cmd_str = " ".join(cmd)
    stdin, stdout, stderr = client.exec_command(cmd_str)
    
    if stdin_data:
        stdin.write(stdin_data)
        stdin.flush()
    stdin.close()
    
    out = stdout.read().decode('utf-8')
    err = stderr.read().decode('utf-8')
    return out, err, stdout.channel.recv_exit_status()

def update_posts(dry_run=True, target_post_id=None):
    import paramiko
    from novelove_core import SSH_PASS, WP_PHP_PATH, WP_CLI_PATH, WP_DOC_ROOT
    
    conn = get_db_conn()
    c = conn.cursor()
    
    # DBから published な通常記事で、追加データが取得できているものを取得
    # target_post_id が指定されている場合はその記事のみ対象（テスト用）
    if target_post_id:
        c.execute("""
            SELECT wp_post_id, author_detail, cast_info, series_name, page_count, title, author, site, release_date, genre
            FROM novelove_posts
            WHERE wp_post_id=?
        """, (target_post_id,))
    else:
        c.execute("""
            SELECT wp_post_id, author_detail, cast_info, series_name, page_count, title, author, site, release_date, genre
            FROM novelove_posts
            WHERE status='published' 
              AND post_type='regular' 
              AND wp_post_id IS NOT NULL 
              AND wp_post_id != ''
              AND (author_detail IS NOT NULL OR cast_info IS NOT NULL OR series_name IS NOT NULL OR page_count IS NOT NULL OR release_date IS NOT NULL)
        """)
        
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        print("No posts found to update.")
        return
        
    print(f"Found {len(rows)} posts to process. (Dry Run: {dry_run})")
    
    # SSH接続を開始
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    ssh_password = SSH_PASS or "#Dama0228"
    print("Connecting to novelove.jp via SSH...")
    try:
        client.connect("novelove.jp", username="root", password=ssh_password, port=22, timeout=20)
        print("SSH Connection established.")
    except Exception as e:
        print(f"Failed to connect to novelove.jp via SSH: {e}")
        return
        
    success_count = 0
    fail_count = 0
    
    try:
        for idx, (wp_id, auth_det, cast, series, pages, title, author, site, rel_date, genre) in enumerate(rows, 1):
            print(f"[{idx}/{len(rows)}] Processing Post ID {wp_id}: {title}...")
            
            # スペックHTMLの生成
            is_dlsite = site and "DLsite" in str(site)
            is_voice = "voice" in str(genre).lower()
            spec_html = build_specs_html(rel_date, auth_det, cast, pages, fallback_author=author, is_dlsite=is_dlsite, is_voice=is_voice)
            if not spec_html:
                print("  No specs available to insert. Skipping.")
                continue
                
            # SSH経由のWP-CLIで現在の記事本文を取得
            # コマンド: wp post get <id> --field=content
            cmd_get = [WP_PHP_PATH, WP_CLI_PATH, "post", "get", str(wp_id), "--field=content", f"--path={WP_DOC_ROOT}", "--allow-root"]
            out_get, err_get, status_get = run_ssh_command(client, cmd_get)
            
            if status_get != 0:
                print(f"  Failed to fetch post via SSH WP-CLI (status={status_get}, error={err_get[:100].strip()})")
                fail_count += 1
                continue
                
            content_raw = out_get
            
            # 1. 既存のスペック表を削除（二重挿入防止）
            clean_content = re.sub(r'<!-- NOVELOVE_SPECS_START -->.*?<!-- NOVELOVE_SPECS_END -->\s*', '', content_raw, flags=re.DOTALL)
            
            # 2. 既存の「発売日：xxxx/xx/xx」の段落行を削除（二重表示防止）
            clean_content = re.sub(r'<p style="text-align:\s*center;\s*color:\s*#666;\s*font-size:\s*0.9em;\s*margin-bottom:\s*10px;?">発売日：\d{4}[-/]\d{2}[-/]\d{2}</p>\s*', '', clean_content)
            
            # 3. アイキャッチ画像の段落の直後にスペック表を挿入
            img_match = re.search(r'(<p style="text-align:\s*center;\s*margin:\s*20px\s*0;?"><a[^>]*><img[^>]*></a></p>)', clean_content)
            if img_match:
                pos = img_match.end()
                new_content = clean_content[:pos] + "\n" + spec_html + clean_content[pos:]
            else:
                # <h2> がある場合のフォールバック（従来の挙動）
                h2_match = re.search(r'<h2[^>]*>', clean_content)
                if h2_match:
                    pos = h2_match.start()
                    new_content = clean_content[:pos] + spec_html + clean_content[pos:]
                else:
                    # <h2> がない場合のフォールバック（最初の吹き出しの閉じタグの直後）
                    bubble_close = re.search(r'</div>\s*</div>', clean_content)
                    if bubble_close:
                        pos = bubble_close.end()
                        new_content = clean_content[:pos] + "\n" + spec_html + clean_content[pos:]
                    else:
                        new_content = spec_html + clean_content
                    
            if dry_run:
                # ドライラン時はローカルファイルにテスト書き出し
                with open("scratch_test_update.html", "w", encoding="utf-8") as f:
                    f.write(f"<h1>{title}</h1>\n")
                    f.write(new_content)
                print(f"  [Dry Run] Specs HTML generated. Sample written to scratch_test_update.html")
                success_count += 1
                # ドライランで1件のみの場合はループ終了
                if target_post_id:
                    break
            else:
                # 本番適用: wp post update <id> -
                # 標準入力経由でコンテンツを安全に流し込む
                cmd_up = [WP_PHP_PATH, WP_CLI_PATH, "post", "update", str(wp_id), "-", f"--path={WP_DOC_ROOT}", "--allow-root"]
                out_up, err_up, status_up = run_ssh_command(client, cmd_up, stdin_data=new_content)
                
                if status_up == 0:
                    print("  Successfully updated post in WordPress via SSH WP-CLI.")
                    success_count += 1
                else:
                    print(f"  Failed to update post via SSH WP-CLI: status={status_up}, error={err_up[:150].strip()}")
                    fail_count += 1
                    
            time.sleep(0.2)  # 負荷軽減
    finally:
        client.close()
        print("SSH Connection closed.")
            
    print(f"\nUpdate Process Complete.")
    print(f"Total Processed: {len(rows)}")
    print(f"Success: {success_count}")
    print(f"Fail: {fail_count}")

if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass  # Python < 3.7 doesn't support reconfigure, but program runs on 3.10
        
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="Execute real database and WP updates (non dry-run)")
    parser.add_argument("--post_id", type=int, help="Target a specific post ID for testing")
    args = parser.parse_args()
    
    update_posts(dry_run=not args.real, target_post_id=args.post_id)
