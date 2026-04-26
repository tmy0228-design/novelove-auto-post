#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_db_wp.py
=============
DB と WordPress の投稿状態を同期するツール。

【機能】
- novelove_posts テーブルで status='published' の全件を取得
- WordPressに対して実際にHTTPアクセスし、記事の生死を確認（200 OK か 404 か）
- WP上に存在しない（削除済み）レコードのstatusを 'excluded' にリセット
- wp_post_url は保持したまま（"どこに投稿してたか"の記録は残す）

【使い方】
  python sync_db_wp.py           # ドライラン（変更なし・確認のみ）
  python sync_db_wp.py --fix     # 実際にDBを修正する
  python sync_db_wp.py --fix --verbose  # 詳細ログ付きで修正

【ダッシュボード連携】
  from sync_db_wp import run_sync
  result = run_sync(dry_run=False)
  # result: {"checked": 100, "ok": 90, "fixed": 10, "errors": 0}
"""

import os
import sys
import sqlite3
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

# --- 環境変数 ---
env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from novelove_core import DB_FILE_UNIFIED

DB_FILES = [DB_FILE_UNIFIED]  # v18.0.0: 統合DB1本に変更
MAX_WORKERS = 5    # 並列数（多すぎるとWPに429 Too Many Requestsを食らうので控えめに）
TIMEOUT = 10       # 1件あたりのHTTPタイムアウト（秒）


def _check_url(row):
    """
    1件のURLに対してHTTPアクセスし、記事が存在するか確認する。
    返り値: (db_file, product_id, wp_post_url, is_alive)
    """
    db_file, product_id, wp_post_url = row
    if not wp_post_url:
        return (db_file, product_id, wp_post_url, False, "url_empty")

    try:
        r = requests.head(wp_post_url, timeout=TIMEOUT, allow_redirects=True)
        # 200番台 → 生存
        if 200 <= r.status_code < 400:
            return (db_file, product_id, wp_post_url, True, f"{r.status_code}")
        # 429 = Too Many Requests → WP側がレート制限しているだけなので「生存」扱い
        elif r.status_code == 429:
            return (db_file, product_id, wp_post_url, True, "429(rate_limited=alive)")
        # 404 = 本当に存在しない
        elif r.status_code == 404:
            return (db_file, product_id, wp_post_url, False, "404")
        else:
            # 500等サーバーエラーは判断不能 → 安全のため「生存」扱い（誤削除を防ぐ）
            return (db_file, product_id, wp_post_url, True, f"{r.status_code}(unknown=alive)")
    except Exception as e:
        # タイムアウト等 → 安全のため「生存」扱い（誤削除を防ぐ）
        return (db_file, product_id, wp_post_url, True, f"error:{e}(alive)")


def run_sync(dry_run=True, verbose=False):
    """
    DB-WP同期のメイン関数。
    dry_run=True の場合は確認のみで変更なし。
    ダッシュボードからはこの関数を直接呼び出す。
    """
    # --- STEP1: DBから全 published レコードを収集 ---
    targets = []
    for db_path in DB_FILES:
        if not os.path.exists(db_path):
            continue
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT product_id, wp_post_url FROM novelove_posts WHERE status='published'"
        ).fetchall()
        conn.close()
        for (product_id, wp_post_url) in rows:
            targets.append((db_path, product_id, wp_post_url))

    total = len(targets)
    print(f"\n📋 チェック対象: {total}件（全DB合計）\n")
    if total == 0:
        print("対象なし。終了します。")
        return {"checked": 0, "ok": 0, "fixed": 0, "errors": 0}

    # --- STEP2: 並列でHTTPチェック ---
    dead_records = []   # (db_file, product_id, wp_post_url, reason)
    ok_count = 0
    err_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_check_url, t): t for t in targets}
        done = 0
        for future in as_completed(futures):
            done += 1
            db_file, product_id, wp_url, is_alive, reason = future.result()

            if is_alive:
                ok_count += 1
                if verbose:
                    print(f"  ✅ OK  [{reason}] {product_id}")
            else:
                dead_records.append((db_file, product_id, wp_url, reason))
                err_count += 1 if "error:" in reason else 0
                print(f"  🚨 DEAD [{reason}] {product_id}  ({wp_url})")

            # 進捗を10件ごとに表示
            if done % 10 == 0 or done == total:
                print(f"  ... {done}/{total} チェック完了")

    # --- STEP3: 結果サマリー ---
    print(f"\n{'='*50}")
    print(f"チェック完了: {total}件")
    print(f"  ✅ 正常 (WPに存在): {ok_count}件")
    print(f"  🚨 消滅 (WPに無い): {len(dead_records)}件")
    print(f"     うちHTTPエラー:  {err_count}件")
    print(f"{'='*50}\n")

    if not dead_records:
        print("✨ 不整合なし！DBとWPは完全に一致しています。")
        return {"checked": total, "ok": ok_count, "fixed": 0, "errors": err_count}

    # --- STEP4: DBを修正（dry_run=False の場合のみ）---
    fixed_count = 0
    if dry_run:
        print("🔍 ドライランモード: 上記のレコードを 'excluded' にする予定ですが、まだ変更していません。")
        print("   実際に修正するには: python sync_db_wp.py --fix")
    else:
        print("🔧 DBの修正を開始します...")
        # DB別にグループ化して一括更新
        from collections import defaultdict
        grouped = defaultdict(list)
        for (db_file, product_id, wp_url, reason) in dead_records:
            grouped[db_file].append(product_id)

        for db_path, product_ids in grouped.items():
            conn = sqlite3.connect(db_path)
            placeholders = ",".join("?" * len(product_ids))
            conn.execute(
                f"UPDATE novelove_posts SET status='excluded' WHERE product_id IN ({placeholders})",
                product_ids
            )
            conn.commit()
            changed = conn.total_changes
            fixed_count += changed
            conn.close()
            print(f"  📝 {os.path.basename(db_path)}: {changed}件を 'excluded' に更新")

        print(f"\n✅ 完了！{fixed_count}件のレコードを 'excluded' に修正しました。")

    return {"checked": total, "ok": ok_count, "fixed": fixed_count, "errors": err_count}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DB-WordPress 投稿状態同期ツール")
    parser.add_argument("--fix", action="store_true", help="実際にDBを修正する（省略時はドライラン）")
    parser.add_argument("--verbose", action="store_true", help="正常なURLも全件表示する")
    args = parser.parse_args()

    result = run_sync(dry_run=not args.fix, verbose=args.verbose)
    print(f"\n最終結果: {result}")
