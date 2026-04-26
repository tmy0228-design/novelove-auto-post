#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_migrate_db.py  —  v18.0.0 DB統合マイグレーション
====================================================
novelove.db / novelove_dlsite.db / novelove_digiket.db
  → novelove_unified.db へデータを集約する。

【実行手順】
  1. バックアップ確認 (git commit または手動コピー)
  2. python _migrate_db.py --dry-run   ← 件数照合のみ（変更なし）
  3. python _migrate_db.py             ← 本番実行
  4. 件数・インデックスを確認後、旧DBをアーカイブ

【ロールバック手順】
  git checkout -- .            # コードを元に戻す
  rm novelove_unified.db       # 統合DBを削除
  # バックアップから旧3DBを復元
"""

import os
import sys
import sqlite3
import argparse
import shutil
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

OLD_DBS = [
    (os.path.join(SCRIPT_DIR, "novelove.db"),         "fanza"),
    (os.path.join(SCRIPT_DIR, "novelove_dlsite.db"),  "dlsite"),
    (os.path.join(SCRIPT_DIR, "novelove_digiket.db"), "digiket"),
]

NEW_DB = os.path.join(SCRIPT_DIR, "novelove_unified.db")

SOURCE_DB_FANZA_DETECT = {
    # site値の一部でグループを補正（旧FANZA DBに複数サイトが混在するため）
    "DLsite":  "dlsite",
    "DigiKet": "digiket",
}


def detect_source_db(site_val: str, default_source: str) -> str:
    """site カラムの値から正確な source_db グループを返す"""
    s = str(site_val or "")
    for keyword, group in SOURCE_DB_FANZA_DETECT.items():
        if keyword in s:
            return group
    return default_source


def backup_old_dbs():
    """旧DBを backup/ ディレクトリにコピーする"""
    backup_dir = os.path.join(SCRIPT_DIR, "backup", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(backup_dir, exist_ok=True)
    for db_path, _ in OLD_DBS:
        if os.path.exists(db_path):
            dest = os.path.join(backup_dir, os.path.basename(db_path))
            shutil.copy2(db_path, dest)
            print(f"  [BACKUP] {os.path.basename(db_path)} → {dest}")
    return backup_dir


def get_count(db_path: str) -> int:
    if not os.path.exists(db_path):
        return 0
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM novelove_posts").fetchone()[0]
    conn.close()
    return n


def init_unified_db(conn: sqlite3.Connection):
    """統合DBのスキーマ初期化（init_db相当）"""
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("""CREATE TABLE IF NOT EXISTS novelove_posts (
        product_id    TEXT PRIMARY KEY,
        title         TEXT,
        author        TEXT DEFAULT '',
        genre         TEXT,
        site          TEXT DEFAULT 'FANZA',
        status        TEXT DEFAULT 'excluded',
        release_date  TEXT DEFAULT '',
        description   TEXT DEFAULT '',
        affiliate_url TEXT DEFAULT '',
        image_url     TEXT DEFAULT '',
        product_url   TEXT DEFAULT '',
        wp_post_url   TEXT DEFAULT '',
        wp_post_id    INTEGER DEFAULT NULL,
        last_error    TEXT DEFAULT '',
        inserted_at   TIMESTAMP DEFAULT (datetime('now', 'localtime')),
        published_at  TIMESTAMP,
        post_type     TEXT DEFAULT 'regular',
        desc_score    INTEGER DEFAULT 0,
        ai_tags       TEXT DEFAULT '',
        reviewer      TEXT DEFAULT '',
        wp_tags       TEXT DEFAULT ''
    )""")
    for col, definition in [
        ("last_error",        "TEXT DEFAULT ''"),
        ("desc_score",        "INTEGER DEFAULT 0"),
        ("post_type",         "TEXT DEFAULT 'regular'"),
        ("site",              "TEXT DEFAULT ''"),
        ("ai_tags",           "TEXT DEFAULT ''"),
        ("reviewer",          "TEXT DEFAULT ''"),
        ("sale_discount_rate","INTEGER DEFAULT 0"),
        ("last_revived_at",   "TIMESTAMP DEFAULT NULL"),
        ("revive_score",      "INTEGER DEFAULT 0"),
        ("original_tags",     "TEXT DEFAULT ''"),
        ("is_exclusive",      "INTEGER DEFAULT 0"),
        ("wp_tags",           "TEXT DEFAULT ''"),
        ("rewrite_count",     "INTEGER DEFAULT 0"),
        ("is_desc_updated",   "INTEGER DEFAULT 0"),
        ("prev_description",  "TEXT DEFAULT ''"),
        ("gsc_indexed",       "INTEGER DEFAULT 0"),
        ("gsc_impressions",   "INTEGER DEFAULT 0"),
        ("gsc_clicks",        "INTEGER DEFAULT 0"),
        ("gsc_last_checked",  "TIMESTAMP DEFAULT NULL"),
        ("last_rewritten_at", "TIMESTAMP DEFAULT NULL"),
        ("wp_post_id",        "INTEGER DEFAULT NULL"),
        ("article_pattern",   "TEXT DEFAULT ''"),
        ("source_db",         "TEXT DEFAULT ''"),  # v18.0.0
    ]:
        try:
            c.execute(f"ALTER TABLE novelove_posts ADD COLUMN {col} {definition}")
        except Exception:
            pass
    c.execute("CREATE INDEX IF NOT EXISTS idx_status_source ON novelove_posts (status, source_db);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_status_genre  ON novelove_posts (status, genre);")
    conn.commit()


def run_migration(dry_run: bool = False):
    print("\n" + "="*60)
    print("  Novelove DB 統合マイグレーション v18.0.0")
    print("="*60)

    # --- 事前カウント ---
    old_totals = {}
    for db_path, label in OLD_DBS:
        n = get_count(db_path)
        old_totals[label] = n
        print(f"  旧DB [{label:8}]: {n:6,d} 件  ({db_path})")

    total_src = sum(old_totals.values())
    print(f"  旧DB 合計        : {total_src:6,d} 件")
    print()

    if dry_run:
        print("  [DRY-RUN] 件数確認のみ。終了します。")
        print("  本番実行: python _migrate_db.py")
        return

    # --- バックアップ ---
    print("  バックアップ中...")
    backup_dir = backup_old_dbs()
    print(f"  → {backup_dir} に保存しました\n")

    # --- 統合DB初期化 ---
    print(f"  統合DB作成: {NEW_DB}")
    new_conn = sqlite3.connect(NEW_DB)
    init_unified_db(new_conn)

    # --- 旧DBをATTACHして INSERT OR IGNORE ---
    inserted_totals = {}
    for db_path, default_source in OLD_DBS:
        if not os.path.exists(db_path):
            print(f"  [SKIP] {db_path} が存在しません")
            inserted_totals[default_source] = 0
            continue

        print(f"  [{default_source}] コピー中...")

        # 旧DBのカラム一覧を確認
        old_conn = sqlite3.connect(db_path)
        old_cols = [r[1] for r in old_conn.execute("PRAGMA table_info(novelove_posts)").fetchall()]
        old_conn.close()

        # 共通カラムを計算（source_db は特別扱いで除外）
        new_conn_tmp = sqlite3.connect(NEW_DB)
        new_cols = [r[1] for r in new_conn_tmp.execute("PRAGMA table_info(novelove_posts)").fetchall()]
        new_conn_tmp.close()

        common_cols = [c for c in new_cols if c in old_cols and c != "source_db"]

        new_conn.execute(f"ATTACH DATABASE '{db_path}' AS src")

        # INSERT OR IGNORE: product_idが衝突した場合は既存を優先
        col_list = ", ".join(common_cols)
        count_before = new_conn.execute("SELECT COUNT(*) FROM novelove_posts").fetchone()[0]
        new_conn.execute(f"""
            INSERT OR IGNORE INTO novelove_posts ({col_list}, source_db)
            SELECT {col_list}, '' FROM src.novelove_posts
        """)
        new_conn.commit()
        count_after = new_conn.execute("SELECT COUNT(*) FROM novelove_posts").fetchone()[0]
        insert_count = count_after - count_before

        # source_db カラムを site値から補正（新規INSERTされた source_db='' のものだけ対象）
        rows = new_conn.execute("SELECT product_id, site FROM novelove_posts WHERE source_db=''").fetchall()
        for pid, site_val in rows:
            sdb = detect_source_db(site_val, default_source)
            new_conn.execute("UPDATE novelove_posts SET source_db=? WHERE product_id=?", (sdb, pid))

        new_conn.commit()
        new_conn.execute("DETACH DATABASE src")

        inserted_totals[default_source] = insert_count
        skipped = old_totals[default_source] - insert_count
        print(f"  [{default_source}] → INSERT {insert_count:,d} 件 / 重複スキップ {skipped:,d} 件")


    # --- 検証 ---
    total_new = get_count(NEW_DB)
    print()
    print("  ===== マイグレーション結果 =====")
    print(f"  旧DB合計:  {total_src:6,d} 件")
    print(f"  統合DB:    {total_new:6,d} 件")
    if total_new < total_src:
        diff = total_src - total_new
        print(f"  ⚠️  差分 {diff} 件は product_id の重複（INSERT OR IGNORE により除外）")
    else:
        print("  ✅ 件数一致")

    # source_db 別カウント
    print()
    for sdb in ["fanza", "dlsite", "digiket"]:
        n = new_conn.execute(f"SELECT COUNT(*) FROM novelove_posts WHERE source_db='{sdb}'").fetchone()[0]
        print(f"  source_db={sdb:8}: {n:6,d} 件")

    new_conn.close()
    print()
    print("  ✅ 完了！次のステップ: サーバーへデプロイ後、旧DBをアーカイブしてください。")
    print("="*60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Novelove DB統合マイグレーション v18.0.0")
    parser.add_argument("--dry-run", action="store_true", help="件数確認のみ（変更なし）")
    args = parser.parse_args()
    run_migration(dry_run=args.dry_run)
