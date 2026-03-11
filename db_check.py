import sqlite3
import os

DB_DLSITE = "novelove_dlsite.db"
DB_FANZA = "novelove.db"

def check_db(db_path):
    if not os.path.exists(db_path):
        print(f"{db_path} does not exist.")
        return
    
    print(f"--- Checking {db_path} ---")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 問題1: アフィリエイトIDの間違い確認
    rows_id = c.execute("SELECT product_id, title, site, affiliate_url FROM novelove_posts WHERE site LIKE 'DLsite%' AND affiliate_url LIKE '%novelove-001%';").fetchall()
    print(f"DLsite items with wrong affiliate ID (novelove-001): {len(rows_id)}")
    for r in rows_id[:5]:
        print(f"  {r['product_id']}: {r['title']} -> {r['affiliate_url']}")
    
    # 問題2: タグの間違い確認 (DB上のレコードから推測)
    # genre が doujin_tl や doujin_bl なのに site に FANZA という文字列が含まれているもの、
    # または site が DLsite なのに tag に関する情報が混在しているかを確認（DBにはタグ自体は保存されていない場合が多いが、siteカラムに情報がある可能性がある）
    rows_site = c.execute("SELECT product_id, title, site, genre FROM novelove_posts WHERE site LIKE 'DLsite%';").fetchall()
    print(f"DLsite items total in this DB: {len(rows_site)}")
    
    conn.close()

check_db(DB_FANZA)
check_db(DB_DLSITE)
