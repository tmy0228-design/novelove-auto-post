#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_補完スクリプト — 公開済みレコードの欠落情報をWP APIから補完する
=================================================================
【安全設計】
  - デフォルトは DRY-RUN（--execute を付けないと一切DBを変更しない）
  - 既存データは絶対に上書きしない（NULL/空のフィールドのみ補完）
  - 補完前後の値を全件ログファイルに記録
  - wp_post_id で取得したデータは slug を検証してから書き込む

【実行方法】
  # 確認のみ（DBは変更しない）
  python3 /tmp/fill_missing_db.py

  # 本番実行（DBに書き込む）
  python3 /tmp/fill_missing_db.py --execute
"""

import sqlite3
import os
import requests
import re
import time
import sys
from datetime import datetime

DB_FILE      = '/home/kusanagi/scripts/novelove_unified.db'
WP_SITE_URL  = os.environ.get('WP_SITE_URL', 'https://novelove.jp')
WP_USER      = os.environ.get('WP_USER', '')
WP_APP_PASSWORD = os.environ.get('WP_APP_PASSWORD', '')
AUTH         = (WP_USER, WP_APP_PASSWORD)
EXECUTE      = '--execute' in sys.argv
LOG_FILE     = f'/tmp/fill_missing_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt'


def log(msg):
    print(msg)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def wp_get_by_id(post_id):
    """wp_post_id で WP 記事を直接取得"""
    try:
        r = requests.get(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
            auth=AUTH,
            params={"_fields": "id,slug,date,link,status"},
            timeout=10
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log(f"  [API ERROR] ID={post_id}: {e}")
    return None


def wp_get_by_slug(slug):
    """slug で検索し、完全一致フィルタ後に返す（bcache 誤爆対策）"""
    try:
        r = requests.get(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts",
            auth=AUTH,
            params={"slug": slug, "status": "publish",
                    "_fields": "id,slug,date,link,status"},
            timeout=10
        )
        if r.status_code == 200:
            posts = r.json()
            if isinstance(posts, list):
                exact = [p for p in posts
                         if p.get('slug', '').lower() == slug.lower()]
                if exact:
                    return exact[0]
    except Exception as e:
        log(f"  [API ERROR] slug={slug}: {e}")
    return None


def extract_slug_from_url(url):
    if not url:
        return None
    m = re.search(r'/([^/]+)/?$', url.rstrip('/'))
    return m.group(1) if m else None


def db_update(conn, product_id, pub_at, wp_id, wp_url):
    """
    NULL / 空のフィールドのみ補完する。
    既存データは絶対に上書きしない。
    """
    sets  = []
    vals  = []
    if pub_at:
        sets.append("published_at = COALESCE(NULLIF(published_at,''), ?)")
        vals.append(pub_at)
    if wp_id:
        sets.append("wp_post_id = COALESCE(wp_post_id, ?)")
        vals.append(wp_id)
    if wp_url:
        sets.append("wp_post_url = COALESCE(NULLIF(wp_post_url,''), ?)")
        vals.append(wp_url)
    if not sets:
        return False
    vals.append(product_id)
    conn.execute(
        f"UPDATE novelove_posts SET {', '.join(sets)} WHERE product_id = ?",
        vals
    )
    return True


def run():
    mode = "本番実行 [DB書き込みあり]" if EXECUTE else "DRY-RUN [DB変更なし]"
    log("=" * 65)
    log(f"DB補完スクリプト起動 — {mode}")
    log(f"実行日時: {datetime.now().isoformat()}")
    log("=" * 65)

    conn_ro = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
    conn_ro.row_factory = sqlite3.Row

    # ── 対象レコードを取得 ──
    rows = conn_ro.execute("""
        SELECT product_id, site, published_at, wp_post_id, wp_post_url
        FROM novelove_posts
        WHERE status = 'published'
          AND (
              published_at IS NULL OR published_at = ''
              OR wp_post_id IS NULL OR wp_post_id = 0
              OR wp_post_url IS NULL OR wp_post_url = ''
          )
        ORDER BY product_id
    """).fetchall()
    conn_ro.close()

    log(f"\n対象レコード数: {len(rows)} 件\n")

    results = {'updated': [], 'skipped': [], 'not_found': [], 'error': []}

    for row in rows:
        pid    = row['product_id']
        db_id  = row['wp_post_id']
        db_url = row['wp_post_url']
        db_pub = row['published_at']

        missing = []
        if not db_pub: missing.append('published_at')
        if not db_id:  missing.append('wp_post_id')
        if not db_url: missing.append('wp_post_url')

        new_id  = None
        new_url = None
        new_pub = None
        method  = None

        # ── 戦略1: wp_post_id で直接取得 ──
        if db_id:
            d = wp_get_by_id(db_id)
            if d and d.get('status') == 'publish':
                # slug 検証（誤記事上書き防止）
                actual_slug = d.get('slug', '')
                expected_slug = pid
                url_slug = extract_slug_from_url(db_url) if db_url else None
                slug_ok = (actual_slug.lower() == expected_slug.lower() or
                           (url_slug and actual_slug.lower() == url_slug.lower()))
                if slug_ok:
                    new_pub = d.get('date', '').replace('T', ' ')
                    new_url = d.get('link', '')
                    method = f'by_wp_id({db_id})'
                else:
                    log(f"  [SKIP] {pid}: ID={db_id} だが slug 不一致 (実際: {actual_slug})")
                    results['skipped'].append(pid)
                    time.sleep(0.2)
                    continue

        # ── 戦略2: wp_post_url の slug で取得 ──
        if not method and db_url:
            url_slug = extract_slug_from_url(db_url)
            if url_slug:
                d = wp_get_by_slug(url_slug)
                if d and d.get('status') == 'publish':
                    new_id  = d.get('id')
                    new_pub = d.get('date', '').replace('T', ' ')
                    new_url = d.get('link', db_url)
                    method  = f'by_url_slug({url_slug})'

        # ── 戦略3: product_id slug で取得 ──
        if not method:
            d = wp_get_by_slug(pid)
            if d and d.get('status') == 'publish':
                new_id  = d.get('id')
                new_pub = d.get('date', '').replace('T', ' ')
                new_url = d.get('link', '')
                method  = f'by_pid_slug({pid})'

        # ── 戦略4: -2,-3,-4 サフィックス ──
        if not method:
            for suffix in ['-2', '-3', '-4']:
                d = wp_get_by_slug(pid + suffix)
                if d and d.get('status') == 'publish':
                    new_id  = d.get('id')
                    new_pub = d.get('date', '').replace('T', ' ')
                    new_url = d.get('link', '')
                    method  = f'by_suffix({pid + suffix})'
                    break

        # ── 結果処理 ──
        if method:
            log(f"  [FOUND] {pid}")
            log(f"    方法: {method}")
            log(f"    欠落: {'+'.join(missing)}")
            if new_pub and not db_pub:
                log(f"    published_at: NULL -> {new_pub}")
            if new_id and not db_id:
                log(f"    wp_post_id:   NULL -> {new_id}")
            if new_url and not db_url:
                log(f"    wp_post_url:  NULL -> {new_url}")

            if EXECUTE:
                try:
                    conn_rw = sqlite3.connect(DB_FILE, timeout=60,
                                              isolation_level="IMMEDIATE")
                    conn_rw.execute("PRAGMA journal_mode=WAL;")
                    db_update(conn_rw, pid,
                              new_pub if not db_pub else None,
                              new_id  if not db_id  else None,
                              new_url if not db_url else None)
                    conn_rw.commit()
                    conn_rw.close()
                    log(f"    -> DB更新完了")
                    results['updated'].append(pid)
                except Exception as e:
                    log(f"    -> DB更新失敗: {e}")
                    results['error'].append(pid)
            else:
                log(f"    -> [DRY-RUN] 上記を書き込む予定（--execute で実行）")
                results['updated'].append(pid)
        else:
            log(f"  [NOT FOUND] {pid} ({str(row['site']).split(':')[0]}) | 欠落: {'+'.join(missing)}")
            results['not_found'].append(pid)

        time.sleep(0.2)

    # ── サマリー ──
    log("\n" + "=" * 65)
    log(f"【完了サマリー】 — {mode}")
    log("=" * 65)
    if EXECUTE:
        log(f"  DB更新成功:    {len(results['updated'])} 件")
        log(f"  エラー:        {len(results['error'])} 件")
    else:
        log(f"  補完予定:      {len(results['updated'])} 件")
    log(f"  スキップ:      {len(results['skipped'])} 件 (slug不一致)")
    log(f"  WP未発見:      {len(results['not_found'])} 件 (補完不可)")
    log(f"\nログ保存先: {LOG_FILE}")
    if not EXECUTE:
        log("\n本番実行するには --execute を付けてください。")


if __name__ == '__main__':
    run()
