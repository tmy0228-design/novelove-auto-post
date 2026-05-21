#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DB補完スクリプト（WP-CLI版）— 公開済みレコードの欠落情報をWP-CLIから補完
=======================================================================
【安全設計】
  - WP-CLI で直接WP DBに問い合わせ（bcache完全無視）
  - slug の完全一致を確認してから書き込む（誤記事への書き込みを防止）
  - COALESCE により既存データは絶対に上書きしない
  - WP記事自体は一切変更しない（novelove_unified.db のみ書き込む）
  - 全変更をログに記録
"""

import sqlite3
import os
import subprocess
import re
import sys
from datetime import datetime

DB_FILE  = '/home/kusanagi/scripts/novelove_unified.db'
DOC_ROOT = os.environ.get('WP_DOC_ROOT', '/home/kusanagi/myblog/DocumentRoot')
EXECUTE  = '--execute' in sys.argv
LOG_FILE = f'/tmp/fill_db_wpcli_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt'

def log(msg):
    print(msg)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

def wpcli_find(slug):
    """WP-CLI で slug を検索（-2,-3,-4 サフィックスも試す）"""
    for sfx in ['', '-2', '-3', '-4']:
        s = slug + sfx
        try:
            r = subprocess.run(
                ['wp', 'post', 'list',
                 f'--name={s}',
                 '--post_status=publish',
                 '--fields=ID,post_name,post_date,guid',
                 '--format=csv',
                 '--allow-root',
                 f'--path={DOC_ROOT}'],
                capture_output=True, text=True, timeout=10
            )
            for line in r.stdout.strip().split('\n')[1:]:
                parts = line.split(',')
                if len(parts) >= 3 and parts[0].strip().isdigit():
                    return {
                        'id':     int(parts[0].strip()),
                        'slug':   parts[1].strip(),
                        'date':   parts[2].strip(),
                        'url':    parts[3].strip() if len(parts) > 3 else '',
                        'suffix': sfx,
                    }
        except Exception as e:
            log(f"  [WP-CLI ERROR] slug={s}: {e}")
    return None

def extract_slug_from_url(url):
    if not url: return None
    m = re.search(r'/([^/]+)/?$', url.rstrip('/'))
    return m.group(1) if m else None

def db_fill(conn, product_id, pub_at, wp_id, wp_url):
    """NULL/空のフィールドのみ補完する（既存データは絶対に上書きしない）"""
    sets, vals = [], []
    if pub_at:
        sets.append("published_at = COALESCE(NULLIF(TRIM(published_at),''), ?)")
        vals.append(pub_at)
    if wp_id:
        sets.append("wp_post_id = COALESCE(NULLIF(wp_post_id, 0), ?)")
        vals.append(wp_id)
    if wp_url:
        sets.append("wp_post_url = COALESCE(NULLIF(TRIM(wp_post_url),''), ?)")
        vals.append(wp_url)
    if not sets:
        return False
    vals.append(product_id)
    conn.execute(f"UPDATE novelove_posts SET {', '.join(sets)} WHERE product_id = ?", vals)
    return True

def run():
    mode = "本番実行 [DB書き込みあり]" if EXECUTE else "DRY-RUN [DB変更なし]"
    log("=" * 65)
    log(f"DB補完スクリプト（WP-CLI版）— {mode}")
    log(f"実行日時: {datetime.now().isoformat()}")
    log("=" * 65)

    # 欠落のある全公開済みレコードを取得
    conn_ro = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
    conn_ro.row_factory = sqlite3.Row
    rows = conn_ro.execute("""
        SELECT product_id, published_at, wp_post_id, wp_post_url
        FROM novelove_posts
        WHERE status = 'published'
          AND (published_at IS NULL OR TRIM(published_at) = ''
               OR wp_post_id IS NULL OR wp_post_id = 0
               OR wp_post_url IS NULL OR TRIM(wp_post_url) = '')
        ORDER BY product_id
    """).fetchall()
    conn_ro.close()

    log(f"\n対象レコード: {len(rows)} 件\n")

    updated, skipped, not_found, errors = [], [], [], []

    for row in rows:
        pid    = row['product_id']
        db_id  = row['wp_post_id']
        db_url = row['wp_post_url'] or ''
        db_pub = row['published_at'] or ''

        missing = []
        if not db_pub: missing.append('published_at')
        if not db_id:  missing.append('wp_post_id')
        if not db_url: missing.append('wp_post_url')

        # 検索するslugの候補（product_id + URLからのslug）
        url_slug = extract_slug_from_url(db_url)
        slugs    = list(dict.fromkeys([pid] + ([url_slug] if url_slug and url_slug != pid else [])))

        result = None
        for slug in slugs:
            result = wpcli_find(slug)
            if result:
                break

        if not result:
            log(f"  [NOT FOUND] {pid} | 欠落: {'+'.join(missing)}")
            not_found.append(pid)
            continue

        # WP URLを正規化して生成
        wp_url_to_fill = result['url'] or f"https://novelove.jp/{result['slug']}/"
        new_pub  = result['date'] if not db_pub else None
        new_id   = result['id']   if not db_id  else None
        new_url  = wp_url_to_fill if not db_url  else None

        # 補完する値がひとつもない場合（すでに全部揃っている）
        if new_pub is None and new_id is None and new_url is None:
            skipped.append(pid)
            continue

        sfx_note = f" (suffix:{result['suffix']})" if result['suffix'] else ''
        log(f"  [FILL] {pid}{sfx_note}")
        log(f"    WP slug: {result['slug']} / WP ID: {result['id']}")
        if new_pub: log(f"    published_at: '' -> {new_pub}")
        if new_id:  log(f"    wp_post_id:   '' -> {new_id}")
        if new_url: log(f"    wp_post_url:  '' -> {new_url}")

        if EXECUTE:
            try:
                conn_rw = sqlite3.connect(DB_FILE, timeout=60, isolation_level="IMMEDIATE")
                conn_rw.execute("PRAGMA journal_mode=WAL;")
                db_fill(conn_rw, pid, new_pub, new_id, new_url)
                conn_rw.commit()
                conn_rw.close()
                log(f"    -> 書き込み完了")
                updated.append(pid)
            except Exception as e:
                log(f"    -> 書き込み失敗: {e}")
                errors.append(pid)
        else:
            log(f"    -> [DRY-RUN] --execute で実行")
            updated.append(pid)

    log("\n" + "=" * 65)
    log(f"【完了サマリー】— {mode}")
    log("=" * 65)
    log(f"  {'DB更新成功' if EXECUTE else '補完予定'}: {len(updated)} 件")
    log(f"  スキップ（補完不要）: {len(skipped)} 件")
    log(f"  WP未発見:            {len(not_found)} 件")
    if EXECUTE:
        log(f"  エラー:              {len(errors)} 件")
    log(f"\nログ: {LOG_FILE}")
    if not EXECUTE:
        log("\n本番実行: --execute を追加して実行してください")

if __name__ == '__main__':
    run()
