#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NOT FOUND 112件をWP-CLIで直接再チェック（bcache完全無視、読み取り専用）
このスクリプトはサーバー上で直接実行する。
"""

import sqlite3
import os
import subprocess
import re
import sys

DB_FILE  = '/home/kusanagi/scripts/novelove_unified.db'
DOC_ROOT = os.environ.get('WP_DOC_ROOT', '/home/kusanagi/myblog/DocumentRoot')

NOT_FOUND_PIDS = [
    "BJ02284791","BJ02330843","BJ02336079","BJ02366510","BJ02435531",
    "BJ02460669","BJ02477995","BJ02478867","BJ02482003","BJ02482004",
    "ITM0328368","ITM0335451","ITM0335531","ITM0335690","ITM0335691",
    "ITM0335692","ITM0335769","RJ01570268","RJ01572143","RJ01574791",
    "RJ01577163","RJ01577624","RJ01583158","RJ01586667","RJ01586854",
    "RJ01587878","RJ01590259","RJ01592684","RJ01594766","RJ01599290",
    "RJ01600955","RJ01602569","RJ01603121","RJ01604252","RJ01604894",
    "RJ01605987","b116apblk00567","b170akoko08001","b222atkmg04115",
    "b330ftksb13126","b330ftksb13127","b403assog41953","b403assog45542",
    "b525atmh16773","b637asyus02116","b865auhdc24401","d_584159",
    "d_739603","d_740994","d_741579","d_743950","d_746121","d_748026",
    "d_748580","d_749522","d_749947","d_750138","d_750819","d_751438",
    "d_751659","d_753001","d_753097","d_753432","d_753499","d_753632",
    "d_753847","d_753873","d_754292","d_754613","d_754615","d_754744",
    "d_755077","digiket-bl-ranking-2026-04-w4","digiket-bl-ranking-2026-04-w5",
    "digiket-bl-ranking-2026-05-w1","digiket-tl-ranking-2026-04-w1",
    "digiket-tl-ranking-2026-04-w2","digiket-tl-ranking-2026-04-w3",
    "digiket-tl-ranking-2026-04-w4","digiket-tl-ranking-2026-04-w5",
    "digiket-tl-ranking-2026-05-w1","dlsite-bl-ranking-2026-04-w1",
    "dlsite-bl-ranking-2026-04-w3","dlsite-bl-ranking-2026-04-w4",
    "dlsite-bl-ranking-2026-05-w1","dlsite-bl-ranking-2026-05-w2",
    "dlsite-tl-ranking-2026-04-w1","dlsite-tl-ranking-2026-04-w4",
    "dlsite-tl-ranking-2026-05-w1","dlsite-tl-ranking-2026-05-w2",
    "dmm-bl-ranking-2026-04-w4","dmm-bl-ranking-2026-04-w5",
    "dmm-bl-ranking-2026-05-w1","dmm-tl-ranking-2026-04-w4",
    "dmm-tl-ranking-2026-04-w5","dmm-tl-ranking-2026-05-w1",
    "fanza-bl-ranking-2026-04-w3","fanza-bl-ranking-2026-04-w4",
    "fanza-bl-ranking-2026-05-w1","fanza-tl-ranking-2026-04-w3",
    "fanza-tl-ranking-2026-04-w4","fanza-tl-ranking-2026-05-w1",
    "k909akrms00659","lovecal-bl-ranking-2026-04-w4","lovecal-bl-ranking-2026-05-w1",
    "lovecal-tl-ranking-2026-04-w4","lovecal-tl-ranking-2026-05-w1",
    "s188aghvv04171","s188aghvv04431","s641aknai19420","s647ailyj03916",
    "s657amslj00013",
]

def wpcli(slug):
    """WP-CLIで slug を検索（直接実行、bcache完全無視）"""
    for sfx in ['', '-2', '-3', '-4']:
        s = slug + sfx
        try:
            result = subprocess.run(
                ['wp', 'post', 'list',
                 f'--name={s}',
                 '--post_status=publish',
                 '--fields=ID,post_name,post_date,guid',
                 '--format=csv',
                 '--allow-root',
                 f'--path={DOC_ROOT}'],
                capture_output=True, text=True, timeout=10
            )
            lines = result.stdout.strip().split('\n')
            for line in lines[1:]:
                parts = line.split(',')
                if len(parts) >= 3 and parts[0].strip().isdigit():
                    return {
                        'id':   int(parts[0].strip()),
                        'slug': parts[1].strip(),
                        'date': parts[2].strip(),
                        'url':  parts[3].strip() if len(parts) > 3 else '',
                        'suffix': sfx,
                    }
        except Exception as e:
            print(f"  [WP-CLI ERROR] {s}: {e}")
    return None

def run():
    conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    db_rows = {}
    for pid in NOT_FOUND_PIDS:
        row = conn.execute(
            "SELECT product_id, wp_post_url FROM novelove_posts WHERE product_id = ?",
            (pid,)
        ).fetchone()
        if row:
            db_rows[pid] = dict(row)
    conn.close()

    print(f"WP-CLI で {len(NOT_FOUND_PIDS)} 件を再チェック（bcache無視）...")
    print("=" * 65)

    found = []
    not_found = []

    for pid in NOT_FOUND_PIDS:
        db_url   = (db_rows.get(pid) or {}).get('wp_post_url') or ''
        url_slug = None
        if db_url:
            m = re.search(r'/([^/]+)/?$', db_url.rstrip('/'))
            url_slug = m.group(1) if m else None

        result = None
        # product_id で試す
        result = wpcli(pid)
        # url_slug が product_id と違う場合は追加で試す
        if not result and url_slug and url_slug.lower() != pid.lower():
            result = wpcli(url_slug)

        if result:
            sfx_note = f" (suffix:{result['suffix']})" if result['suffix'] else ''
            print(f"  [FOUND] {pid}{sfx_note} -> WP ID:{result['id']} slug:{result['slug']} / {result['date']}")
            found.append({'pid': pid, **result})
        else:
            print(f"  [NOT FOUND] {pid}")
            not_found.append(pid)

    print("\n" + "=" * 65)
    print("【WP-CLI 再チェック結果サマリー】")
    print("=" * 65)
    print(f"  発見:     {len(found)} 件")
    print(f"  未発見:   {len(not_found)} 件")

    if not_found:
        print("\n--- 本当に見つからない記事（投稿失敗の可能性） ---")
        for pid in not_found:
            print(f"  {pid}")

    print("\n(変更なし・調査のみ)")

if __name__ == '__main__':
    run()
