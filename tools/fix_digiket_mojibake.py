#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DigiKet DB 文字化けクリーンアップスクリプト
- STEP 1: バックアップ
- STEP 2: ドライラン（対象件数の確認のみ）
- STEP 3: 削除実行（--execute フラグで実行）
"""
import sqlite3
import shutil
import os
import sys
from datetime import datetime

DB_PATH = "novelove_digiket.db"
MOJIBAKE = chr(0xFFFD)  # U+FFFD (Replacement Character)


def main():
    execute_mode = "--execute" in sys.argv

    # ===== STEP 1: バックアップ =====
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"novelove_digiket_backup_{ts}.db"
    shutil.copy2(DB_PATH, backup_name)
    print(f"[BACKUP] {backup_name} ({os.path.getsize(backup_name):,} bytes)")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ===== STEP 2: 完全分析 =====
    total = cur.execute("SELECT count(*) FROM novelove_posts").fetchone()[0]
    print(f"\n{'='*60}")
    print(f"[DB] 全レコード数: {total}")
    print(f"{'='*60}")

    # ステータス別の全体像
    print("\n[全体] ステータス別内訳:")
    for row in cur.execute("SELECT status, count(*) FROM novelove_posts GROUP BY status ORDER BY count(*) DESC"):
        print(f"  {row[0]}: {row[1]}")

    # 文字化けレコードの内訳
    like_q = f"%{MOJIBAKE}%"
    print(f"\n[文字化け] ステータス別内訳:")
    broken_total = 0
    for row in cur.execute(
        "SELECT status, count(*) FROM novelove_posts "
        "WHERE title LIKE ? OR description LIKE ? "
        "GROUP BY status ORDER BY count(*) DESC",
        (like_q, like_q),
    ):
        print(f"  {row[0]}: {row[1]}")
        broken_total += row[1]
    print(f"  --- 文字化け合計: {broken_total}")

    # 正常レコード
    clean_total = 0
    for row in cur.execute(
        "SELECT status, count(*) FROM novelove_posts "
        "WHERE title NOT LIKE ? AND description NOT LIKE ? "
        "GROUP BY status ORDER BY count(*) DESC",
        (like_q, like_q),
    ):
        clean_total += row[1]
    print(f"\n[正常データ] 合計: {clean_total}")

    # 削除対象（文字化けあり AND published以外）
    delete_count = cur.execute(
        "SELECT count(*) FROM novelove_posts "
        "WHERE (title LIKE ? OR description LIKE ?) AND status != 'published'",
        (like_q, like_q),
    ).fetchone()[0]

    # 再スクレイピング対象（文字化けあり AND published）
    rescrape_count = cur.execute(
        "SELECT count(*) FROM novelove_posts "
        "WHERE (title LIKE ? OR description LIKE ?) AND status = 'published'",
        (like_q, like_q),
    ).fetchone()[0]

    print(f"\n{'='*60}")
    print(f"[今回の操作]")
    print(f"  DELETE対象 (文字化け & 未投稿): {delete_count}件")
    print(f"  RESCRAPE対象 (文字化け & 投稿済): {rescrape_count}件 (※今回は対象外)")
    print(f"  影響なし (正常データ):  {clean_total}件")
    print(f"{'='*60}")

    # サニティチェック
    check = delete_count + rescrape_count + clean_total
    if check != total:
        print(f"\n[SANITY CHECK] NG: {delete_count} + {rescrape_count} + {clean_total} = {check} != {total}")
        print("  安全のため中断します。")
        conn.close()
        return

    print(f"\n[SANITY CHECK] OK: {delete_count} + {rescrape_count} + {clean_total} = {total}")

    # ===== STEP 3: 実行 =====
    if not execute_mode:
        print(f"\n[DRY RUN] 実際の削除は行いません。")
        print(f"  実行するには: python fix_digiket_mojibake.py --execute")
        conn.close()
        return

    print(f"\n[EXECUTE] 削除を開始します...")

    # 削除前の再確認カウント
    pre_count = cur.execute("SELECT count(*) FROM novelove_posts").fetchone()[0]
    print(f"  削除前の全件数: {pre_count}")

    # 削除実行
    cur.execute(
        "DELETE FROM novelove_posts "
        "WHERE (title LIKE ? OR description LIKE ?) AND status != 'published'",
        (like_q, like_q),
    )
    deleted = cur.rowcount
    conn.commit()

    # 削除後の確認
    post_count = cur.execute("SELECT count(*) FROM novelove_posts").fetchone()[0]
    post_published = cur.execute(
        "SELECT count(*) FROM novelove_posts WHERE status = 'published'"
    ).fetchone()[0]

    print(f"  実際に削除された件数: {deleted}")
    print(f"  削除後の全件数: {post_count}")
    print(f"  削除後のpublished件数: {post_published}")

    # 最終サニティチェック
    expected = pre_count - delete_count
    if post_count != expected:
        print(f"\n[FINAL CHECK] NG: 削除後 {post_count} != 期待値 {expected}")
        print("  ロールバックします...")
        conn.close()
        shutil.copy2(backup_name, DB_PATH)
        print(f"  バックアップから復元しました: {backup_name}")
        return

    print(f"\n[FINAL CHECK] OK: {post_count} = {expected}")
    print(f"[DONE] {deleted}件を安全に削除しました。バックアップ: {backup_name}")

    conn.close()


if __name__ == "__main__":
    main()
