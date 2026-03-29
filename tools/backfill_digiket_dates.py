#!/usr/bin/env python3
"""
既存の novelove_digiket.db にある全レコードのうち
release_date が空 or NULL のものに対して
詳細ページをスクレイピングして発売日を補完するスクリプト。
"""
import sys, os
# tools/ の親ディレクトリ（scripts/）をモジュール検索パスに追加
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)


from novelove_fetcher import scrape_digiket_description
from novelove_core import logger, db_connect, DB_FILE_DIGIKET
import time

def backfill_digiket_dates():
    conn = db_connect(DB_FILE_DIGIKET)
    conn.row_factory = __import__('sqlite3').Row
    c = conn.cursor()

    rows = c.execute("""
        SELECT product_id, product_url
        FROM novelove_posts
        WHERE (release_date IS NULL OR release_date = '')
          AND product_url IS NOT NULL AND product_url != ''
    """).fetchall()

    total = len(rows)
    logger.info(f"=== DigiKet 発売日バックフィル開始 ({total}件) ===")

    updated = 0
    skipped = 0

    for i, row in enumerate(rows, 1):
        pid = row['product_id']
        url = row['product_url']
        logger.info(f"  [{i}/{total}] {pid}: {url[:50]}")

        try:
            _, _, _, _, release_date = scrape_digiket_description(url)
            if release_date:
                c.execute(
                    "UPDATE novelove_posts SET release_date=? WHERE product_id=?",
                    (release_date, pid)
                )
                conn.commit()
                logger.info(f"    ✅ 発売日補完: {release_date}")
                updated += 1
            else:
                logger.info(f"    ⚠️  日付取得できず: スキップ")
                skipped += 1
        except Exception as e:
            logger.error(f"    ❌ エラー: {e}")
            skipped += 1

        time.sleep(1.5)  # サーバー負荷対策

    conn.close()
    logger.info(f"=== 完了: 更新 {updated}件 / スキップ {skipped}件 ===")

if __name__ == "__main__":
    backfill_digiket_dates()
