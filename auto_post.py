#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
Novelove 自動投稿エンジン v21.1.1
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
 - 修正: エンコーディング判定を強化（文字化け解消）
 - 修正: FIFUアイキャッチ設定のメタキーを修正（画像欠落解消）
 - 修正: 関連記事（内部リンク）の取得ロジックを強化・安定化
 - 修正: AIタグ抽出を「部分一致マッチング」に改良（タグ消失解消）
 - 修正: ログ/出力時のエンコーディング例外対策（強制終了防止）
 - 機能: WordPress記事IDをDB（wp_post_id）に保存する機能を追加
 - 統合: ジャンル・サイト・AI・R18の4層タグ構成を標準化
==========================================================
【変更点 v9.5.3】
 - 修正: scrape_description()内の呼び出しをタプル対応に修正
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
import base64
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import argparse

# --- Discord通知機能 ---
# --- ライター性格設定・執筆ルール（novelove_soul.py に分離管理）---
from novelove_soul import REVIEWERS
from novelove_bluesky import post_to_bluesky

from novelove_core import (
    logger, ERROR_LABELS, notify_discord,
    DB_FILE_UNIFIED,
    get_affiliate_button_html, generate_affiliate_url,
    _get_reviewer_for_genre, _genre_label,
    get_db_path, get_source_db, db_connect, init_db, get_genre_index, save_genre_index,
    WP_SITE_URL,
    MAIN_LOCK_FILE, RANK_LOCK_FILE,
    is_emergency_stop, trigger_emergency_stop,
    OPENROUTER_API_KEY, WP_USER, WP_APP_PASSWORD,
    DMM_API_ID, DMM_AFFILIATE_API_ID, DMM_AFFILIATE_LINK_ID,
    DLSITE_AFFILIATE_ID,
    WP_PHP_PATH, WP_CLI_PATH, WP_DOC_ROOT,
    normalize_title, super_normalize_title,
    acquire_lock, release_lock,
)

def _recover_posting_orphans():
    """
    status='posting' のまま残っている幽霊記事を検出・リカバリする。
    WP上に記事が既に存在すれば status='published' に更新し、なければ 'pending' に戻す。
    """
    conn = db_connect(DB_FILE_UNIFIED)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        orphans = c.execute(
            "SELECT product_id, title FROM novelove_posts WHERE status='posting'"
        ).fetchall()
        if not orphans:
            return

        logger.warning(f"⚠️ [リカバリ] status='posting' のまま残っている記事が {len(orphans)} 件あります。WPと照合します。")
        auth = (WP_USER, WP_APP_PASSWORD)

        for row in orphans:
            pid = row["product_id"]
            title = row["title"]
            try:
                # WordPress から slug を条件に既存投稿を検索
                r = requests.get(
                    f"{WP_SITE_URL}/wp-json/wp/v2/posts",
                    params={"slug": pid, "_fields": "id,link"},
                    auth=auth, timeout=15
                )
                if r.status_code == 200 and r.json():
                    wp_data = r.json()[0]
                    c.execute(
                        "UPDATE novelove_posts SET status='published', wp_post_id=?, wp_post_url=? WHERE product_id=?",
                        (wp_data["id"], wp_data["link"], pid)
                    )
                    logger.info(f"  [リカバリ成功] {pid} ({title[:15]}) は既にWP上に存在するため published に修復しました。")
                else:
                    c.execute(
                        "UPDATE novelove_posts SET status='pending', last_error='recovered_from_posting' WHERE product_id=?",
                        (pid,)
                    )
                    logger.info(f"  [リカバリ成功] {pid} ({title[:15]}) はWP上に存在しないため pending に差し戻しました。")
            except Exception as e:
                logger.error(f"  [リカバリ失敗] {pid} のWP照合中にエラーが発生しました: {e}")
        conn.commit()
    except Exception as e:
        logger.error(f"🚨 リカバリ処理全体でエラーが発生しました: {e}")
    finally:
        conn.close()

from novelove_fetcher import (
    fetch_and_stock_all,
    FETCH_TARGETS,
    mask_input,
    format_author_detail,
)

from novelove_writer import (
    _evaluate_article_potential,
    build_prompt,
    _call_deepseek_raw,
    call_deepseek,
    make_excerpt,
    generate_article,
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

def post_to_wordpress(title, content, genre, image_url, excerpt="", seo_title="", slug="", is_r18=False, site_label=None, ai_tags=None, reviewer=None, thumb_url=None, overwrite=False):
    """
    WordPress REST API で投稿。FIFUプラグイン経由で外部リンクをアイキャッチに設定。
    image_url: 記事本文に埋め込む大きい画像URL
    thumb_url: FIFUアイキャッチに設定する軽量サムネURL（省略時はimage_urlをそのまま使用）
    overwrite: True の場合、同一 slug の既存投稿があればそれを上書き更新する（v21.5.0: 固定スラグ・ランキング記事用）。
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
    # v19.0.0: ボイスジャンル対応
    is_voice = "voice" in g_lower
    if "novel" in g_lower:
        is_novel = True
    elif any(x in g_lower for x in ("comic", "manga", "doujin")):
        is_novel = False
    else:
        # v11.1.3: キーワード判定を廃止。取得時に公式種別でDBジャンルが確定していることを前提とする。
        is_novel = False

    is_ranking = "ranking" in str(slug).lower() or "ランキング" in title
    is_curation = "curation" in str(genre).lower()
    is_bl = "bl" in str(genre).lower()
    
    if is_ranking:
        cat_name = "ランキング"
    elif is_curation:
        cat_name = "BLまとめ" if is_bl else "TLまとめ"
    elif is_voice:
        # v19.0.0: ボイス作品用カテゴリ
        cat_name = "BLボイス" if is_bl else "TLボイス"
    else:
        # 小説か漫画かでカテゴリを分ける
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
        normalized_labels = {"DMM.com": "DMM", "DLsite": "DLsite（がるまに）", "Lovecal": "らぶカル"}
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
        # 👇 ゲストタグの救済を追加
        if ai_tags:
            for t in ai_tags:
                if t in tag_names and t not in allowed_ranking_tags:
                    allowed_ranking_tags.append(t)
        tag_names = allowed_ranking_tags
    elif is_curation:
        # まとめ記事用の特例処理：担当者、まとめ対象のタグを付与（余計なBL/TL/クロスタグは廃止）
        allowed_curation_tags = []
        if reviewer: allowed_curation_tags.append(reviewer)
        if ai_tags:
            for t in ai_tags:
                allowed_curation_tags.append(t)
        tag_names = allowed_curation_tags

    
    # 完全に廃止された単体タグ・不要な複合タグの徹底排除 (v10.6.0)
    exclude_list = ("BL", "TL", "コミック", "小説", "漫画", "BLコミック", "TLコミック", "BL同人", "TL同人", "商業BL", "同人BL", "商業TL", "同人TL", "商業BL小説", "商業TL小説")
    if is_curation:
        # まとめ記事では BL/TL は排除しない
        exclude_list = ("コミック", "小説", "漫画", "BLコミック", "TLコミック", "BL同人", "TL同人", "商業BL", "同人BL", "商業TL", "同人TL", "商業BL小説", "商業TL小説")
    tag_names = [t for t in tag_names if t not in exclude_list]

    # WordPress側にカテゴリやタグを問い合わせてID化
    tag_ids = [t for t in [get_or_create_term(name, "tags") for name in tag_names] if t]

    post_data = {
        "title": title, "content": content, "excerpt": excerpt,
        "status": "publish", "slug": slug,
        "categories": categories, "tags": tag_ids, "meta": meta,
    }

    # v21.5.0: overwrite=True かつ同一slugの既存投稿があれば、新規作成ではなく上書き更新する。
    # （固定スラグ運用のランキング記事で、毎週スラグに -2 が付く重複を防止する）
    endpoint = f"{WP_SITE_URL}/wp-json/wp/v2/posts"
    if overwrite and slug:
        try:
            existing = requests.get(
                endpoint, auth=auth,
                params={"slug": slug, "status": ["publish", "draft", "pending", "future", "private"], "_fields": "id"},
                timeout=20,
            )
            if existing.status_code == 200:
                arr = existing.json()
                if isinstance(arr, list) and arr and arr[0].get("id"):
                    existing_id = arr[0]["id"]
                    endpoint = f"{WP_SITE_URL}/wp-json/wp/v2/posts/{existing_id}"
                    # 「今週のピックアップ」の鮮度を出すため公開日を現在時刻へ更新する
                    post_data["date"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    logger.info(f"  [WP] 既存スラグ '{slug}' を上書き更新します (ID={existing_id})")
        except Exception as e:
            logger.warning(f"  [WP] 既存スラグ確認に失敗（新規作成にフォールバック）: {e}")

    try:
        r = requests.post(endpoint, auth=auth, json=post_data, timeout=40)
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
                    # v15.4.0: Base64エンコードでRCE脆弱性を完全排除
                    b64_url = base64.b64encode(fifu_url.encode('utf-8')).decode('utf-8')
                    php_code = f"$url = base64_decode('{b64_url}'); fifu_dev_set_image({wp_post_id}, $url);"
                    subprocess.run([php_path, wp_path, "eval", php_code, doc_root, "--allow-root"], capture_output=True, text=True, timeout=60, check=True)
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
                        # S-4: 削除も下書き化も両方失敗した場合はDiscordへ通知（ゾンビ公開記事の見落とし防止）
                        notify_discord(
                            f"🚨 **ROLLBACKが致命的に失敗しました**\n"
                            f"WP投稿ID: `{wp_post_id}` が公開されたままの可能性があります。\n"
                            f"**エラー**: {rollback_err}\n"
                            f"手動で確認・削除してください。",
                            username="🚨 ロールバック失敗通知"
                        )
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

            # 4. まとめ記事: Cocoon目次を非表示（the_page_toc_novisible = 1）
            if is_curation:
                try:
                    subprocess.run([php_path, wp_path, "post", "meta", "update", str(wp_post_id), "the_page_toc_novisible", "1", doc_root, "--allow-root"], capture_output=True, timeout=30)
                except Exception as e:
                    logger.warning(f"  [WP-CLI] TOC非表示設定失敗: {e}")
                
        return link, wp_post_id
    
    logger.error(f"WordPress投稿失敗: status={r.status_code}, body={r.text[:500]}")
    return None, None

# === メインロジック ===
# --- [削除] 旧 main() 定義 (v11.4.14 にて統合・削除) ---

def _get_dynamic_cooldown() -> int:
    """
    v21.2.6: DBの投稿待ち在庫(pending)数に応じてクールダウン時間を動的に決定する。
    - 在庫30件以上 -> 14分 (実効15分間隔 / 日最大96件: アクティブモード)
    - 在庫10～29件 -> 25分 (実効30分間隔 / 日最大48件: 標準モード)
    - 在庫9件以下  -> 55分 (実効60分間隔 / 日最大24件: セーブモード)
    エラー発生時はデフォルトの25分を返す。
    """
    try:
        tmp = db_connect(DB_FILE_UNIFIED)
        row = tmp.execute(
            "SELECT COUNT(*) FROM novelove_posts WHERE status='pending' AND post_type='regular'"
        ).fetchone()
        tmp.close()
        count = row[0] if row else 0
    except Exception as e:
        logger.error(f"  [Dynamic Cooldown] 在庫数取得失敗: {e}")
        return 25  # フォールバック

    if count >= 30:
        cooldown = 14   # アクティブモード: cronの度に毎回投稿
    elif count >= 10:
        cooldown = 25   # 標準モード: 現状維持
    else:
        cooldown = 55   # セーブモード: 在庫枯渇防止

    logger.info(f"  [Dynamic Cooldown] 投稿待ち在庫: {count}件 -> クールダウン: {cooldown}分")
    return cooldown

def _check_global_cooldown(cooldown_minutes=45, post_type='regular'):
    """
    統合DBから最新の投稿時刻をチェックし、指定分数が経過しているか返す。
    経過していれば True、クールダウン中なら False を返す。
    """
    latest_pub = None
    # v18.0.0: 統合DB1本から最新投稿時刻を取得
    tmp_conn = db_connect(DB_FILE_UNIFIED)
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
    1. クールダウンチェック（45分）<- 重い処理の前に移動
    2. 新着取得
    3. 在庫クリーンアップ
    4. 投稿実行
    """
    # ★ 緊急停止チェック
    if is_emergency_stop():
        logger.info("🚨 緊急停止中のためスキップ。解除: rm emergency_stop.lock")
        return

    # 投稿状態の不整合リカバリを実行 (v21.3.0 追加)
    _recover_posting_orphans()

    # クールダウンチェック (通常投稿: cron15分+cooldown25分で実効約30分間隔)
    # v11.4.12: 何よりも先に判定を行い、負荷をゼロにする
    # v19.1.1: cron15分/cooldown25分に変更（~48件/日へ増量）
    is_ready, elapsed = _check_global_cooldown(_get_dynamic_cooldown())
    if not is_ready:
        logger.info(f"🕒 クールダウン中（{elapsed:.1f}分経過）。0.1秒で終了します。")
        return


    fetch_and_stock_all()


    # --- 在庫クリーンアップ (v18.0.0: 統合DB対応) ---
    conn = db_connect(DB_FILE_UNIFIED)
    c = conn.cursor()
    # ① 7日以上経過したpendingをexcludedへ (JST)
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("UPDATE novelove_posts SET status='excluded', last_error='expired' WHERE status='pending' AND inserted_at < ?", (seven_days_ago,))

    # ② source_dbごと・ジャンルごとにスコア上位の20件を残し、他をexcludedへ
    # source_dbで分離することで、各サイトの在庫バランスが崩れないよう管理する
    for sdb in ['dmm', 'lovecal', 'dlsite']:
        genres_in_sdb = [row[0] for row in c.execute(
            "SELECT DISTINCT genre FROM novelove_posts WHERE status='pending' AND source_db=?",
            (sdb,)
        ).fetchall()]
        for genre in genres_in_sdb:
            rows = c.execute(
                "SELECT product_id FROM novelove_posts WHERE status='pending' AND genre=? AND source_db=? ORDER BY desc_score DESC, inserted_at DESC",
                (genre, sdb)
            ).fetchall()
            # v21.2.7: DMMは新着取得量が多いため在庫上限を120件に拡張。その他は60件を維持。
            limit_val = 120 if sdb == 'dmm' else 60
            if len(rows) > limit_val:
                to_exclude = [r[0] for r in rows[limit_val:]]
                placeholders = ",".join(["?"] * len(to_exclude))
                c.execute(
                    f"UPDATE novelove_posts SET status='excluded', last_error='inventory_full' WHERE product_id IN ({placeholders})",
                    to_exclude
                )
    conn.commit()
    conn.close()
    # pendingから1件投稿（ジャンルラウンドロビン）
    # ★ タイムアウトはfetch完了後・投稿ループ開始時点から計測する
    start_time = time.time()
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
        # v18.0.0: source_dbでサイトグループを絞り込み（サイト間の混在防止）
        source_db_val = target_info.get("source_db") or get_source_db(target_info.get("site", "Lovecal"))
        genre = target_info["genre"]
        conn = db_connect(DB_FILE_UNIFIED)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # v11.4.7: SELECT * を廃止し、カラム名を明示的に指定 (v13.2.3: original_tags, is_exclusive 追加)
        # v15.7.0: ORDER BY に desc_score DESC を追加し、品質スコアの高い記事を優先投稿する
        # v18.0.0: AND source_db=? を追加し、サイトグループ内でのみ選択する
        rows = c.execute(
            "SELECT product_id, title, author, genre, site, status, description, affiliate_url, image_url, product_url, release_date, post_type, desc_score, ai_tags, reviewer, original_tags, is_exclusive, author_detail, cast_info, series_name, page_count FROM novelove_posts WHERE status='pending' AND genre=? AND source_db=? ORDER BY desc_score DESC, inserted_at DESC",
            (genre, source_db_val)
        ).fetchall()
        
        category_success = False
        if rows:
            for row in rows:
                # ★ 5分タイムアウトチェック（ループ内でも実施）
                if time.time() - start_time > 300:
                    break
                
                pid = row['product_id']
                try:
                    # ★ 全体をtry-exceptで囲む（想定外の例外も捕捉）
                    success, reason = _execute_posting_flow(row, c, conn)
                except Exception as e:
                    logger.error(f"  [想定外エラー] {e}")
                    try:
                        c.execute("UPDATE novelove_posts SET status='excluded', last_error='unexpected_error' WHERE product_id=?", (pid,))
                        conn.commit()
                    except Exception:
                        pass
                    success = False
                    reason = "unexpected_error"

                label = ERROR_LABELS.get(reason, reason) if reason else "成功"
                tried_details.append(f"・{row['title'][:30]}... ({target_info['label']}) ➔ {label}")
                if success:
                    posted = True
                    category_success = True
                    error_count = 0  # 成功したらリセット
                    break  # 投稿成功したので、このカテゴリのループを抜ける
                else:
                    # 正常な選別処理（品質フィルタ）の結果はサーキットブレーカー対象外
                    NORMAL_FILTER_REASONS = ("low_score", "duplicate_fuzzy", "excluded_foreign", "image_missing", "no_desc_or_image", "thin_score3", "excluded_by_pre_filter")
                    is_normal_filter = any(reason and reason.startswith(r) for r in NORMAL_FILTER_REASONS)
                    if is_normal_filter:
                        logger.info(f"  [フィルタ除外] {reason} — サーキットブレーカー対象外。次の保留作品を試します。")
                    else:
                        error_count += 1
                    # ★ 3回連続「異常系」失敗でサーキットブレーカー発動
                    if error_count >= 3:
                        trigger_emergency_stop(f"投稿が3回連続失敗しました（最後の理由: {reason}）")
                        break
        else:
            logger.info(f"  -> {target_info['label']} にpendingなし。次へ...")
        conn.close()
        
        # 緊急停止発動時は全体のカテゴリ巡回ループも抜ける
        if is_emergency_stop():
            break
        if posted:
            save_genre_index(g_idx_base + i + 1)
            logger.info(f"✅ {target_info['label']} にて投稿成功。")
            break
    if not posted:
        # 在庫統計レポート
        # v18.0.0: 統合DB1本から在庫カウント
        inventory_list = []
        _c = db_connect(DB_FILE_UNIFIED)
        c_dmm     = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site LIKE 'DMM%'").fetchone()[0]
        c_lovecal = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site LIKE 'Lovecal%'").fetchone()[0]
        c_dl      = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND source_db='dlsite'").fetchone()[0]
        _c.close()
        inventory_list = [f"DMM {c_dmm}", f"らぶカル {c_lovecal}", f"DLsite {c_dl}"]
        inventory_str = " / ".join(inventory_list) + " 件"

        attempts_str = "\n".join(tried_details) if tried_details else "（なし：全在庫切れ）"
        
        # 24hエラー統計
        err_stats = {}
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        summary_stats = {"total": 0, "accepted": 0, "excluded": 0}
        # v18.0.0: 統合DB1本からエラー統計を集計
        _c = db_connect(DB_FILE_UNIFIED)
        rows = _c.execute("SELECT last_error, count(*) FROM novelove_posts WHERE status='excluded' AND inserted_at > ? GROUP BY last_error", (yesterday,)).fetchall()
        for r in rows: err_stats[r[0]] = err_stats.get(r[0], 0) + r[1]
        summary_stats["total"]    = _c.execute("SELECT count(*) FROM novelove_posts WHERE inserted_at > ?", (yesterday,)).fetchone()[0]
        summary_stats["accepted"] = _c.execute("SELECT count(*) FROM novelove_posts WHERE status IN ('pending','published') AND inserted_at > ?", (yesterday,)).fetchone()[0]
        summary_stats["excluded"] = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='excluded' AND inserted_at > ?", (yesterday,)).fetchone()[0]
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

# === [v20.0.7] 末尾数字（話数・巻数）抽出用ヘルパー ===
def extract_tail_number(title_str):
    if not title_str:
        return None
    import re
    s = title_str.strip()
    
    circled_map = {
        '①': 1, '②': 2, '③': 3, '④': 4, '⑤': 5, '⑥': 6, '⑦': 7, '⑧': 8, '⑨': 9, '⑩': 10,
        '⑪': 11, '⑫': 12, '⑬': 13, '⑭': 14, '⑮': 15, '⑯': 16, '⑰': 17, '⑱': 18, '⑲': 19, '⑳': 20
    }
    
    if s and s[-1] in circled_map:
        return circled_map[s[-1]]
        
    m = re.search(r'[\(\[（【〈《「『]\s*([0-9０-９]+)\s*[話巻]?\s*[\)\]）】〉》」』]$', s)
    if m:
        num_str = m.group(1)
        num_str = "".join(chr(ord(c) - 0xfee0) if '０' <= c <= '９' else c for c in num_str)
        try:
            return int(num_str)
        except ValueError:
            return None
            
    m2 = re.search(r'([0-9０-９]+)\s*[話巻]?\s*[！？\?\!\.…\s]*$', s)
    if m2:
        num_str = m2.group(1)
        num_str = "".join(chr(ord(c) - 0xfee0) if '０' <= c <= '９' else c for c in num_str)
        try:
            return int(num_str)
        except ValueError:
            return None
            
    return None

# === [v12.2.0] クロスDB重複排除（Fuzzy Matching）===
def is_cross_db_duplicate(new_title, new_desc, current_pid, threshold=0.90):
    """全DBを横断し、スッピンタイトルの類似度が閾値以上の published 記事があるか判定する。"""
    norm_new_clean = super_normalize_title(new_title)
    if not norm_new_clean:
        return False, "", 0.0
    
    # 記号・スペースを排除した純粋なスッピンタイトルの先頭5文字を部分一致のキーにする
    clean_prefix = norm_new_clean[:5]
    if not clean_prefix:
        clean_prefix = norm_new_clean[:2]  # フォールバック
    if not clean_prefix:
        return False, "", 0.0
        
    query_pattern = f"%{clean_prefix}%"
    norm_new = normalize_title(new_title)
    new_tail_num = extract_tail_number(new_title)
    
    # v18.0.0: 統合DB1本で全サイト横断検索（検索漏れがなくなり改善）
    try:
        c2 = db_connect(DB_FILE_UNIFIED)
        c2.row_factory = sqlite3.Row
        # SQL側で正規化タイトルに対する部分一致で高速絞り込み（LIMIT制限なし）
        rows = c2.execute(
            "SELECT product_id, title, description FROM novelove_posts WHERE status='published' AND product_id!=? AND super_normalize_title(title) LIKE ?",
            (current_pid, query_pattern)
        ).fetchall()
        c2.close()
        for r in rows:
            norm_existing = normalize_title(r['title'])
            if not norm_existing:
                continue
                
            # 話数・巻数（末尾数字）の異なる作品を重複から除外（救済）
            existing_tail_num = extract_tail_number(r['title'])
            if new_tail_num is not None and existing_tail_num is not None and new_tail_num != existing_tail_num:
                logger.info(f"  [重複回避(話数違い)] 新規末尾: {new_tail_num}, 既存末尾: {existing_tail_num} のため別作品と判定します (既存: {r['title'][:20]})")
                continue
                
            ratio = difflib.SequenceMatcher(None, norm_new, norm_existing).ratio()
            if ratio >= threshold:
                # あらすじ（description）の類似度セーフガード
                existing_desc = r['description']
                if new_desc and existing_desc:
                    desc_ratio = difflib.SequenceMatcher(None, str(new_desc), str(existing_desc)).ratio()
                    if desc_ratio < 0.30:
                        logger.info(f"  [重複回避(救済)] タイトル類似度 {ratio:.0%} ({r['title'][:20]}) ですが、あらすじ類似度 {desc_ratio:.0%} のため別作品と判定します")
                        continue
                return True, r['title'], ratio
    except Exception as e:
        logger.warning(f"  [重複チェック] DB読み込みエラー: {e}")
    return False, "", 0.0

def build_specs_html(release_date, author_detail, cast_info, series_name, page_count, fallback_author=None, site_label=None, is_voice=False):
    specs = []
    
    # 発売日の追加
    if release_date and isinstance(release_date, str) and len(release_date) >= 4:
        formatted_date = release_date[:10].replace("-", "/")
        specs.append(f"発売日: {formatted_date}")
        
    def clean_txt(t):
        if not t: return ""
        return t.replace("\r", "").replace("\n", "").replace("\xa0", " ").strip()

    # 著者詳細のパース（完全版 v21.2.5）
    # 全パターン対応:
    #   ① 日付・時刻ゴミの排除  ② VALID_ROLES バリデーション
    #   ③ ゴミ値（掲載終了等）の排除  ④ コロンなしは直前の役割を引き継ぎ
    #   ⑤ 同一クリエイターが複数役割を持つ場合に中黒(・)でまとめる（1人多役合体）
    _VALID_ROLES = frozenset(['著者', 'サークル', '出版社', 'レーベル', 'シナリオ', 'イラスト', '声優(CV)', '原作', 'WA'])
    _COMPANY_ROLES = frozenset(['出版社', 'レーベル', 'サークル'])
    _DATE_RE = re.compile(r'\d{4}[-/]\d{2}[-/]\d{2}')
    _TIME_RE = re.compile(r'\d{2}:\d{2}:\d{2}')
    _GARBAGE = ('掲載終了', '情報')
    _ROLE_ORDER = ['著者', 'シナリオ', 'イラスト', '原作', 'WA', 'サークル', '出版社', 'レーベル', '声優(CV)']

    if author_detail:
        author_detail = clean_txt(author_detail)
        _raw_parts = [p.strip() for p in author_detail.split(',') if p.strip()]
        _parsed_pairs = []
        _last_role = None

        for _part in _raw_parts:
            if _DATE_RE.search(_part) or _TIME_RE.search(_part):
                continue
            if ':' in _part:
                _role, _name = _part.split(':', 1)
                _role = _role.strip()
                _name = _name.strip()
                if _role not in _VALID_ROLES:
                    continue
                if any(_g in _name for _g in _GARBAGE):
                    continue
                if not _name:
                    continue
                _last_role = _role
                _parsed_pairs.append((_name, _role))
            else:
                _name = _part
                if not _name or any(_g in _name for _g in _GARBAGE):
                    continue
                _role = _last_role or '著者'
                _parsed_pairs.append((_name, _role))

        _name_to_roles = {}
        for _name, _role in _parsed_pairs:
            _name_to_roles.setdefault(_name, [])
            if _role not in _name_to_roles[_name]:
                _name_to_roles[_name].append(_role)

        _combined = {}
        for _name, _roles in _name_to_roles.items():
            if any(_r in _COMPANY_ROLES for _r in _roles):
                for _r in _roles:
                    _combined.setdefault(_r, []).append(_name)
            else:
                _sorted_roles = [_r for _r in _ROLE_ORDER if _r in _roles]
                if not _sorted_roles:
                    _sorted_roles = sorted(_roles)
                _role_key = '・'.join(_sorted_roles)
                _combined.setdefault(_role_key, []).append(_name)

        def _role_priority(_rk):
            for _idx, _r in enumerate(_ROLE_ORDER):
                if _r in _rk.split('・'):
                    return _idx
            return len(_ROLE_ORDER)

        for _rk in sorted(_combined.keys(), key=_role_priority):
            specs.append(f"{_rk}: {' / '.join(_combined[_rk])}")
    elif fallback_author:
        fallback_author = clean_txt(fallback_author)
        is_dlsite = site_label and "DLsite" in str(site_label)
        if is_dlsite and "/" in fallback_author:
            sub_parts = [p.strip() for p in fallback_author.split("/") if p.strip()]
            if len(sub_parts) >= 2:
                specs.append(f"レーベル: {sub_parts[0]}")
                specs.append(f"著者: {sub_parts[1]}")
            else:
                specs.append(f"サークル: {fallback_author}")
        else:
            if is_dlsite:
                specs.append(f"サークル: {fallback_author}")
            else:
                specs.append(f"著者: {fallback_author}")
    if cast_info:
        specs.append(f"声優(CV): {cast_info}")
    if page_count:
        try:
            pg_val = int(page_count)
            if pg_val > 0:
                if is_voice:
                    specs.append(f"{pg_val}本")
                else:
                    specs.append(f"{pg_val}P")
        except (ValueError, TypeError):
            pass
    if not specs:
        return ""
    specs_text = " ｜ ".join(specs)
    html = f"""<!-- NOVELOVE_SPECS_START -->
<div class="novelove-specs" style="background:#fafafa; border-top:1px solid #eee; border-bottom:1px solid #eee; padding:6px 10px; margin:12px 0; font-size:0.85em; color:#666; text-align:center; line-height:1.5;">
  {specs_text}
</div>
<!-- NOVELOVE_SPECS_END -->\n"""
    return html

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
    # v19.0.0: ボイスジャンルの作品はボイス関連NGワードをバイパス
    _current_genre = str(row['genre']) if 'genre' in row.keys() else ""
    if "voice_" in _current_genre:
        ng_patterns = ["動画", "English", "Chinese", "サンプル", "【動画】"]
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
    
    # スコア3以下は破棄 - v15.2: 高品質記事のみ投稿してサイト評価を保護
    if eval_score <= 3:
        logger.warning(f"  -> 内容が不十分（スコア{eval_score}点）のため執筆スキップ")
        cursor.execute("UPDATE novelove_posts SET status='excluded', last_error='low_score' WHERE product_id=?", (pid,))
        conn.commit()
        return False, f"low_score: {eval_score}"
        
    logger.info(f"  ✅ スコア基準クリア ({eval_score}点)。執筆を開始します。")

    img_url = row["image_url"] or ""
    # A+C方式: FIFUには軽量サムネ、記事本文には大きいURLを使う
    thumb_url = _get_thumbnail_url(img_url)

    # 取得時に完全にLovecalか判定されるため、URLによる強制置換フォールバックは廃止 (v15.4.1)
    _product_url_val = row["product_url"] or ""

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
        # 🌟 v14.5.1: DLsite用にpid/floorを常に渡す（非DLsiteでは無視される）
        "affiliate_url": generate_affiliate_url(site_label, _product_url_val,
                                                pid=pid,
                                                floor="home" if isinstance(site_raw, str) and "r18=0" in site_raw else ("bl" if "bl" in str(row["genre"]).lower() else "girls")),
        "image_url":     img_url,
        "thumb_url":     thumb_url,
        "release_date":  row["release_date"],
        "ai_tags":       row["ai_tags"],
        "desc_score":    eval_score,  # スコアを渡す
        "original_tags": row["original_tags"] if "original_tags" in row.keys() else "",
        "is_exclusive":  row["is_exclusive"] if "is_exclusive" in row.keys() else 0,
    }

    # v12.2.0: 統合DB・Fuzzy Matching重複チェック (旧24hガードレールを完全置換)
    is_dup, dup_title, dup_ratio = is_cross_db_duplicate(title, desc_str, pid)
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
    article_pattern = res.article_pattern or "A"  # v16.0.0: 使用HTMLパターン

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
        _normalized = {"DMM.com": "DMM", "DLsite": "DLsite", "Lovecal": "Lovecal"}
        _sn = _normalized.get(site_label, site_label)
        excl_tag = {"DLsite": "DLsite専売", "DMM": "DMM独占", "Lovecal": "らぶカル専売"}.get(_sn, "")
        if not excl_tag and "らぶカル" in str(site_label):
            excl_tag = "らぶカル専売"
        if excl_tag and excl_tag not in final_ai_tags:
            final_ai_tags.append(excl_tag)

        # v20.0.5: 新規投稿時の専売タイトルプレフィックス＆専用バナー初期付与
        _sn_excl = _sn or ("Lovecal" if "らぶカル" in str(site_label) else "")
        excl_prefix = ""
        excl_banner = ""
        if _sn_excl == "DLsite":
            excl_prefix = "【DLsite専売】"
            excl_banner = (
                "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
                '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #7b1fa2, #e91e63); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(123, 31, 162, 0.2);">\n'
                "    【DLsite専売】 ここでしか読めない限定配信作品です！\n"
                "</div>\n"
                "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
            )
        elif _sn_excl == "Lovecal":
            excl_prefix = "【らぶカル専売】"
            excl_banner = (
                "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
                '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #ff5722, #ff9800); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(255, 87, 34, 0.2);">\n'
                "    【らぶカル専売】 ここでしか読めない限定配信作品です！\n"
                "</div>\n"
                "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
            )
        elif _sn_excl == "DMM":
            excl_prefix = "【DMM独占】"
            excl_banner = (
                "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
                '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #0d47a1, #29b6f6); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(13, 71, 161, 0.2);">\n'
                "    【DMM独占】 ここでしか読めない限定配信作品です！\n"
                "</div>\n"
                "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
            )
            
        if excl_prefix:
            wp_title = f"{excl_prefix}{wp_title}"
        if excl_banner:
            content = excl_banner + content

    # 🌟 SPEC TABLE AUTO INSERTION 🌟
    row_dict = dict(row)
    auth_det = row_dict.get("author_detail", "") or ""
    cast_inf = row_dict.get("cast_info", "") or ""
    ser_name = row_dict.get("series_name", "") or ""
    pg_count = row_dict.get("page_count", 0) or 0
    
    genre_str = row_dict.get("genre", "") or ""
    is_voice = "voice" in str(genre_str).lower()
    spec_html = build_specs_html(row["release_date"], auth_det, cast_inf, ser_name, pg_count, fallback_author=row["author"], site_label=site_label, is_voice=is_voice)
    if spec_html:
        # 二重挿入防止ガードレール
        content = re.sub(r'<!-- NOVELOVE_SPECS_START -->.*?<!-- NOVELOVE_SPECS_END -->\s*', '', content, flags=re.DOTALL)
        
        # 既存の「発売日：xxxx/xx/xx」の段落があれば削除（二重表示防止ガードレール）
        content = re.sub(r'<p style="text-align:\s*center;\s*color:\s*#666;\s*font-size:\s*0.9em;\s*margin-bottom:\s*10px;?">発売日：\d{4}[-/]\d{2}[-/]\d{2}</p>\s*', '', content)
        
        # アイキャッチ画像の段落の直後にスペック表を挿入
        img_match = re.search(r'(<p style="text-align:\s*center;\s*margin:\s*20px\s*0;?"><a[^>]*><img[^>]*></a></p>)', content)
        if img_match:
            pos = img_match.end()
            content = content[:pos] + "\n" + spec_html + content[pos:]
        else:
            # フォールバック: 最初の <h2> を探してその手前にスペック表を挿入
            h2_match = re.search(r'<h2[^>]*>', content)
            if h2_match:
                pos = h2_match.start()
                content = content[:pos] + spec_html + content[pos:]
    # 🌟 中間ステートの書き込み: WP投稿前に status='posting' へ更新 (v21.3.0)
    cursor.execute(
        "UPDATE novelove_posts SET status='posting' WHERE product_id=?", (pid,)
    )
    conn.commit()

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
        _normalized_labels = {"DMM.com": "DMM", "DLsite": "DLsite（がるまに）", "Lovecal": "らぶカル"}
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
            # 👇 ゲストタグの救済を追加
            for t in final_ai_tags:
                if t in _wp_tags_parts and t not in _allowed:
                    _allowed.append(t)
            _wp_tags_parts = _allowed
        # exclude_list フィルタ（post_to_wordpress L777-778 と同一）
        _exclude = ("BL", "TL", "コミック", "小説", "漫画", "BLコミック", "TLコミック", "BL同人", "TL同人", "商業BL", "同人BL", "商業TL", "同人TL", "商業BL小説", "商業TL小説")
        _wp_tags_parts = [_t for _t in _wp_tags_parts if _t not in _exclude]
        wp_tags_str = ",".join(_wp_tags_parts)
        # v11.4.0: ai_tags も最新版で上書き保存, 過去のエラー履歴（last_error）もクリア, desc_scoreも保存
        # v12.8.0: wp_tags も同時保存
        # v14.2.0: wp_post_id を書き戻すよう修正（wp_post_idが保存されない致命的バグを修正）
        # v14.6.0: site カラムも書き戻し（らぶカル等、投稿時に修正されたsiteがDBに反映されないバグを修正）
        cursor.execute(
            "UPDATE novelove_posts SET status='published', site=?, wp_post_id=?, wp_post_url=?, published_at=datetime('now', 'localtime'), reviewer=?, ai_tags=?, wp_tags=?, last_error=NULL, desc_score=?, article_pattern=? WHERE product_id=?",
            (site_raw, wp_post_id, link, rev_name, ai_tags_str, wp_tags_str, ai_score, article_pattern, pid)
        )
        conn.commit()

        
        # 統計取得 (v18.0.0: 統合DB1本から集計)
        _conn = db_connect(DB_FILE_UNIFIED)
        total_daily = _conn.execute("SELECT COUNT(*) FROM novelove_posts WHERE status='published' AND date(published_at) = date('now', 'localtime')").fetchone()[0]
        _conn.close()

        # v18.0.0: 統合DB1本から在庫カウント
        _c = db_connect(DB_FILE_UNIFIED)
        c_dmm     = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site LIKE 'DMM%'").fetchone()[0]
        c_lovecal = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site LIKE 'Lovecal%'").fetchone()[0]
        c_dl      = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND source_db='dlsite'").fetchone()[0]
        _c.close()
        inventory_list = [f"DMM {c_dmm}", f"らぶカル {c_lovecal}", f"DLsite {c_dl}"]
        inventory_str = " / ".join(inventory_list) + " 件"

        notify_discord(
            f"✅ **[{site_label}] [{_genre_label(row['genre'])}] 投稿成功！**\n"
            f"**タイトル**: {wp_title}\n"
            f"**統計**: 今日 {total_daily}件目 / スコア{ai_score} / パターン{article_pattern} / あらすじ{desc_c_len}字 / 記事{words}字 / ライター: {rev_name}\n"
            f"**投稿待ち在庫**: {inventory_str}\n"
            f"**URL**: {link}",
            username="ノベラブ通知くん"
        )
        logger.info(f"✅ 投稿成功！ URL: {link}")

        # v18.5.0: Bluesky投稿頻度制限（ハイブリッドフィルタ）+ 茉莉花SNS担当化
        # DLsite/FANZA/らぶカル: 専売(is_exclusive=1) かつ スコア5のみ投稿
        # DMM                : スコア5のみ投稿（専売条件なし）
        _is_high_volume_site = any(site_raw.startswith(s) for s in ("DLsite", "Lovecal"))
        _is_exclusive_val    = is_exclusive  # L658で定義済みの bool 変数を流用
        _bsky_ok = False
        if _is_high_volume_site:
            _bsky_ok = (ai_score >= 5 and _is_exclusive_val)
        else:
            _bsky_ok = (ai_score >= 5)

        if _bsky_ok:
            try:
                post_to_bluesky(
                    title=wp_title,
                    genre=row["genre"],
                    excerpt=row["description"] or "",
                    url=link,
                    wp_tags_str=wp_tags_str,
                    image_url=img_url,
                    is_r18=is_r18,
                )
            except Exception as _bsky_err:
                logger.error(f"🚨 Bluesky呼び出しで予期せぬエラー（続行）: {_bsky_err}")
        else:
            logger.info(f"  [Bluesky] スキップ (site={site_raw}, score={ai_score}, exclusive={_is_exclusive_val})")

        # v20.0.3: トップページキャッシュのクリア（バックグラウンド実行）
        # 旧: functions.php の transition_post_status フックで wp_site_cache を直接DELETEしていたが、
        #      device_url カラムにインデックスがなくフルスキャンで100秒超のDBロックが発生していたため廃止。
        # 新: 投稿成功後にkusanagiコマンドでキャッシュをクリアする（WordPress外で実行するためDBロックなし）。
        try:
            subprocess.Popen(
                "kusanagi bcache clear myblog && kusanagi fcache clear myblog",
                shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            logger.info("  [Cache] KUSANAGI bcache/fcache クリアをバックグラウンドで実行")
        except Exception as cache_err:
            logger.warning(f"  [Cache] キャッシュクリア失敗（続行）: {cache_err}")

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

    logger.info("Novelove エンジン v21.1.1 起動")
    init_db()

    # ランキングロックチェック (他プロセス排他チェックのみ)
    if os.path.exists(RANK_LOCK_FILE):
        mtime = os.path.getmtime(RANK_LOCK_FILE)
        if time.time() - mtime > 7200:
            logger.warning("🚨 ランキングロックが2時間を超えています。強制解除します。")
            release_lock(RANK_LOCK_FILE)
        else:
            logger.info("🕒 ランキング処理が実行中です。通常投稿はスキップします。")
            return

    # 原子的メインロック取得
    if not acquire_lock(MAIN_LOCK_FILE, stale_timeout=7200):
        logger.info("🕒 メイン処理は既に実行中です。終了します。")
        return

    try:
        _run_main_logic()
    finally:
        release_lock(MAIN_LOCK_FILE)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Novelove Auto Posting Tool")
    parser.add_argument("--ranking", action="store_true", help="Run the ranking generation workflow")
    parser.add_argument("--ranking-force-all", action="store_true", help="Generate/overwrite all 6 fixed-slug ranking articles regardless of weekday (v21.5.0 one-time seeding)")
    args = parser.parse_args()
    if args.ranking_force_all:
        process_ranking_articles(force_all=True)
    elif args.ranking:
        process_ranking_articles()
    else:
        main()
