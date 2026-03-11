import sqlite3
import os

DB_FILES = ["novelove.db", "novelove_dlsite.db"]

NG_KEYWORDS = [
    "ボイス", "音声", "ASMR", "CV.", "CV:", "cv.", "cv:", "シチュエーションCD", "バイノーラル", "ドラマCD",
    "簡体中文", "繁体中文", "繁體中文", "English", "韓国語版", "中国語版", "中国語",
    "한국어", "中文字幕", "翻訳台詞",
    "ゲーム", "アニメ", "CG集", "ノベル", "小説", "実用"
]

def check_db():
    total_found = 0
    total_updated = 0
    
    for db_path in DB_FILES:
        if not os.path.exists(db_path):
            continue
            
        print(f"\n--- Checking {db_path} ---")
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        
        # すべてのレコードを取得してPython側でチェック
        c.execute("SELECT product_id, title, status, desc_score FROM novelove_posts")
        rows = c.fetchall()
        
        noise_records = []
        for row in rows:
            pid, title, status, score = row
            if title:
                is_noise = any(kw.lower() in title.lower() for kw in NG_KEYWORDS)
                if is_noise:
                    noise_records.append((pid, title, status, score))
                    
        print(f"ノイズ判定されたレコード: {len(noise_records)} 件")
        total_found += len(noise_records)
        
        for pid, title, status, score in noise_records:
            if status != 'excluded' and status != 'failed':
                print(f"  [要更新] ID: {pid} | Title: {title[:30]} | Status: {status} | Score: {score}")
                # 強制的にexcludedにする
                c.execute("UPDATE novelove_posts SET status='excluded' WHERE product_id=?", (pid,))
                total_updated += 1
            else:
                pass # 既にexcludedかfailedならOK
                
        conn.commit()
        conn.close()
        
    print(f"\n--- まとめ ---")
    print(f"検出された合計ノイズレコード数: {total_found}")
    print(f"ステータスを 'excluded' に更新した数: {total_updated}")

if __name__ == "__main__":
    check_db()
