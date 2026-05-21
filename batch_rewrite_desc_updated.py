#!/usr/bin/env python3
"""
is_desc_updated=1 かつ last_rewritten_at >= 2026-04-16 の記事を
nexus_rewrite.py の run_rewrite() で正規リライト実行するバッチスクリプト。

使い方:
  python3 batch_rewrite_desc_updated.py --dry-run   # 対象確認のみ
  python3 batch_rewrite_desc_updated.py --execute   # 実際にリライト実行
"""
import sys
import sqlite3
import time
import os

# nexus_rewrite.py と同じディレクトリで実行されることを前提
sys.path.insert(0, '/home/kusanagi/scripts')
os.chdir('/home/kusanagi/scripts')

DB_PATH = '/home/kusanagi/scripts/novelove_unified.db'
REWRITE_FROM_DATE = '2026-04-16'

def get_target_products():
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT product_id, title, last_rewritten_at,
                  substr(description, 1, 60) as new_desc_preview,
                  substr(prev_description, 1, 60) as old_desc_preview,
                  length(description) as new_len,
                  length(prev_description) as old_len
           FROM novelove_posts
           WHERE is_desc_updated = 1
             AND last_rewritten_at >= ?
           ORDER BY last_rewritten_at
        """,
        (REWRITE_FROM_DATE,)
    ).fetchall()
    conn.close()
    return rows

def main():
    dry_run = '--execute' not in sys.argv
    
    print("=" * 60)
    print("バッチリライト: is_desc_updated=1 & rewritten_at >= " + REWRITE_FROM_DATE)
    print(f"モード: {'DRY-RUN（確認のみ）' if dry_run else '🚀 EXECUTE（実際に実行）'}")
    print("=" * 60)
    
    targets = get_target_products()
    print(f"\n対象件数: {len(targets)} 件\n")
    
    for i, row in enumerate(targets, 1):
        print(f"[{i:02d}/{len(targets):02d}] {row['product_id']}")
        print(f"       タイトル: {row['title'][:40] if row['title'] else '(不明)'}...")
        print(f"       最終リライト: {row['last_rewritten_at']}")
        print(f"       旧あらすじ({row['old_len']}字): {row['old_desc_preview']}...")
        print(f"       新あらすじ({row['new_len']}字): {row['new_desc_preview']}...")
    
    if dry_run:
        print("\n\n✅ dry-run 完了。--execute オプションをつけて実行してください。")
        return
    
    # 実際にリライト実行
    print("\n\n🚀 リライト開始...\n")
    from nexus_rewrite import run_rewrite
    
    success = 0
    failed = 0
    
    for i, row in enumerate(targets, 1):
        pid = row['product_id']
        print(f"\n[{i:02d}/{len(targets):02d}] リライト中: {pid}")
        print(f"       {row['title'][:50] if row['title'] else '(不明)'}...")
        
        try:
            result = run_rewrite(product_id=pid, execute=True)
            if result:
                print(f"       ✅ 成功")
                success += 1
            else:
                print(f"       ⚠️ run_rewrite が None を返しました（スキップ）")
                failed += 1
        except Exception as e:
            print(f"       ❌ エラー: {e}")
            failed += 1
        
        # API負荷軽減のため記事間に少し待機
        if i < len(targets):
            print(f"       60秒待機中...")
            time.sleep(60)
    
    print("\n" + "=" * 60)
    print(f"バッチ完了: 成功={success}, 失敗={failed}, 合計={len(targets)}")
    print("=" * 60)

if __name__ == '__main__':
    main()
