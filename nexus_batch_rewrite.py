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
WP_CLI = "/usr/local/bin/wp"
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

# 追加対象：以下はDBスキャンで確認された個別修正対象
# - rj01567065  : アイコン404バグ修正対象（旧来からの追加）
# - rj01533656  : speech-bubble末尾div欠落（トークン切れ）
# - b163cijt232807: 同上
EXTRA_IDS = ["rj01567065", "rj01533656", "b163cijt232807"]
for eid in EXTRA_IDS:
    if eid not in [s.lower() for s in wp_slugs]:
        wp_slugs.append(eid)
        logger.info(f"  => {eid} を追加（個別修正対象）")

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
