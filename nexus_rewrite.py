#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
nexus_rewrite.py — Novelove リライトエンジン v1.0.0
==========================================================
【概要】
  公開済み記事を、指定したライター・感情モードで AI 再執筆し、
  WordPress の記事本文・SEO・タグを安全に上書き更新する CLI ツール。

【安全設計】
  1. --dry-run がデフォルト。--execute を明示しない限り WP を変更しない
  2. セール/売れ筋タグ（sale, best-seller）を保護リストに退避し、リライト後に復元
  3. .rewrite.lock ファイルで二重起動を防止
  4. product_id の存在確認・status='published' チェック
  5. スラグ・カテゴリは一切変更しない（SEO パワー保持）

【使い方】
  # 内容確認のみ（WP変更なし）
  python nexus_rewrite.py --product-id RJ01570022

  # ライター・感情モード指定でdry-run
  python nexus_rewrite.py --product-id RJ01570022 --reviewer shion --mood "布教欲が強い"

  # 本番実行（WP実際に書き換える）
  python nexus_rewrite.py --product-id RJ01570022 --reviewer shion --execute
==========================================================
"""

import os
import sys
import argparse
import sqlite3
import requests
import subprocess
from datetime import datetime
from dotenv import load_dotenv

# --- 環境変数の読み込み ---
env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()

# --- 共通モジュール ---
from novelove_core import (
    logger,
    DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET,
    db_connect, notify_discord, get_db_path,
    WP_SITE_URL, SCRIPT_DIR,
)
# auto_post.py から執筆エンジンのみを借用
from auto_post import generate_article, get_or_create_term

# === 環境変数 ===
WP_USER         = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")

# === 定数 ===
LOCK_FILE         = os.path.join(SCRIPT_DIR, ".rewrite.lock")
SALE_TAG_SLUG     = "sale"
BESTSELLER_SLUG   = "best-seller"
# リライト時に絶対削除してはいけないタグのslug一覧
PROTECTED_SLUGS   = {SALE_TAG_SLUG, BESTSELLER_SLUG}
# タグ名フィルタ（post_to_wordpress と同一ルール）
EXCLUDE_TAG_NAMES = {
    "BL", "TL", "コミック", "小説", "漫画",
    "BLコミック", "TLコミック", "BL同人", "TL同人",
    "商業BL", "同人BL", "商業TL", "同人TL",
    "商業BL小説", "商業TL小説",
}
# サイト名の正規化マップ（post_to_wordpress と同一）
NORMALIZED_LABELS = {
    "DMM.com": "DMM",
    "FANZA":   "FANZA",
    "DLsite":  "DLsite",
    "DigiKet": "DigiKet",
}


# =====================================================================
# 1. ロック管理
# =====================================================================
def _acquire_lock():
    """ロックファイルを取得。既にロック中なら False を返す。"""
    if os.path.exists(LOCK_FILE):
        logger.warning(f"⚠️ リライトロックファイルが存在します: {LOCK_FILE}")
        logger.warning("  別のリライトプロセスが実行中か、前回正常終了しなかった可能性があります。")
        logger.warning("  問題なければ手動でファイルを削除してください。")
        return False
    try:
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()}\n")
        return True
    except Exception as e:
        logger.error(f"ロックファイル作成失敗: {e}")
        return False


def _release_lock():
    """ロックファイルを解放。"""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception as e:
        logger.warning(f"ロックファイル解放失敗: {e}")


# =====================================================================
# 2. DB から対象記事を取得
# =====================================================================
def _get_published_row(product_id):
    """
    全3DBを横断して product_id を検索し、status='published' のレコードを返す。
    戻り値: (row, db_path) または (None, None)
    """
    for db_path in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
        if not os.path.exists(db_path):
            continue
        try:
            conn = db_connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT product_id, title, author, genre, site, status,
                          description, affiliate_url, image_url, wp_post_url,
                          ai_tags, desc_score, original_tags, is_exclusive,
                          release_date, reviewer, rewrite_count, wp_tags
                   FROM novelove_posts
                   WHERE product_id = ?""",
                (product_id,)
            ).fetchone()
            conn.close()
            if row:
                return row, db_path
        except Exception as e:
            logger.warning(f"  [DB] 読み込みエラー ({db_path}): {e}")
    return None, None


# =====================================================================
# 3. WordPress ヘルパー
# =====================================================================
def _wp_auth():
    return (WP_USER, WP_APP_PASSWORD)


def _wp_get_post_id_and_tags(slug):
    """
    スラグ（= product_id）から WP 記事の ID と現在のタグ ID リストを取得する。
    nexus_revive.py の _wp_search_post_by_slug と同一のロジック。
    戻り値: (wp_post_id: int|None, tag_ids: list[int])
    """
    auth = _wp_auth()
    try:
        r = requests.get(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts",
            auth=auth,
            params={"slug": slug, "status": "publish", "_fields": "id,tags"},
            timeout=15,
        )
        posts = r.json()
        if isinstance(posts, list) and posts:
            return posts[0]["id"], posts[0].get("tags", [])
    except Exception as e:
        logger.warning(f"  [WP] 記事検索エラー (slug={slug}): {e}")
    return None, []


def _wp_get_protected_tag_ids(current_tag_ids):
    """
    現在の記事のタグID一覧から、保護すべきタグ（sale, best-seller）のIDを抽出する。
    戻り値: set of int（保護タグのIDのみ）
    """
    auth = _wp_auth()
    protected = set()
    if not current_tag_ids:
        return protected
    try:
        # WP REST API でタグIDリストをslugに変換して確認
        r = requests.get(
            f"{WP_SITE_URL}/wp-json/wp/v2/tags",
            auth=auth,
            params={"include": ",".join(str(i) for i in current_tag_ids), "per_page": 100},
            timeout=15,
        )
        tags = r.json()
        if isinstance(tags, list):
            for tag in tags:
                if tag.get("slug") in PROTECTED_SLUGS:
                    protected.add(tag["id"])
    except Exception as e:
        logger.warning(f"  [WP] 保護タグ取得エラー: {e}")
    return protected


def _build_new_tag_ids(ai_tags, site_label, reviewer_name, is_ranking, protected_ids):
    """
    新しいタグ名リストを構築し、WP上のタグID（なければ作成）に変換する。
    post_to_wordpress() の L730-L792 と同一ルールで構築。
    保護タグ（セール/売れ筋）は最後にマージして返す。

    戻り値: list[int] （最終的にWPへ送信するタグIDリスト）
    """
    # --- (1) タグ名リストの構築 ---
    tag_names = []

    # サイト名タグ
    site_name = NORMALIZED_LABELS.get(site_label, site_label)
    if site_name and site_name not in tag_names:
        tag_names.append(site_name)

    # AI 生成タグ
    for t in (ai_tags or []):
        if t and t not in tag_names:
            tag_names.append(t)

    # ライター名タグ
    if reviewer_name and reviewer_name not in tag_names:
        tag_names.append(reviewer_name)

    # ランキング記事特例（サイト名とライター名のみに絞る）
    if is_ranking:
        allowed = []
        if site_name and site_name in tag_names:
            allowed.append(site_name)
        if reviewer_name and reviewer_name in tag_names:
            allowed.append(reviewer_name)
        tag_names = allowed

    # 不要タグ除外フィルタ（post_to_wordpress と同一）
    tag_names = [t for t in tag_names if t not in EXCLUDE_TAG_NAMES]

    # --- (2) タグ名 → WP タグ ID に変換（なければ作成） ---
    tag_ids = [tid for tid in [get_or_create_term(name, "tags") for name in tag_names] if tid]

    # --- (3) 保護タグをマージ（上書き禁止のセール/売れ筋タグを復元） ---
    for pid in protected_ids:
        if pid not in tag_ids:
            tag_ids.append(pid)

    return tag_ids


def _wp_update_post(wp_post_id, content, wp_title, excerpt):
    """
    WP REST API で記事本文・タイトル・抜粋を上書き更新する。
    スラグ・カテゴリは送信しない（変更しない）。
    """
    auth = _wp_auth()
    payload = {
        "title":   wp_title,
        "content": content,
        "excerpt": excerpt,
    }
    try:
        r = requests.post(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}",
            auth=auth, json=payload, timeout=40,
        )
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"  [WP] 記事更新エラー: {e}")
        return False


def _wp_update_tags(wp_post_id, tag_ids):
    """WP REST API でタグのみを上書き更新する。"""
    auth = _wp_auth()
    try:
        r = requests.post(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}",
            auth=auth, json={"tags": tag_ids}, timeout=15,
        )
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"  [WP] タグ更新エラー: {e}")
        return False


def _wp_cli_update_meta(wp_post_id, seo_title, excerpt):
    """WP-CLI でSEOタイトルとメタディスクリプションを更新する。"""
    php_path = "/opt/kusanagi/php/bin/php"
    wp_path  = "/opt/kusanagi/bin/wp"
    doc_root = "--path=/home/kusanagi/myblog/DocumentRoot"

    if seo_title:
        try:
            subprocess.run(
                [php_path, wp_path, "post", "meta", "update",
                 str(wp_post_id), "the_page_seo_title", seo_title,
                 doc_root, "--allow-root"],
                capture_output=True, timeout=30,
            )
        except Exception as e:
            logger.warning(f"  [WP-CLI] SEOタイトル更新失敗: {e}")

    if excerpt:
        try:
            subprocess.run(
                [php_path, wp_path, "post", "meta", "update",
                 str(wp_post_id), "the_page_meta_description", excerpt,
                 doc_root, "--allow-root"],
                capture_output=True, timeout=30,
            )
        except Exception as e:
            logger.warning(f"  [WP-CLI] メタディスクリプション更新失敗: {e}")


# =====================================================================
# 4. DB 更新
# =====================================================================
def _db_update_after_rewrite(db_path, product_id, rev_name, ai_tags_list,
                              site_label, is_ranking, ai_score):
    """
    リライト成功後に DB を更新する。
    - ai_tags, wp_tags, reviewer を最新値で上書き
    - rewrite_count を +1
    - desc_score, status, published_at は変更しない
    """
    # wp_tags の文字列を構築（post_to_wordpress / _execute_posting_flow と同一ルール）
    site_name = NORMALIZED_LABELS.get(site_label, site_label)
    wp_tags_parts = []
    if site_name:
        wp_tags_parts.append(site_name)
    for t in ai_tags_list:
        if t and t not in wp_tags_parts:
            wp_tags_parts.append(t)
    if rev_name and rev_name not in wp_tags_parts:
        wp_tags_parts.append(rev_name)
    if is_ranking:
        allowed = []
        if site_name and site_name in wp_tags_parts:
            allowed.append(site_name)
        if rev_name and rev_name in wp_tags_parts:
            allowed.append(rev_name)
        wp_tags_parts = allowed
    wp_tags_parts = [t for t in wp_tags_parts if t not in EXCLUDE_TAG_NAMES]

    ai_tags_str = ",".join(ai_tags_list)
    wp_tags_str = ",".join(wp_tags_parts)

    try:
        conn = db_connect(db_path)
        conn.execute(
            """UPDATE novelove_posts
               SET reviewer = ?,
                   ai_tags  = ?,
                   wp_tags  = ?,
                   rewrite_count = COALESCE(rewrite_count, 0) + 1,
                   is_desc_updated = 0
               WHERE product_id = ?""",
            (rev_name, ai_tags_str, wp_tags_str, product_id),
        )
        conn.commit()
        conn.close()
        logger.info(f"  [DB] 更新完了: ai_tags={ai_tags_str[:50]} / wp_tags={wp_tags_str[:50]}")
    except Exception as e:
        logger.error(f"  [DB] 更新失敗: {e}")


# =====================================================================
# 5. メイン処理
# =====================================================================
def run_rewrite(product_id, reviewer_id=None, mood=None, execute=False):
    """
    リライト実行のメイン関数。
    execute=False のとき: 記事を生成してログに出力するのみ（WP・DB を変更しない）。
    execute=True  のとき: WP 記事を実際に上書き更新し、DB も更新する。
    """
    logger.info("=" * 60)
    logger.info(f"🔄 Nexus リライトエンジン起動 | {'本番実行' if execute else 'DRY-RUN'}")
    logger.info(f"   product_id: {product_id}")
    logger.info(f"   reviewer: {reviewer_id or 'ランダム'} / mood: {mood or 'ランダム'}")
    logger.info("=" * 60)

    # --- Step 1: DB から対象記事を取得 ---
    row, db_path = _get_published_row(product_id)
    if not row:
        logger.error(f"❌ product_id '{product_id}' が見つかりません（全3DBを検索済み）")
        return False

    if row["status"] != "published":
        logger.error(f"❌ status='{row['status']}' です。リライト対象は 'published' のみです。")
        return False

    title    = row["title"]
    site_raw = row["site"] or ""
    site_label = site_raw.split(":")[0] if ":" in site_raw else str(site_raw)
    genre    = row["genre"]
    desc_str = str(row["description"]) if row["description"] else ""
    img_url  = row["image_url"] or ""

    # DigiKet 高解像度化（_execute_posting_flow L1130-1131 と同一）
    if img_url and "img.digiket.net" in img_url and "_2.jpg" in img_url:
        img_url = img_url.replace("_2.jpg", "_1.jpg")

    logger.info(f"  [対象] {title[:50]} ({site_label} / {genre})")
    logger.info(f"  [あらすじ文字数] {len(desc_str)}文字")

    # --- Step 2: WP 上の記事IDと現在タグを取得 ---
    logger.info("  [WP] 記事ID・現在タグを取得中...")
    wp_post_id, current_tag_ids = _wp_get_post_id_and_tags(product_id)
    if not wp_post_id:
        logger.error(f"❌ WP 上に slug='{product_id}' の公開記事が見つかりません")
        return False

    logger.info(f"  [WP] post_id={wp_post_id} / 現在のタグID: {current_tag_ids}")

    # --- Step 3: 保護タグを退避 ---
    protected_ids = _wp_get_protected_tag_ids(current_tag_ids)
    if protected_ids:
        logger.info(f"  [タグ保護] セール/売れ筋タグを退避: {protected_ids}")
    else:
        logger.info("  [タグ保護] 保護対象タグなし")

    # --- Step 4: target 辞書を組み立てる（_execute_posting_flow L1133-1147 と同一構造） ---
    target = {
        "product_id":    row["product_id"],
        "title":         title,
        "author":        row["author"] or "",
        "genre":         genre,
        "site":          site_raw,
        "description":   desc_str,
        "affiliate_url": row["affiliate_url"] or "",
        "image_url":     img_url,
        "release_date":  row["release_date"] or "",
        "ai_tags":       row["ai_tags"] or "",
        "desc_score":    row["desc_score"] or 0,   # 変更しない（素材濃さの指標）
        "original_tags": row["original_tags"] or "",
        "is_exclusive":  row["is_exclusive"] or 0,
    }

    # --- Step 5: AI 執筆 ---
    logger.info("  [AI] 執筆開始...")
    res = generate_article(target, override_reviewer_id=reviewer_id, override_mood=mood)

    if not res or not res[0] or not res[1]:
        err = res[5] if (res and len(res) >= 6 and res[5]) else "ai_failed"
        logger.error(f"❌ AI 執筆失敗: {err}")
        return False

    wp_title, content, excerpt, seo_title, is_r18, status, model, level, ptime, words, rev_name, ai_tags_from_ai, ai_score = res

    desc_c_len = len(desc_str)
    logger.info(f"  [完了] AI執筆成功！ (あらすじ{desc_c_len}文字 → 記事{words}文字 / ライター: {rev_name})")
    logger.info(f"  [タグ] {ai_tags_from_ai}")

    is_ranking = "ranking" in str(product_id).lower() or "ランキング" in title

    if not execute:
        # DRY-RUN: WP・DB には一切触れない
        logger.info("")
        logger.info("=" * 60)
        logger.info("✅ [DRY-RUN 完了] WordPress・DB への書き込みはしていません")
        logger.info(f"   タイトル : {wp_title}")
        logger.info(f"   SEO      : {seo_title}")
        logger.info(f"   ライター : {rev_name}")
        logger.info(f"   AIタグ   : {ai_tags_from_ai}")
        logger.info(f"   記事文字数: {words}文字")
        logger.info(f"   本番実行するには --execute を付けて再実行してください")
        logger.info("=" * 60)
        return True

    # ===== 本番実行ゾーン =====
    logger.info("  [WP] 記事本文・タイトル・抜粋を更新中...")
    if not _wp_update_post(wp_post_id, content, wp_title, excerpt):
        logger.error("❌ WP REST API での記事更新に失敗しました")
        notify_discord(
            f"🚨 **[リライト失敗]** WP記事更新エラー\n"
            f"**対象**: {title[:40]}\n"
            f"**product_id**: {product_id}",
            username="🔄 Nexusリライト"
        )
        return False

    logger.info("  [WP] SEO メタをWP-CLI で更新中...")
    _wp_cli_update_meta(wp_post_id, seo_title, excerpt)

    logger.info("  [WP] タグを再構築・更新中...")
    new_tag_ids = _build_new_tag_ids(
        ai_tags=ai_tags_from_ai,
        site_label=site_label,
        reviewer_name=rev_name,
        is_ranking=is_ranking,
        protected_ids=protected_ids,
    )
    if not _wp_update_tags(wp_post_id, new_tag_ids):
        logger.warning("  ⚠️ タグ更新に失敗しました（記事本文は更新済み）")

    logger.info("  [DB] 更新中...")
    _db_update_after_rewrite(
        db_path=db_path,
        product_id=product_id,
        rev_name=rev_name,
        ai_tags_list=ai_tags_from_ai,
        site_label=site_label,
        is_ranking=is_ranking,
        ai_score=ai_score,
    )

    # Discord 通知
    notify_discord(
        f"🔄 **[リライト完了]** [{site_label}] {title[:40]}\n"
        f"**ライター**: {rev_name}\n"
        f"**記事文字数**: {words}文字 / **あらすじ**: {desc_c_len}文字\n"
        f"**新タグ**: {', '.join(ai_tags_from_ai)}\n"
        f"**WP記事ID**: {wp_post_id}",
        username="🔄 Nexusリライト"
    )

    logger.info("=" * 60)
    logger.info(f"🏁 リライト完了！ product_id={product_id} / wp_post_id={wp_post_id}")
    logger.info("=" * 60)
    return True


# =====================================================================
# 6. エントリーポイント
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Novelove リライトエンジン — 公開済み記事を AI 再執筆して WP に反映する"
    )
    parser.add_argument(
        "--product-id", required=True,
        help="リライトする作品の product_id (例: RJ01570022)",
    )
    parser.add_argument(
        "--reviewer",
        help="ライターID (例: shion, aoi, ren, marika, momoka)。省略時はジャンル対応ライターからランダム",
    )
    parser.add_argument(
        "--mood",
        help="感情モード文字列 (例: \"布教欲が強い\")。省略時はランダム",
    )
    parser.add_argument(
        "--execute", action="store_true", default=False,
        help="実際に WP・DB を更新する。このフラグがなければ DRY-RUN (内容確認のみ)",
    )
    args = parser.parse_args()

    # WP 認証情報の事前チェック
    if not WP_USER or not WP_APP_PASSWORD:
        logger.error("❌ WP_USER または WP_APP_PASSWORD が設定されていません。.env を確認してください。")
        sys.exit(1)

    # 二重起動防止
    if not _acquire_lock():
        logger.error("❌ 別のリライトプロセスが実行中です。終了します。")
        sys.exit(1)

    try:
        success = run_rewrite(
            product_id=args.product_id,
            reviewer_id=args.reviewer,
            mood=args.mood,
            execute=args.execute,
        )
        sys.exit(0 if success else 1)
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
