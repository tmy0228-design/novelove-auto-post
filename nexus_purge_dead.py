#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
nexus_purge_dead.py — 死に記事自動パージ (v18.6.0)
==========================================================
【役割】
  GSCデータに基づき、検索エンジンから完全に無視されている
  「死に記事」を自動検出し、WordPressから完全削除する。
  ただし、過去にクリック実績のある「殿堂入り記事」は
  永久保護され、絶対に削除されない。

【削除ルール】
  ルールA: 公開45日以上 ＆ 未インデックス(gsc_indexed=0)
           → Googleに完全に見捨てられた記事
  ルールB: 公開60日以上 ＆ 表示5回以下 ＆ クリック0
           → 検索結果にほぼ出ず、誰にもクリックされない記事
  共通除外: is_protected=1 の記事は絶対に削除しない

【起動方法】
  python nexus_purge_dead.py              # 本番実行
  python nexus_purge_dead.py --dry-run    # 審査のみ（削除しない）

【Cron登録（推奨: GSCバッチの1時間後）】
  30 4 * * * /opt/kusanagi/bin/python3 /home/kusanagi/scripts/nexus_purge_dead.py >> /home/kusanagi/scripts/nexus_purge_dead.log 2>&1
==========================================================
"""

import os
import sqlite3
import argparse
import requests
from datetime import datetime

from dotenv import load_dotenv
env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()

from novelove_core import (
    logger, DB_FILE_UNIFIED, db_connect, notify_discord,
    WP_SITE_URL, WP_USER, WP_APP_PASSWORD,
)

# === パージ閾値 ===
RULE_A_DAYS = 45           # 未インデックスの猶予日数
RULE_B_DAYS = 60           # 低トラフィックの猶予日数
RULE_B_MAX_IMPRESSIONS = 5 # この表示回数以下を「低トラフィック」とみなす
RULE_B_MAX_CLICKS = 0      # クリック数がこれ以下で削除対象


# =====================================================================
# WordPress 記事削除（nexus_purge.py から移植・改良）
# =====================================================================
def _delete_wp_post(product_id: str, wp_post_id: int = None) -> bool:
    """
    WordPressの記事を削除（ゴミ箱送り）する。
    wp_post_id がDBにあればそれを使い、なければslug検索する。
    """
    auth = (WP_USER, WP_APP_PASSWORD)
    try:
        # wp_post_id が分かっている場合は直接削除
        if wp_post_id:
            del_req = requests.delete(
                f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}",
                auth=auth, timeout=20
            )
            if del_req.status_code in (200, 201):
                logger.info(f"  → 🗑️ WP記事を削除（ゴミ箱送り）完了: wp_post_id={wp_post_id}")
                return True
            else:
                logger.error(f"  → WP記事削除エラー(ID指定): {del_req.status_code}")
                # フォールバック: slug検索で再試行
        
        # slug(product_id)から検索して削除
        search_req = requests.get(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts",
            params={"slug": product_id, "_fields": "id,title"},
            auth=auth, timeout=15
        )
        if search_req.status_code != 200:
            logger.warning(f"  → WP検索エラー: {search_req.status_code}")
            return False

        posts = search_req.json()
        if not posts:
            logger.info(f"  → WP上に記事なし (slug={product_id})。DB側のみ更新します。")
            return True  # 既に無いなら成功扱い

        found_id = posts[0]['id']
        found_title = posts[0]['title']['rendered']
        del_req = requests.delete(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{found_id}",
            auth=auth, timeout=20
        )
        if del_req.status_code in (200, 201):
            logger.info(f"  → 🗑️ WP記事を削除（ゴミ箱送り）完了: ID={found_id} ({found_title[:30]})")
            return True
        else:
            logger.error(f"  → WP記事削除エラー: {del_req.status_code}")
            return False

    except Exception as e:
        logger.error(f"  → WP通信エラー: {e}")
        return False


# =====================================================================
# メイン処理
# =====================================================================
def run_purge_dead(dry_run=False):
    logger.info("=" * 60)
    logger.info("⚡ 死に記事自動パージ (nexus_purge_dead) 開始")
    if dry_run:
        logger.info("※ DRY-RUN モード: 実際の削除・DB更新は行いません")
    logger.info("=" * 60)

    conn = db_connect(DB_FILE_UNIFIED)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # --- カラムの存在確認（初回実行時の安全装置）---
    try:
        c.execute("SELECT is_protected FROM novelove_posts LIMIT 1")
    except sqlite3.OperationalError:
        logger.info("  [DB] is_protected カラムが未作成。init_db を実行します。")
        conn.close()
        from novelove_core import init_db
        init_db()
        conn = db_connect(DB_FILE_UNIFIED)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # === ルールA: 公開45日以上 ＆ 未インデックス ===
    rule_a_rows = c.execute("""
        SELECT product_id, title, wp_post_id, wp_post_url, published_at,
               gsc_indexed, gsc_impressions, gsc_clicks
        FROM novelove_posts
        WHERE status = 'published'
          AND post_type = 'regular'
          AND is_protected = 0
          AND gsc_last_checked IS NOT NULL
          AND gsc_last_checked >= date('now', '-7 days')
          AND gsc_indexed = 0
          AND published_at <= date('now', ? || ' days')
    """, (f"-{RULE_A_DAYS}",)).fetchall()

    # === ルールB: 公開60日以上 ＆ 表示5回以下 ＆ クリック0 ===
    rule_b_rows = c.execute("""
        SELECT product_id, title, wp_post_id, wp_post_url, published_at,
               gsc_indexed, gsc_impressions, gsc_clicks
        FROM novelove_posts
        WHERE status = 'published'
          AND post_type = 'regular'
          AND is_protected = 0
          AND gsc_last_checked IS NOT NULL
          AND gsc_last_checked >= date('now', '-7 days')
          AND gsc_impressions <= ?
          AND gsc_clicks <= ?
          AND published_at <= date('now', ? || ' days')
    """, (RULE_B_MAX_IMPRESSIONS, RULE_B_MAX_CLICKS, f"-{RULE_B_DAYS}")).fetchall()

    # ルールBからルールAの重複を除外
    rule_a_pids = {r['product_id'] for r in rule_a_rows}
    rule_b_unique = [r for r in rule_b_rows if r['product_id'] not in rule_a_pids]

    logger.info(f"  [ルールA] 未インデックス({RULE_A_DAYS}日超): {len(rule_a_rows)}件")
    logger.info(f"  [ルールB] 低トラフィック({RULE_B_DAYS}日超): {len(rule_b_unique)}件")
    logger.info(f"  [合計] 削除対象: {len(rule_a_rows) + len(rule_b_unique)}件")

    # === 事前通知と削除上限の設定 (v21.3.0) ===
    MAX_PURGE_PER_RUN = 30
    all_targets = list(rule_a_rows) + rule_b_unique

    if all_targets:
        preview_lines = []
        for r in all_targets[:MAX_PURGE_PER_RUN]:
            preview_lines.append(f"・{r['title'][:25]}... (imp:{r['gsc_impressions'] or 0}/clk:{r['gsc_clicks'] or 0})")
        
        mode_label = "🧪 DRY-RUN" if dry_run else "⚡ 本番実行"
        preview_text = (
            f"⚡ **[死に記事パージ予定]** ({mode_label})\n"
            f"本日パージ対象: {len(all_targets)}件 (うち最大 {MAX_PURGE_PER_RUN}件を処理します)\n"
            + "\n".join(preview_lines[:15])
        )
        if len(preview_lines) > 15:
            preview_text += "\n..."
        notify_discord(preview_text, username="⚡ 死に記事パージ (事前通知)")

    purged_count = 0
    failed_count = 0

    for row in all_targets:
        if purged_count >= MAX_PURGE_PER_RUN:
            logger.warning(f"⚠️ 1回あたりのパージ上限（{MAX_PURGE_PER_RUN}件）に達したため、残りは次回実行に持ち越します。")
            break

        pid = row['product_id']
        title = (row['title'] or '')[:35]
        rule = "A:未インデックス" if pid in rule_a_pids else "B:低トラフィック"
        imp = row['gsc_impressions'] or 0
        clicks = row['gsc_clicks'] or 0

        logger.info(f"  [{rule}] {pid} — {title}... (表示:{imp}/クリック:{clicks})")

        if dry_run:
            logger.info(f"    → [DRY-RUN] スキップ")
            purged_count += 1
            continue

        # WordPress から削除
        wp_id = row['wp_post_id']
        if _delete_wp_post(pid, wp_id):
            # DB を deleted に更新
            c.execute("""
                UPDATE novelove_posts
                SET status = 'deleted',
                    last_error = ?,
                    wp_post_url = '',
                    wp_post_id = NULL
                WHERE product_id = ?
            """, (f"purged_gsc_dead:{rule} ({now_str})", pid))
            conn.commit()
            purged_count += 1
        else:
            failed_count += 1

    conn.close()

    # === Discord 通知 ===
    mode_label = "🧪 DRY-RUN" if dry_run else "⚡ 本番実行"
    summary = (
        f"⚡ **[死に記事パージ完了]** ({mode_label})\n"
        f"┣ ルールA (未インデックス {RULE_A_DAYS}日超): {len(rule_a_rows)}件\n"
        f"┣ ルールB (低トラフィック {RULE_B_DAYS}日超): {len(rule_b_unique)}件\n"
        f"┣ 削除成功: {purged_count}件\n"
        f"┗ 削除失敗: {failed_count}件"
    )
    notify_discord(summary, username="⚡ 死に記事パージ")

    logger.info("=" * 60)
    logger.info(f"🏁 死に記事パージ完了 — 削除: {purged_count}件 / 失敗: {failed_count}件")
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="死に記事自動パージ")
    parser.add_argument("--dry-run", action="store_true",
                        help="審査のみ行い、実際の削除・DB更新を行わない")
    args = parser.parse_args()
    run_purge_dead(dry_run=args.dry_run)
