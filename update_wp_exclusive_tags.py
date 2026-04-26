import os
import sys
import time
import sqlite3
import requests
import json
from dotenv import load_dotenv

# .env を読み込んで本番と同じ設定を使用
load_dotenv()
WP_SITE_URL = os.getenv("WP_SITE_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")

DB_DIR = "/home/kusanagi/scripts"
DB_PATHS = [
    f"{DB_DIR}/novelove_unified.db"
]

def get_or_create_tag(name):
    """タグ名を指定してIDを取得する（存在しなければ作成）"""
    auth = (WP_USER, WP_APP_PASSWORD)
    try:
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/tags", auth=auth, params={"search": name}, timeout=15)
        hits = r.json()
        for hit in hits:
            if hit.get("name") == name:
                return hit["id"]
        # 見つからなかった場合は新規作成
        r2 = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/tags", auth=auth, json={"name": name}, timeout=15)
        return r2.json().get("id")
    except Exception as e:
        print(f"Error fetching/creating tag {name}: {e}")
        return None

def fetch_wp_post_tags(post_id):
    """記事の現在のタグID一覧を取得"""
    auth = (WP_USER, WP_APP_PASSWORD)
    try:
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}", auth=auth, timeout=15)
        data = r.json()
        if "tags" in data:
            return data["tags"]
        return []
    except Exception as e:
        print(f"Error fetching post {post_id}: {e}")
        return None

def update_wp_post_tags(post_id, tag_ids):
    """記事のタグを上書き更新"""
    auth = (WP_USER, WP_APP_PASSWORD)
    try:
        r = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}", auth=auth, json={"tags": tag_ids}, timeout=15)
        if r.status_code in (200, 201):
            return True
        else:
            print(f"Error updating post {post_id}: HTTP {r.status_code}")
            return False
    except Exception as e:
        print(f"Error updating post {post_id}: {e}")
        return False

def main():
    print("=== 公開済み過去記事の専売タグ一括付与 (Phase 2) ===")
    
    # 1. 各サイトの専売タグIDを取得
    tag_names = {
        "DLsite": "DLsite専売",
        "FANZA": "FANZA独占",       # DMM商業も同人等もこれにまとめる
        "Lovecal": "らぶカル独占",
        "DigiKet": "DigiKet限定"
    }
    
    tag_ids = {}
    for site, t_name in tag_names.items():
        tid = get_or_create_tag(t_name)
        if tid:
            tag_ids[site] = tid
            print(f"タグ '{t_name}' -> ID: {tid}")
        else:
            print(f"致命的エラー: タグ '{t_name}' のIDが取得できません")
            sys.exit(1)

    # 2. 各DBから該当する記事一覧を取得して更新
    total_updated = 0
    total_skipped = 0
    total_errors = 0

    for db_path in DB_PATHS:
        if not os.path.exists(db_path):
            continue
            
        print(f"\n--- {os.path.basename(db_path)} の処理開始 ---")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # 投稿済み(published) で 専売(is_exclusive=1) で WPの記事IDがあるものを取得
        rows = c.execute("SELECT product_id, title, site, wp_post_id FROM novelove_posts WHERE status='published' AND is_exclusive=1 AND wp_post_id IS NOT NULL").fetchall()
        
        for idx, row in enumerate(rows):
            pid = row["product_id"]
            title = row["title"]
            site_raw = str(row["site"]).split(":")[0]  # Lovecal 等の抽出
            wp_post_id = row["wp_post_id"]
            
            # 付与すべきタグIDを判定
            target_site = "FANZA"
            if "DLsite" in site_raw: target_site = "DLsite"
            elif "DigiKet" in site_raw: target_site = "DigiKet"
            elif "Lovecal" in site_raw: target_site = "Lovecal"
            
            target_tag_id = tag_ids.get(target_site)
            
            # API叩いて現在のタグ一覧取得
            current_tags = fetch_wp_post_tags(wp_post_id)
            if current_tags is None:
                total_errors += 1
                continue
                
            # 重複防止チェック（すでに付与されていればスキップ）
            if target_tag_id in current_tags:
                total_skipped += 1
                sys.stdout.write(f"\r[{idx+1}/{len(rows)}] [SKIP] 既に付与済み: {title[:20]}")
                sys.stdout.flush()
                continue
                
            # タグを追加して更新
            current_tags.append(target_tag_id)
            success = update_wp_post_tags(wp_post_id, current_tags)
            
            if success:
                total_updated += 1
                print(f"\n✅ [UPDATE] 過去記事に '{tag_names[target_site]}' を付与! (ID: {wp_post_id}): {title}")
            else:
                total_errors += 1
                
            # サーバー負荷対策
            time.sleep(1.0)
            
        conn.close()

    print("\n\n=== 処理完了 ===")
    print(f"🔹 新たにタグを付与した記事: {total_updated} 件")
    print(f"🔹 既に付与済みでスキップ: {total_skipped} 件")
    print(f"🔹 エラー数: {total_errors} 件")

if __name__ == "__main__":
    main()
