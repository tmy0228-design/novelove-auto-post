#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nexus_batch_rewrite.py — 問題記事の一括リライトスクリプト
対象1: WP記事本文に「公式属性タグ」を含む published 記事
対象2: speech-bubble-left の末尾div閉じ漏れ記事（トークン切れ起因）
       - rj01533656 / b163cijt232807
"""
import sys
import os
import time
import sqlite3
import subprocess

sys.path.insert(0, '/home/kusanagi/scripts')
os.chdir('/home/kusanagi/scripts')

from novelove_core import logger, notify_discord

# =============================================
# Step 1: WP DBから対象記事のproduct_id一覧を取得
# =============================================
logger.info("=" * 60)
logger.info("🔄 バッチリライト開始: 公式属性タグ漏れ記事 一括修正")
logger.info("=" * 60)

WP_DB = "/home/kusanagi/myblog/DocumentRoot/wp-config.php"
WP_CLI = "/opt/kusanagi/bin/wp"
WP_ROOT = "/home/kusanagi/myblog/DocumentRoot"

# WP-CLIで記事本文に「公式属性タグ」を含む記事のslug(=product_id)を取得
logger.info("[Step1] WP DBから対象記事を取得中...")
result = subprocess.run(
    [WP_CLI, "db", "query",
     "SELECT post_name FROM wp_posts WHERE post_status='publish' AND post_type='post' AND post_content LIKE '%公式属性タグ%'",
     "--allow-root", f"--path={WP_ROOT}", "--skip-column-names"],
    capture_output=True, text=True, timeout=30
)
wp_slugs = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
logger.info(f"  => WPから {len(wp_slugs)}件 取得")

# 追加対象: find_meta_tag_leak.py で検出されたメタ言語混入記事 (2026-04-25)
# v17.8.7: 以下を除外済み
#   DB未登録 (6件): rj01474169, rj01598833, d_754174, bj02465811, rj01590597, rj01540849
#   status=excluded (2件): rj01602569, rj01600955
EXTRA_IDS = [
    'rj01567065', 'd_757745', 'bj02451294', 'rj01612627', 'b129dbnka20607',
    'd_754694', 'd_758946', 'd_758963', 'rj01613548', 'rj01613718',
    'rj01612122', 'd_754689', 'k909akrms00663', 'rj01529323', 'rj01608029',
    'd_758484', 'rj01613162', 'rj01613089', 'rj01613357', 'rj01613353',
    's298asnph27138', 'rj01613412', 'rj01610669', 'rj01575071', 'k379asmah01732',
    'd_651204', 'd_740478', 'rj01612540', 'rj01612125', 'rj01612465',
    'rj01611667', 'bj02468030', 'rj01567728', 'd_756078', 'd_734654',
    'd_738598', 'rj01608331', 'rj01610835', 'd_621710', 'd_747407',
    'rj01609647', 'd_755048', 'rj01612066', 'rj01610949', 'k924aruuu14165',
    'd_741376', 'd_738849', 'rj01611908', 'd_757208', 'd_736094',
    'd_617199', 'd_740636', 'rj01597464', 'd_757409', 'd_757415',
    'bj02479798', 'rj01593174', 'd_749733', 'rj01603383-2', 'rj01571537-2',
    'rj01607675', 'rj01508889', 'd_755865', 'rj01593406', 'rj01610174',
    'b865auhdc24396-2', 'rj01608975', 'rj01599109', 's298asnph24828',
    'b231aftmj04035-2', 'rj01604337', 'b236afrpt04652-2', 'rj01608938',
    'bj02381029', 'rj01601946', 'rj01595117', 'rj01594800', 'd_741579',
    'rj01606960', 'd_751659', 'd_746121', 'd_755077', 'd_754292',
    'd_753432', 'd_753632', 'rj01588214', 's188aghvv04171', 'd_754615',
    'd_754613', 'd_740994', 'rj01606251',
    'b637asyus02116', 'rj01604337',
    'd_745322', 'k272aksdz01620', 'd_750138', 'd_753001', 'rj01593613',
    'd_753097', 'rj01604880', 'd_748689', 'd_753526',
    'd_751873', 'd_752598', 'd_751887', 'd_749917', 'd_749999',
    'd_750501', 'd_751429', 'rj01604440', 'd_725325', 'rj01600985',
    'rj01603580', 'rj01599141', 'rj01597536', 'rj01563907',
    'rj01572961', 'd_581428', 'd_363539', 'd_715103', 'd_657533', 'd_636815',
]
for eid in EXTRA_IDS:
    if eid not in [s.lower() for s in wp_slugs]:
        wp_slugs.append(eid)
logger.info(f"  => EXTRA_IDS から {len(EXTRA_IDS)}件 追加（重複除外済み）")

total = len(wp_slugs)
logger.info(f"  => リライト対象 合計: {total}件")

# Discordに開始通知
notify_discord(
    f"[バッチリライト開始]\n"
    f"対象: {total}件（公式属性タグ漏れ + div閉じ漏れ + アイコンバグ修正）\n"
    f"処理中は30分〜2時間程度かかります。完了時に通知します。",
    username="Nexusリライト"
)

# =============================================
# Step 2: 1件ずつ run_rewrite を呼び出す
# =============================================
from nexus_rewrite import run_rewrite

success_count = 0
fail_count = 0
skip_count = 0
failed_ids = []

for idx, product_id in enumerate(wp_slugs, 1):
    logger.info("")
    logger.info(f"━━━ [{idx}/{total}件目] {product_id} ━━━")

    try:
        ok = run_rewrite(product_id=product_id, execute=True)
        if ok:
            success_count += 1
            logger.info(f"  ✅ [{idx}/{total}] 成功: {product_id}")
        else:
            fail_count += 1
            failed_ids.append(product_id)
            logger.warning(f"  ❌ [{idx}/{total}] 失敗: {product_id}")
    except Exception as e:
        fail_count += 1
        failed_ids.append(product_id)
        logger.error(f"  ❌ [{idx}/{total}] 例外: {product_id} / {e}")

    # 進捗Discord通知（10件ごと）
    if idx % 10 == 0:
        notify_discord(
            f"🔄 **[バッチリライト進捗]** {idx}/{total}件完了\n"
            f"✅ 成功: {success_count}件 / ❌ 失敗: {fail_count}件",
            username="🔄 Nexusリライト"
        )

    # APIへの負荷軽減: 1件ごとに5秒待機
    if idx < total:
        time.sleep(5)

# =============================================
# Step 3: 完了通知
# =============================================
logger.info("")
logger.info("=" * 60)
logger.info(f"🏁 バッチリライト完了！")
logger.info(f"   合計: {total}件")
logger.info(f"   ✅ 成功: {success_count}件")
logger.info(f"   ❌ 失敗: {fail_count}件")
if failed_ids:
    logger.info(f"   失敗ID: {', '.join(failed_ids)}")
logger.info("=" * 60)

fail_detail = ""
if failed_ids:
    fail_detail = f"\n❌ **失敗したID**:\n" + "\n".join(f"  • {pid}" for pid in failed_ids)

notify_discord(
    f"🏁 **[バッチリライト完了]**\n"
    f"━━━━━━━━━━━━━━━━\n"
    f"📊 **合計**: {total}件\n"
    f"✅ **成功**: {success_count}件\n"
    f"❌ **失敗**: {fail_count}件"
    f"{fail_detail}",
    username="🔄 Nexusリライト"
)
