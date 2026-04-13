#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
Novelove 自動投稿エンジン v14.0.0
【多重投稿ループ停止・データフロー修復・堅牢性強化】
==========================================================
【変更点 v11.4.8】
 - 修正: `get_internal_link` の動的SQL構築時、SQLite特有の ORDER BY 0 インデックスエラーを修正
【変更点 v11.4.7】
 - 修正: 投稿失敗時（画像設定エラー時等）の status 更新漏れを修正し、多重投稿を完全停止
 - 改善: SELECT * を廃止し、カラム名を明示指定することで将来の不整合リスクを排除
 - 機能: 投稿直前のタイトル重複チェック（24hガードレール）を追加
 - 改善: WP-CLI エラーログに stderr（詳細メッセージ）を含めるように変更
【変更点 v11.4.6】
 - 修正: 画像設定（FIFU）のタイムアウトを 15秒 ➔ 60秒に延長
 - 機能: 画像設定失敗時に WP 投稿を自動削除（ロールバック）するガードレールを実装
【変更点 v11.4.5】
 - 修正: _check_image_ok 関数のインポート漏れ（NameError）を修正
【変更点 v11.4.4】
 - 修正: _call_deepseek_raw 関数の消失を復元
 - 修正: クールダウン判定の時刻計算バグ（utcnow -> now）を修正
【変更点 v11.4.3】
 - 構造: 取得ロジックを novelove_fetcher.py へ完全分離
 - 修正: ランキング機能（fetch_ranking_...）でのインポート漏れを修正
==========================================================
【変更点 v11.3.5】
 - 改善: プロンプト刷新（感情モード/事実性ガード/NGフレーズ集）
【変更点 v10.1.0】
 - 修正: DigiKet等のエンコーディング判定を強化（文字化け解消）
 - 修正: FIFUアイキャッチ設定のメタキーを修正（画像欠落解消）
 - 修正: 関連記事（内部リンク）の取得ロジックを強化・安定化
 - 修正: AIタグ抽出を「部分一致マッチング」に改良（タグ消失解消）
 - 修正: ログ/出力時のエンコーディング例外対策（強制終了防止）
 - 機能: WordPress記事IDをDB（wp_post_id）に保存する機能を追加
 - 統合: ジャンル・サイト・AI・R18の4層タグ構成を標準化
==========================================================
【変更点 v9.5.3】
 - 修正: scrape_description()内のdigiket呼び出しをタプル対応に修正
【変更点 v9.4.0】
 - 機能: FIFU外部リンク化（画像アップロード廃止）
==========================================================
"""
import random
import difflib
import subprocess
import requests
import os
import urllib.parse
import sqlite3
import time
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import argparse

# --- Discord通知機能 ---
# --- ライター性格設定・執筆ルール（novelove_soul.py に分離管理）---
from novelove_soul import REVIEWERS, MOOD_PATTERNS, FACT_GUARD, NG_PHRASES, get_relationship

from novelove_core import (
    logger, ERROR_LABELS, notify_discord,
    DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET,
    get_affiliate_button_html, generate_affiliate_url,
    _get_reviewer_for_genre, _genre_label,
    get_db_path, db_connect, init_db, get_genre_index, save_genre_index,
    WP_SITE_URL,
    MAIN_LOCK_FILE, RANK_LOCK_FILE,
    is_emergency_stop, trigger_emergency_stop,
    DEEPSEEK_API_KEY, WP_USER, WP_APP_PASSWORD,
    DMM_API_ID, DMM_AFFILIATE_API_ID, DMM_AFFILIATE_LINK_ID,
    DLSITE_AFFILIATE_ID, DIGIKET_AFFILIATE_ID,
    WP_PHP_PATH, WP_CLI_PATH, WP_DOC_ROOT,
)

# === 取得ロジックは novelove_fetcher.py に分離 ===
from novelove_fetcher import (
    fetch_and_stock_all,
    fetch_digiket_items,
    FETCH_TARGETS,
    AI_TAG_WHITELIST,
    mask_input,
    scrape_description,
    scrape_digiket_description,
    _is_noise_content,
    _check_image_ok,
)

from novelove_writer import (
    _evaluate_article_potential,
    build_prompt,
    _call_deepseek_raw,
    call_deepseek,
    make_excerpt,
    generate_article,
    _inject_score3_osusume,
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
)

from novelove_ranking import process_ranking_articles


def _get_thumbnail_url(image_url: str) -> str:
    """
    大きい画像URLから、FIFUに設定する軽量サムネURLを生成する。
    確実に存在が確認済みのサイズのみ変換する（404リスク回避）。
    変換できないものはそのまま返す（例: FANZA doujin-assets）。
    """
    if not image_url:
        return image_url
    # DLsite: modpub/_img_main.jpg -> resize/_img_main_300x300.webp (18KB, 確認済み)
    if "img.dlsite.jp/modpub/" in image_url and "_img_main.jpg" in image_url:
        return image_url.replace("/modpub/", "/resize/").replace("_img_main.jpg", "_img_main_300x300.webp")
    # DMM ebook-assets: pl.jpg -> ps.jpg (16KB, 確認済み。doujin等はNOW PRINTINGになるため除外)
    if "ebook-assets.dmm" in image_url and image_url.endswith("pl.jpg"):
        return image_url[:-6] + "ps.jpg"
    # DigiKet: _1.jpg / _2.jpg -> _a_200x150.jpg (10KB, 確認済み)
    if "digiket.net" in image_url:
        return re.sub(r'_\d+\.jpg$', '_a_200x150.jpg', image_url)
    # FANZA doujin-assets 等: 変換しない（NOW PRINTINGリダイレクト対策）
    return image_url

# === WordPress投稿 ===
def get_or_create_term(name, taxonomy):
    auth = (WP_USER, WP_APP_PASSWORD)
    try:
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/{taxonomy}", auth=auth, params={"search": name}, timeout=15)
        hits = r.json()
        for hit in hits:
            if hit.get("name") == name: return hit["id"]
        r2 = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/{taxonomy}", auth=auth, json={"name": name}, timeout=15)
        return r2.json().get("id")
    except Exception:
        return None

def post_to_wordpress(title, content, genre, image_url, excerpt="", seo_title="", slug="", is_r18=False, site_label=None, ai_tags=None, reviewer=None, thumb_url=None):
    """
    WordPress REST API で投稿。FIFUプラグイン経由で外部リンクをアイキャッチに設定。
    image_url: 記事本文に埋め込む大きい画像URL
    thumb_url: FIFUアイキャッチに設定する軽量サムネURL（省略時はimage_urlをそのまま使用）
    """
    auth = (WP_USER, WP_APP_PASSWORD)
    # FIFUには軽量サムネを使用（A+C方式）
    fifu_url = thumb_url if thumb_url else image_url
    # FIFUプラグイン用メタとCocoon SEOメタ
    meta = {
        "fifu_image_url": fifu_url,
        "fifu_image_alt": title,
    }
    if seo_title: meta["the_page_seo_title"] = seo_title
    if excerpt: meta["the_page_meta_description"] = excerpt

    # === v10.6.0 新カテゴリ・タグ分類ロジック ===
    
    # 形態とジャンルに基づくカテゴリ(大分類)の決定
    # v11.0.2: ジャンル文字列による厳格判定。タイトルキーワードはフォールバックのみ。
    g_lower = str(genre).lower()
    if "novel" in g_lower:
        is_novel = True
    elif any(x in g_lower for x in ("comic", "manga", "doujin")):
        is_novel = False
    else:
        # v11.1.3: キーワード判定を廃止。取得時に公式種別でDBジャンルが確定していることを前提とする。
        is_novel = False

    is_ranking = "ranking" in str(slug).lower() or "ランキング" in title
    
    if is_ranking:
        cat_name = "ランキング"
    else:
        # 小説か漫画かでカテゴリを分ける
        is_bl = "bl" in genre.lower() or "BL" in genre
        if is_novel:
            cat_name = "BL小説" if is_bl else "TL小説"
        else:
            cat_name = "BL漫画" if is_bl else "TL漫画"
            
    cat_id = get_or_create_term(cat_name, "categories")
    categories = [cat_id] if cat_id else [25] # 25は「未分類」の安全なフォールバック

    # タグ(小分類・属性)の構成
    # GENRE_TAGS は廃止されたため空リスト、AIタグとサイト情報のみを利用
    tag_names = []
    site_name = None

    if site_label:
        normalized_labels = {"DMM.com": "DMM", "FANZA": "FANZA", "DLsite": "DLsite", "DigiKet": "DigiKet", "Lovecal": "らぶカル"}
        site_name = normalized_labels.get(site_label, site_label)
        if site_name and site_name not in tag_names: tag_names.append(site_name)

    if ai_tags:
        for t in ai_tags:
            if t and t not in tag_names: tag_names.append(t)

    # 担当者タグの付与
    if reviewer and reviewer not in tag_names:
        tag_names.append(reviewer)

    # ランキング記事の特例処理（サイト名と担当者のみを残す）
    if is_ranking:
        allowed_ranking_tags = []
        if site_name and site_name in tag_names: allowed_ranking_tags.append(site_name)
        if reviewer and reviewer in tag_names: allowed_ranking_tags.append(reviewer)
        tag_names = allowed_ranking_tags


    
    # 完全に廃止された単体タグ・不要な複合タグの徹底排除 (v10.6.0)
    exclude_list = ("BL", "TL", "コミック", "小説", "漫画", "BLコミック", "TLコミック", "BL同人", "TL同人", "商業BL", "同人BL", "商業TL", "同人TL", "商業BL小説", "商業TL小説")
    tag_names = [t for t in tag_names if t not in exclude_list]

    # WordPress側にカテゴリやタグを問い合わせてID化
    tag_ids = [t for t in [get_or_create_term(name, "tags") for name in tag_names] if t]

    post_data = {
        "title": title, "content": content, "excerpt": excerpt,
        "status": "publish", "slug": slug,
        "categories": categories, "tags": tag_ids, "meta": meta,
    }
    try:
        r = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, json=post_data, timeout=40)
    except Exception as e:
        logger.error(f"WordPress投稿接続エラー: {e}")
        return None, None

    if r.status_code in (200, 201):
        data = r.json()
        wp_post_id = data.get("id")
        link = data.get("link")
        if wp_post_id:
            # WP-CLIを使用してメタデータを確実に更新する (v11.1.2)
            # 環境依存パスは novelove_core.py で一元管理。移転時は .env を更新するだけでOK。
            php_path = WP_PHP_PATH
            wp_path  = WP_CLI_PATH
            doc_root = f"--path={WP_DOC_ROOT}"
            
            # 1. アイキャッチ画像の設定 (v13.2.3: A+C方式 — 軽量サムネURLを使用)
            if fifu_url:
                try:
                    subprocess.run([php_path, wp_path, "eval", f'fifu_dev_set_image({wp_post_id}, "{fifu_url}");', doc_root, "--allow-root"], capture_output=True, text=True, timeout=60, check=True)
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                    std_err_msg = e.stderr if hasattr(e, "stderr") else str(e)
                    logger.error(f"  [WP-CLI] 画像設定失敗 (タイムアウトまたはエラー): {std_err_msg}")
                    # 中途半端な記事を残さないためのロールバック (v11.4.14 強化)
                    try:
                        res_del = requests.delete(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}", auth=auth, params={"force": "true"}, timeout=15)
                        if res_del.status_code not in (200, 201):
                            logger.warning(f"  [ROLLBACK] 投稿削除リクエストが失敗しました。ステータスを下書きに変更します。 (status={res_del.status_code})")
                            requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}", auth=auth, json={"status": "draft"}, timeout=15)
                        else:
                            logger.warning(f"  [ROLLBACK] 画像設定失敗のため投稿を削除しました: ID={wp_post_id}")
                    except Exception as rollback_err:
                        logger.error(f"  [ROLLBACK] 致命的失敗: {rollback_err}")
                        # 最終手段として下書き変更を試行
                        try: requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}", auth=auth, json={"status": "draft"}, timeout=10)
                        except: pass
                    return None, None # 呼び出し元で wp_post_failed として処理される
            
            # 2. SEOタイトルの設定
            if seo_title:
                try:
                    subprocess.run([php_path, wp_path, "post", "meta", "update", str(wp_post_id), "the_page_seo_title", seo_title, doc_root, "--allow-root"], capture_output=True, timeout=30)
                except Exception as e:
                    logger.warning(f"  [WP-CLI] SEOタイトル設定失敗: {e}")
                
            # 3. メタディスクリプション（抜粋）の設定
            if excerpt:
                try:
                    subprocess.run([php_path, wp_path, "post", "meta", "update", str(wp_post_id), "the_page_meta_description", excerpt, doc_root, "--allow-root"], capture_output=True, timeout=30)
                except Exception as e:
                    logger.warning(f"  [WP-CLI] メタディスクリプション設定失敗: {e}")
                
        return link, wp_post_id
    
    logger.error(f"WordPress投稿失敗: status={r.status_code}, body={r.text[:500]}")
    return None, None

# === メインロジック ===
# --- [削除] 旧 main() 定義 (v11.4.14 にて統合・削除) ---
def _check_global_cooldown(cooldown_minutes=55, post_type='regular'):
    """
    全DB横断で最新の投稿時刻をチェックし、指定分数が経過しているか返す。
    経過していれば True、クールダウン中なら False を返す。
    """
    latest_pub = None
    for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
        if not os.path.exists(db_p): continue
        tmp_conn = db_connect(db_p)
        # post_type でフィルタリング (v11.4.13 修正)
        row = tmp_conn.execute(
            "SELECT published_at FROM novelove_posts WHERE status='published' AND post_type=? ORDER BY published_at DESC LIMIT 1",
            (post_type,)
        ).fetchone()
        tmp_conn.close()
        if row and row[0]:
            try:
                # v11.4.11: 常にJST（localtime）としてパース
                dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                if latest_pub is None or dt > latest_pub:
                    latest_pub = dt
            except Exception as e:
                logger.warning(f"  [クールダウン] published_atのパース失敗: {row[0]} / {e}")
    
    jst = timezone(timedelta(hours=9))
    now_jst = datetime.now(jst).replace(tzinfo=None)
    if latest_pub:
        # v11.4.14: 常にJST（UTC+9）で比較
        diff = (now_jst - latest_pub).total_seconds() / 60
        if diff < cooldown_minutes:
            return False, diff
    return True, 0

def _run_main_logic():
    """
    v11.4.12: メイン処理。
    1. クールダウンチェック（55分）<- 重い処理の前に移動
    2. 新着取得
    3. 在庫クリーンアップ
    4. 投稿実行
    """
    # ★ 緊急停止チェック
    if is_emergency_stop():
        logger.info("🚨 緊急停止中のためスキップ。解除: rm emergency_stop.lock")
        return

    # クールダウンチェック (通常投稿: 55分)
    # v11.4.12: 何よりも先に判定を行い、負荷をゼロにする
    is_ready, elapsed = _check_global_cooldown(55)
    if not is_ready:
        logger.info(f"🕒 クールダウン中（{elapsed:.1f}分経過）。0.1秒で終了します。")
        return

    # 処理開始時刻（5分タイムアウト用）
    start_time = time.time()

    fetch_and_stock_all()
    try:
        fetch_digiket_items()
    except Exception as e:
        logger.error(f"DigiKet取得エラー: {e}")

    # --- 在庫クリーンアップ ---
    for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
        if not os.path.exists(db_p): continue
        conn = db_connect(db_p)
        c = conn.cursor()
        # ① 7日以上経過したpendingをexcludedへ (JST)
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE novelove_posts SET status='excluded', last_error='expired' WHERE status='pending' AND inserted_at < ?", (seven_days_ago,))
        
        # ② ジャンルごとにスコア上位かつ最新の20件を残して、他をexcludedへ
        # FETCH_TARGETS を使わず、DBに実際に存在するジャンルを直接取得することで重複クエリを防ぐ
        genres_in_db = [row[0] for row in c.execute(
            "SELECT DISTINCT genre FROM novelove_posts WHERE status='pending'"
        ).fetchall()]
        for genre in genres_in_db:
            rows = c.execute(
                "SELECT product_id FROM novelove_posts WHERE status='pending' AND genre=? ORDER BY desc_score DESC, inserted_at DESC",
                (genre,)
            ).fetchall()
            if len(rows) > 20:
                to_exclude = [r[0] for r in rows[20:]]
                placeholders = ",".join(["?"] * len(to_exclude))
                c.execute(
                    f"UPDATE novelove_posts SET status='excluded', last_error='inventory_full' WHERE product_id IN ({placeholders})",
                    to_exclude
                )
        conn.commit()
        conn.close()
    # pendingから1件投稿（ジャンルラウンドロビン）
    g_idx_base = get_genre_index()
    posted = False
    tried_details = []
    error_count = 0  # ★ 連続失敗カウンター

    for i in range(len(FETCH_TARGETS)):
        # ★ 5分タイムアウトチェック（緊急停止ではなくスキップ＆リトライ）
        if time.time() - start_time > 300:
            logger.warning("⏰ 今回の投稿をスキップしました（5分タイムアウト）。次回のcronで自動リトライします。")
            notify_discord(
                "⏰ **投稿処理がタイムアウトしました**\n"
                "今回の投稿はスキップされましたが、次回のcron（30分後）で自動リトライします。\n"
                "※ 連続して発生する場合はサーバーのネットワーク状態を確認してください。",
                username="⏰ タイムアウト通知"
            )
            break

        target_info = FETCH_TARGETS[(g_idx_base + i) % len(FETCH_TARGETS)]
        db_path = get_db_path(target_info.get("site", "FANZA"))
        genre = target_info["genre"]
        conn = db_connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # v11.4.7: SELECT * を廃止し、カラム名を明示的に指定 (v13.2.3: original_tags, is_exclusive 追加)
        row = c.execute(
            "SELECT product_id, title, author, genre, site, status, description, affiliate_url, image_url, product_url, release_date, post_type, desc_score, ai_tags, reviewer, original_tags, is_exclusive FROM novelove_posts WHERE status='pending' AND genre=? ORDER BY inserted_at DESC LIMIT 1",
            (genre,)
        ).fetchone()
        if row:
            try:
                # ★ 全体をtry-exceptで囲む（想定外の例外も捕捉）
                success, reason = _execute_posting_flow(row, c, conn)
            except Exception as e:
                logger.error(f"  [想定外エラー] {e}")
                # pendingのまま放置されないようにexcludedに変更
                try:
                    c.execute("UPDATE novelove_posts SET status='excluded', last_error='unexpected_error' WHERE product_id=?", (row['product_id'],))
                    conn.commit()
                except Exception:
                    pass
                success = False
                reason = "unexpected_error"

            label = ERROR_LABELS.get(reason, reason) if reason else "成功"
            tried_details.append(f"・{row['title'][:30]}... ({target_info['label']}) ➔ {label}")
            if success:
                posted = True
                error_count = 0  # 成功したらリセット
            else:
                # 正常な選別処理（品質フィルタ）の結果はサーキットブレーカー対象外
                NORMAL_FILTER_REASONS = ("low_score", "duplicate_fuzzy", "excluded_foreign", "image_missing", "no_desc_or_image", "thin_score3", "excluded_by_pre_filter")
                is_normal_filter = any(reason and reason.startswith(r) for r in NORMAL_FILTER_REASONS)
                if is_normal_filter:
                    logger.info(f"  [フィルタ除外] {reason} — サーキットブレーカー対象外")
                else:
                    error_count += 1
                # ★ 3回連続「異常系」失敗でサーキットブレーカー発動
                if error_count >= 3:
                    trigger_emergency_stop(f"投稿が3回連続失敗しました（最後の理由: {reason}）")
                    break
        else:
            logger.info(f"  -> {target_info['label']} にpendingなし。次へ...")
        conn.close()
        if posted:
            save_genre_index(g_idx_base + i + 1)
            logger.info(f"✅ {target_info['label']} にて投稿成功。")
            break
    if not posted:
        # 在庫統計レポート
        inventory_list = []
        if os.path.exists(DB_FILE_FANZA):
            _c = db_connect(DB_FILE_FANZA)
            c_fanza = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site NOT LIKE '%ebook%' AND site NOT LIKE '%digital_doujin_bl%' AND site NOT LIKE '%digital_doujin_tl%'").fetchone()[0]
            c_lovecal = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND (site LIKE '%digital_doujin_bl%' OR site LIKE '%digital_doujin_tl%')").fetchone()[0]
            c_dmm = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site LIKE '%ebook%'").fetchone()[0]
            inventory_list.extend([f"FANZA {c_fanza}", f"DMM {c_dmm}", f"らぶカル {c_lovecal}"])
            _c.close()
        if os.path.exists(DB_FILE_DLSITE):
            _c = db_connect(DB_FILE_DLSITE)
            c_dl = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
            inventory_list.append(f"DLsite {c_dl}")
            _c.close()
        if os.path.exists(DB_FILE_DIGIKET):
            _c = db_connect(DB_FILE_DIGIKET)
            c_dk = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
            inventory_list.append(f"DigiKet {c_dk}")
            _c.close()
        inventory_str = " / ".join(inventory_list) + " 件"

        attempts_str = "\n".join(tried_details) if tried_details else "（なし：全在庫切れ）"
        
        # 24hエラー統計
        err_stats = {}
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        summary_stats = {"total": 0, "accepted": 0, "excluded": 0}
        for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
            if not os.path.exists(db_p): continue
            _c = db_connect(db_p)
            rows = _c.execute("SELECT last_error, count(*) FROM novelove_posts WHERE status='excluded' AND inserted_at > ? GROUP BY last_error", (yesterday,)).fetchall()
            for r in rows: err_stats[r[0]] = err_stats.get(r[0], 0) + r[1]
            summary_stats["total"]    += _c.execute("SELECT count(*) FROM novelove_posts WHERE inserted_at > ?", (yesterday,)).fetchone()[0]
            summary_stats["accepted"] += _c.execute("SELECT count(*) FROM novelove_posts WHERE status IN ('pending','published') AND inserted_at > ?", (yesterday,)).fetchone()[0]
            summary_stats["excluded"] += _c.execute("SELECT count(*) FROM novelove_posts WHERE status='excluded' AND inserted_at > ?", (yesterday,)).fetchone()[0]
            _c.close()

        display_errs = []
        for k, v in err_stats.items():
            kb = ERROR_LABELS.get(k, k)
            display_errs.append(f"  ・{kb}: {v}件")
        err_msg = "\n".join(display_errs) if display_errs else "なし"

        notify_discord(
            f"⚠️ **投稿対象なし**\n今回の実行では投稿が行われませんでした。\n\n"
            f"**【今回の試行】**\n{attempts_str}\n\n"
            f"**【現在の投稿待ち在庫】**\n{inventory_str}\n\n"
            f"**【本日の新着統計 (24h)】**\n"
            f"・全件数: {summary_stats['total']}件\n"
            f"  ┣ 採用: {summary_stats['accepted']}件\n"
            f"  ┗ 除外: {summary_stats['excluded']}件\n{err_msg}",
            username="ノベラブ通知くん"
        )
    logger.info("=" * 60)

# === [v12.2.0] クロスDB重複排除（Fuzzy Matching）===
def normalize_title(title):
    """タイトルから装飾（括弧とその中身）とスペースを除去し、スッピン文字列を返す。"""
    t = re.sub(r'[\[\(（【〈《「『].*?[\]\)）】〉》」』]', '', str(title))
    t = re.sub(r'[\s　]+', '', t)
    return t.strip()

def is_cross_db_duplicate(new_title, current_pid, threshold=0.90):
    """全DBを横断し、スッピンタイトルの類似度が閾値以上の published 記事があるか判定する。"""
    norm_new = normalize_title(new_title)
    if not norm_new:
        return False, "", 0.0
    for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
        if not os.path.exists(db_p):
            continue
        try:
            c2 = db_connect(db_p)
            c2.row_factory = sqlite3.Row
            rows = c2.execute(
                "SELECT product_id, title FROM novelove_posts WHERE status='published' AND product_id!=?",
                (current_pid,)
            ).fetchall()
            c2.close()
            for r in rows:
                norm_existing = normalize_title(r['title'])
                if not norm_existing:
                    continue
                ratio = difflib.SequenceMatcher(None, norm_new, norm_existing).ratio()
                if ratio >= threshold:
                    return True, r['title'], ratio
        except Exception as e:
            logger.warning(f"  [重複チェック] DB読み込みエラー ({db_p}): {e}")
    return False, "", 0.0

def _execute_posting_flow(row, cursor, conn):
    """v11.4.0: 執筆・タグ抽出・投稿・通知フロー。"""
    pid = row["product_id"]
    title = row["title"]
    site_raw = row["site"]
    # 🌟 NEW: らぶカルの場合はサイト自体を「Lovecal」として完全に分離独立させる
    # URLに lovecul.dmm.co.jp が含まれる作品は本来FANZAとして保存されているが、ここでLovecalに強制置換
    if "product_url" in row.keys() and "lovecul.dmm.co.jp" in str(row["product_url"]):
        site_raw = str(site_raw).replace("FANZA", "Lovecal")
        
    site_label = site_raw.split(":")[0] if isinstance(site_raw, str) and ":" in site_raw else str(site_raw)
    
    logger.info(f"--- [投稿実行] {site_label} | {title[:40]} ---")
    
    # --- [v11.4.14] AI審査前のコスト最適化（事前キーワードフィルタ） ---
    title_str = str(row['title'])
    desc_str = str(row['description']) if 'description' in row.keys() else ""
    ng_patterns = ["動画", "ボイス", "シチュエーションCD", "ASMR", "English", "Chinese", "サンプル", "【ボイス】", "【動画】"]
    if any(p in title_str for p in ng_patterns) or any(p in desc_str for p in ng_patterns):
        logger.info(f"  [Pre-Filter] 不採用キーワード、または不適合形式を検知したため除外します: {title_str[:30]}...")
        cursor.execute("UPDATE novelove_posts SET status='excluded', last_error='excluded_by_pre_filter' WHERE product_id=?", (row['product_id'],))
        conn.commit()
        return False, "excluded_by_pre_filter"

    # 🌟 NEW: AI事前評価スキップロジック
    logger.info(f"  [{row['genre']}] 事前品質審査開始: {title[:30]}...")
    _orig_tags_for_eval = row["original_tags"] if "original_tags" in row.keys() else ""
    eval_score = _evaluate_article_potential(title, desc_str, original_tags=_orig_tags_for_eval)
    logger.info(f"  -> AI品質スコア: {eval_score}/5点")
    
    # スコア2以下は破棄（中身がスッカスカ、ノイズのみ）
    if eval_score <= 2:
        logger.warning(f"  -> 内容が不十分（スコア{eval_score}点）のため執筆スキップ")
        cursor.execute("UPDATE novelove_posts SET status='excluded', last_error='low_score' WHERE product_id=?", (pid,))
        conn.commit()
        return False, f"low_score: {eval_score}"
        
    logger.info(f"  ✅ スコア基準クリア ({eval_score}点)。執筆を開始します。")

    # DigiKet高解像度化を一元処理
    img_url = row["image_url"] or ""
    if img_url and "img.digiket.net" in img_url and "_2.jpg" in img_url:
        img_url = img_url.replace("_2.jpg", "_1.jpg")
    # A+C方式: FIFUには軽量サムネ、記事本文には大きいURLを使う
    thumb_url = _get_thumbnail_url(img_url)

    # 🌟 URLにloveculが含まれていたら、DBのsiteがFANZA等でも強制的にLovecal扱いにする
    _product_url_val = row["product_url"] or ""
    if "lovecul.dmm.co.jp" in _product_url_val:
        site_raw = "Lovecal:r18=1"
        site_label = "Lovecal"

    target = {
        "product_id":    pid,
        "title":         row["title"],
        "author":        row["author"] or "",
        "genre":         row["genre"],
        "site":          site_raw,
        "description":   desc_str,
        "product_url":   _product_url_val,
        # 🌟 v14.3.0: affiliate_urlはDBのキャッシュを使わず、product_urlから毎回再生成する
        # （らぶカル等のアフィリエイトドメイン判定バグを根絶する）
        "affiliate_url": generate_affiliate_url(site_label, _product_url_val),
        "image_url":     img_url,
        "thumb_url":     thumb_url,
        "release_date":  row["release_date"],
        "ai_tags":       row["ai_tags"],
        "desc_score":    eval_score,  # スコアを渡す
        "original_tags": row["original_tags"] if "original_tags" in row.keys() else "",
        "is_exclusive":  row["is_exclusive"] if "is_exclusive" in row.keys() else 0,
    }

    # v12.2.0: 全DB横断・Fuzzy Matching重複チェック (旧24hガードレールを完全置換)
    is_dup, dup_title, dup_ratio = is_cross_db_duplicate(title, pid)
    if is_dup:
        logger.warning(f"  [重複ブロック] スッピンタイトル '{normalize_title(title)}' は '{normalize_title(dup_title)}' と類似度 {dup_ratio:.0%} のためスキップ (元: {dup_title[:40]})")
        cursor.execute("UPDATE novelove_posts SET status='excluded', last_error='duplicate_fuzzy' WHERE product_id=?", (pid,))
        conn.commit()
        return False, "duplicate_fuzzy"

    res = generate_article(target)
    if not res or not res.wp_title or not res.content:
        err = res.status if res and res.status not in ("ok", "") else "ai_failed"
        # content_block（AIが内容的に執筆不可と判断）は再挑戦しても無駄なのでexcluded
        # それ以外（サーバーダウン等の一時エラー）はpendingに戻して次回再挑戦させる
        if err == "content_block":
            cursor.execute("UPDATE novelove_posts SET status='excluded', last_error=? WHERE product_id=?", (err, pid))
        else:
            cursor.execute("UPDATE novelove_posts SET last_error=? WHERE product_id=?", (err, pid))
            logger.info(f"  [お蔵入り防止] status=pending のまま保持。次回再挑戦します。(reason={err})")
        conn.commit()
        return False, err

    wp_title    = res.wp_title
    content     = res.content
    excerpt     = res.excerpt
    seo_title   = res.seo_title
    is_r18      = res.is_r18
    status      = res.status
    model       = res.model
    level       = res.level
    ptime       = res.proc_time
    words       = res.word_count
    rev_name    = res.reviewer_name
    ai_tags_from_ai = res.ai_tags
    ai_score    = res.ai_score

    # AI執筆完了時に、取得できたあらすじの文字数をログに出力（スクレイピング品質の検証証明）
    desc_c_len = len(str(target.get("description", "")))
    logger.info(f"  [完了] AI執筆完了！(抽出あらすじ文字数: {desc_c_len}文字)")

    # AIスコア安全弁（通常は事前審査済みなのでここには来ないが、万一のフェイルセーフ）
    if ai_score == 0:
        ai_score = eval_score  # 事前審査でのスコアを使用

    # タグ: generate_article内で既にDB既存タグへのフォールバック＋専売タグ付与済み
    final_ai_tags = ai_tags_from_ai

    # v13.5.1: 専売タグの付与（DBの is_exclusive フラグに基づく厳密なDOM判定結果）
    is_exclusive = (row["is_exclusive"] if "is_exclusive" in row.keys() else 0) == 1
    if is_exclusive:
        _normalized = {"DMM.com": "DMM", "FANZA": "FANZA", "DLsite": "DLsite", "DigiKet": "DigiKet", "Lovecal": "Lovecal"}
        _sn = _normalized.get(site_label, site_label)
        excl_tag = {"DLsite": "DLsite専売", "FANZA": "FANZA独占", "DMM": "DMM独占", "DigiKet": "DigiKet限定", "Lovecal": "らぶカル独占"}.get(_sn, "")
        if not excl_tag and "らぶカル" in str(site_label):
            excl_tag = "らぶカル独占"
        if excl_tag and excl_tag not in final_ai_tags:
            final_ai_tags.append(excl_tag)
    link, wp_post_id = post_to_wordpress(
        wp_title, content, row["genre"], img_url,
        excerpt=excerpt, seo_title=seo_title, slug=pid, is_r18=is_r18,
        site_label=site_label, ai_tags=final_ai_tags, reviewer=rev_name,
        thumb_url=thumb_url
    )
    
    if link:
        ai_tags_str = ",".join(final_ai_tags)
        # v12.8.0: wp_tags（WPへ実際に送信した完成品タグ一覧）を構築してDBへ書き戻す
        # ※ post_to_wordpress() 内のタグ構築ロジック(L746-778)と完全一致させること
        _normalized_labels = {"DMM.com": "DMM", "FANZA": "FANZA", "DLsite": "DLsite", "DigiKet": "DigiKet", "Lovecal": "らぶカル"}
        _site_name_for_wp = _normalized_labels.get(site_label, site_label)
        _wp_tags_parts = []
        if _site_name_for_wp:
            _wp_tags_parts.append(_site_name_for_wp)
        for _t in final_ai_tags:
            if _t and _t not in _wp_tags_parts:
                _wp_tags_parts.append(_t)
        if rev_name and rev_name not in _wp_tags_parts:
            _wp_tags_parts.append(rev_name)
        # ランキング記事特例（post_to_wordpress L763-767 と同一）
        _is_ranking = "ranking" in str(pid).lower() or "ランキング" in str(row["title"])
        if _is_ranking:
            _allowed = []
            if _site_name_for_wp and _site_name_for_wp in _wp_tags_parts:
                _allowed.append(_site_name_for_wp)
            if rev_name and rev_name in _wp_tags_parts:
                _allowed.append(rev_name)
            _wp_tags_parts = _allowed
        # exclude_list フィルタ（post_to_wordpress L777-778 と同一）
        _exclude = ("BL", "TL", "コミック", "小説", "漫画", "BLコミック", "TLコミック", "BL同人", "TL同人", "商業BL", "同人BL", "商業TL", "同人TL", "商業BL小説", "商業TL小説")
        _wp_tags_parts = [_t for _t in _wp_tags_parts if _t not in _exclude]
        wp_tags_str = ",".join(_wp_tags_parts)
        # v11.4.0: ai_tags も最新版で上書き保存, 過去のエラー履歴（last_error）もクリア, desc_scoreも保存
        # v12.8.0: wp_tags も同時保存
        # v14.2.0: wp_post_id を書き戻すよう修正（wp_post_idが保存されない致命的バグを修正）
        cursor.execute(
            "UPDATE novelove_posts SET status='published', wp_post_id=?, wp_post_url=?, published_at=datetime('now', 'localtime'), reviewer=?, ai_tags=?, wp_tags=?, last_error=NULL, desc_score=? WHERE product_id=?",
            (wp_post_id, link, rev_name, ai_tags_str, wp_tags_str, ai_score, pid)
        )
        conn.commit()

        
        # 統計取得
        total_daily = 0
        for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
            if not os.path.exists(db_p): continue
            _conn = db_connect(db_p)
            count = _conn.execute("SELECT COUNT(*) FROM novelove_posts WHERE status='published' AND date(published_at) = date('now', 'localtime')").fetchone()[0]
            total_daily += count
            _conn.close()

        inventory_list = []
        if os.path.exists(DB_FILE_FANZA):
            _c = db_connect(DB_FILE_FANZA)
            c_lovecal = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND product_url LIKE '%lovecul.dmm.co.jp%'").fetchone()[0]
            c_dmm     = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site LIKE '%ebook%' AND product_url NOT LIKE '%lovecul.dmm.co.jp%'").fetchone()[0]
            c_fanza   = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site NOT LIKE '%ebook%' AND product_url NOT LIKE '%lovecul.dmm.co.jp%'").fetchone()[0]
            inventory_list.extend([f"FANZA {c_fanza}", f"DMM {c_dmm}", f"らぶカル {c_lovecal}"])
            _c.close()
        if os.path.exists(DB_FILE_DLSITE):
            _c = db_connect(DB_FILE_DLSITE)
            c_dl = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
            inventory_list.append(f"DLsite {c_dl}")
            _c.close()
        if os.path.exists(DB_FILE_DIGIKET):
            _c = db_connect(DB_FILE_DIGIKET)
            c_dk = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
            inventory_list.append(f"DigiKet {c_dk}")
            _c.close()
        inventory_str = " / ".join(inventory_list) + " 件"

        notify_discord(
            f"✅ **[{site_label}] [{_genre_label(row['genre'])}] 投稿成功！**\n"
            f"**タイトル**: {wp_title}\n"
            f"**統計**: 今日 {total_daily}件目 / スコア{ai_score} / あらすじ{desc_c_len}文字 / 記事{words}文字 / ライター: {rev_name}\n"
            f"**投稿待ち在庫**: {inventory_str}\n"
            f"**URL**: {link}",
            username="ノベラブ通知くん"
        )
        logger.info(f"✅ 投稿成功！ URL: {link}")
        return True, None
    else:
        cursor.execute("UPDATE novelove_posts SET status='excluded', last_error='wp_post_failed' WHERE product_id=?", (pid,))
        conn.commit()
        logger.warning(f"⚠️ WP投稿失敗: {pid} (status='excluded' に変更)")
        return False, "wp_post_failed"

def main():
    # ★ 緊急停止チェック（最頂部）
    if is_emergency_stop():
        logger.info("🚨 緊急停止中のためスキップ。解除: rm emergency_stop.lock")
        return

    logger.info("Novelove エンジン v14.0.0 起動")
    init_db()
    # メインロックチェック
    if os.path.exists(MAIN_LOCK_FILE):
        mtime = os.path.getmtime(MAIN_LOCK_FILE)
        if time.time() - mtime > 7200:
            logger.warning("🚨 メインロックが2時間を超えています。強制解除して続行します。")
            try:
                os.remove(MAIN_LOCK_FILE)
            except Exception as e:
                logger.error(f"ロック解除失敗: {e}")
                return
        else:
            logger.info("🕒 メイン処理は既に実行中です。終了します。")
            return

    # ランキングロックチェック
    if os.path.exists(RANK_LOCK_FILE):
        logger.info("🕒 ランキング処理が実行中です。通常投稿はスキップします。")
        return

    try:
        with open(MAIN_LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logger.error(f"🚨 メインロック作成失敗: {e}")
        return

    try:
        _run_main_logic()
    finally:
        try:
            if os.path.exists(MAIN_LOCK_FILE):
                os.remove(MAIN_LOCK_FILE)
        except Exception as e:
            logger.error(f"🚨 メインロック解除失敗: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Novelove Auto Posting Tool")
    parser.add_argument("--ranking", action="store_true", help="Run the ranking generation workflow")
    args = parser.parse_args()
    if args.ranking:
        process_ranking_articles()
    else:
        main()
