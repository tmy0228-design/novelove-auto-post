import sqlite3
import os
import sys

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from novelove_core import DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET

dbs = {
    'FANZA/DMM/LoveCal': DB_FILE_FANZA,
    'DLsite': DB_FILE_DLSITE,
    'DigiKet': DB_FILE_DIGIKET
}

print('=== 最近の審査スコア分布と在庫状況 ===')
for name, db_path in dbs.items():
    if not os.path.exists(db_path):
        print(f"File not found: {db_path}")
        continue
        
    try:
        conn = sqlite3.connect(db_path)
        # 総合待機(pending)
        total_pending = conn.execute("SELECT count(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
        print(f'\n■ {name} (投稿待ち在庫: 計 {total_pending} 件)')
        
        print('  [在庫(pending)のスコア別内訳]')
        rows = conn.execute("SELECT desc_score, COUNT(*) FROM novelove_posts WHERE status='pending' GROUP BY desc_score ORDER BY desc_score DESC").fetchall()
        for r in rows:
            print(f'    - Score {r[0]}: {r[1]}件')
            
        print('\n  [直近7日間に実施された全審査結果のスコア分布]')
        rows = conn.execute("SELECT desc_score, COUNT(*) FROM novelove_posts WHERE inserted_at > datetime('now','-7 days', 'localtime') GROUP BY desc_score ORDER BY desc_score DESC").fetchall()
        for r in rows:
            pct = (r[1] / max(sum([x[1] for x in rows]), 1)) * 100
            print(f'    - Score {r[0]}: {r[1]}件 ({pct:.1f}%)')
        
    except Exception as e:
        print(f"Error reading DB {name}: {e}")
    finally:
        conn.close()
