import sqlite3
import os

dbs = {
    'FANZA/DMM/LoveCal': r'unpacked\home\kusanagi\scripts\novelove.db',
    'DLsite': r'unpacked\home\kusanagi\scripts\novelove_dlsite.db',
    'DigiKet': r'unpacked\home\kusanagi\scripts\novelove_digiket.db'
}

print('=== 最近の審査スコア分布と在庫状況 ===')
for name, db_path in dbs.items():
    db_path = os.path.abspath(db_path)
    if not os.path.exists(db_path):
        print(f"\n■ {name} (データベースファイルが見つかりません)")
        continue
        
    try:
        conn = sqlite3.connect(db_path)
        total_pending = conn.execute("SELECT count(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
        print(f'\n■ {name} (現在の投稿待ち在庫: 計 {total_pending} 件)')
        
        print('  [在庫(pending)の中のスコア内訳]')
        rows = conn.execute("SELECT desc_score, COUNT(*) FROM novelove_posts WHERE status='pending' GROUP BY desc_score ORDER BY desc_score DESC").fetchall()
        for r in rows:
            print(f'    - Score {r[0]}: {r[1]}件')
            
        print('  [直近7日間の全審査結果（除外含む全件）の分布]')
        try:
            rows = conn.execute("SELECT desc_score, COUNT(*) FROM novelove_posts WHERE inserted_at > datetime('now','-7 days','localtime') GROUP BY desc_score ORDER BY desc_score DESC").fetchall()
            total_7d = sum([x[1] for x in rows])
            if total_7d > 0:
                for r in rows:
                    pct = (r[1] / total_7d) * 100
                    print(f'    - Score {r[0]}: {r[1]}件 ({pct:.1f}%)')
            else:
                print('    - データなし')
        except Exception as e:
            print(f'    - 集計中エラー: {e}')
            
    except Exception as e:
        print(f"\n■ {name} (エラー: {e})")
    finally:
        conn.close()
