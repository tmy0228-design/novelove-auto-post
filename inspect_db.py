import sqlite3
import os

def inspect_db(db_path):
    if not os.path.exists(db_path):
        print(f"{db_path} not found.")
        return
    
    print(f"--- Inspecting {db_path} ---")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # テーブルリスト
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print(f"Tables: {tables}")
    
    if "novelove_posts" in tables:
        # ステータスごとの件数
        statuses = c.execute("SELECT status, COUNT(*) FROM novelove_posts GROUP BY status").fetchall()
        print("Statuses:")
        for s in statuses:
            print(f"  {s[0]}: {s[1]}")
        
        # 公開済みデータのサンプル
        row = c.execute("SELECT product_id, title, site, genre, status, wp_post_url FROM novelove_posts WHERE status='published' LIMIT 1").fetchone()
        if row:
            print("Sample Published Row:")
            print(dict(row))
        else:
            print("No published rows found.")
            # 試しに全件の最初の1件
            any_row = c.execute("SELECT * FROM novelove_posts LIMIT 1").fetchone()
            if any_row:
                print("Sample Any Row (first one):")
                print(dict(any_row))
    
    conn.close()

inspect_db("novelove.db")
inspect_db("novelove_dlsite.db")
