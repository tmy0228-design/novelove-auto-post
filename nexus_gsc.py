#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
nexus_gsc.py — Google Search Console 連携 (S5)
==========================================================
【役割】
  Google Search Console API でインデックス状態・表示回数・
  クリック数を取得し、「死に記事」を自動検知する。

【起動方法（Cron: 1日1回）】
  python nexus_gsc.py

【必要な環境変数/.env】
  GSC_SERVICE_ACCOUNT_JSON  ... サービスアカウントJSONファイルパス
  GSC_SITE_URL              ... GSCに登録したサイトURL
                                 例: sc-domain:novelove.jp
                                 または https://novelove.jp/
【必要なライブラリ】
  pip install google-auth google-auth-httplib2 google-api-python-client
==========================================================
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# --- 環境変数の読み込み ---
env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()

from novelove_core import (
    logger,
    DB_FILE_UNIFIED,
    db_connect, notify_discord,
)

# === 環境変数 ===
GSC_SERVICE_ACCOUNT_JSON = os.environ.get("GSC_SERVICE_ACCOUNT_JSON", "")
GSC_SITE_URL             = os.environ.get("GSC_SITE_URL", "")

# === 死に記事判定の閾値 ===
DEAD_ARTICLE_DAYS      = 30   # 公開後この日数を超えたら死に記事アラート対象

# === GSC インデックス確認: 安全装置 ===
INSPECT_DAYS_MIN       = 14   # 公開後最低この日数以上の記事を確認対象にする
INSPECT_RECHECK_DAYS   = 7    # 同一記事を再チェックするまでの最少日数
INSPECT_DAILY_LIMIT    = 600  # 1日の上限。Google URL Inspection API 無料枠の公式上限は600件/日

DEAD_LEVEL1_UNINDEXED  = True  # レベル1: 未インデックス
DEAD_LEVEL2_ZERO_IMPR  = True  # レベル2: インデックス済み・表示0
DEAD_LEVEL3_ZERO_CLICK = True  # レベル3: 表示あり・クリック0


# =====================================================================
# 1. GSC API クライアント生成
# =====================================================================
def _build_gsc_service():
    """Google Search Console API サービスオブジェクトを返す"""
    if not GSC_SERVICE_ACCOUNT_JSON:
        raise EnvironmentError(
            "GSC_SERVICE_ACCOUNT_JSON が未設定です。.envファイルを確認してください。"
        )
    if not os.path.exists(GSC_SERVICE_ACCOUNT_JSON):
        raise FileNotFoundError(
            f"サービスアカウントJSONが見つかりません: {GSC_SERVICE_ACCOUNT_JSON}"
        )

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
    creds = service_account.Credentials.from_service_account_file(
        GSC_SERVICE_ACCOUNT_JSON, scopes=SCOPES
    )
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


# =====================================================================
# 2. URL別クリック/表示数の取得（直近30日）
# =====================================================================
def fetch_gsc_url_data(service) -> dict:
    """
    GSC API の searchanalytics.query を使い、
    直近30日間の URL別（impressions, clicks）を一括取得し辞書で返す。
    戻り値: { url: {"impressions": int, "clicks": int}, ... }
    """
    if not GSC_SITE_URL:
        raise EnvironmentError("GSC_SITE_URL が未設定です。")

    today     = datetime.now(timezone.utc).date()
    start_dt  = today - timedelta(days=30)
    end_dt    = today - timedelta(days=1)  # 前日まで（当日はデータ未確定）

    body = {
        "startDate":  start_dt.isoformat(),
        "endDate":    end_dt.isoformat(),
        "dimensions": ["page"],
        "rowLimit":   25000,  # 最大件数
    }

    result = service.searchanalytics().query(
        siteUrl=GSC_SITE_URL, body=body
    ).execute()

    url_data = {}
    for row in result.get("rows", []):
        url   = row["keys"][0]
        url_data[url] = {
            "impressions": int(row.get("impressions", 0)),
            "clicks":      int(row.get("clicks", 0)),
        }
    logger.info(f"  [GSC] URL別データ取得完了: {len(url_data)}件")
    return url_data


# =====================================================================
# 3. インデックス状態の確認（URL Inspection API）
# =====================================================================
def check_indexed(service, url: str):
    """
    URL Inspection API で指定URLがGoogleにインデックスされているか返す。
    戻り値: True=インデックス済み / False=未インデックス / None=API失敗（判定不能）
    """
    try:
        result = service.urlInspection().index().inspect(
            body={"inspectionUrl": url, "siteUrl": GSC_SITE_URL}
        ).execute()
        verdict = (
            result.get("inspectionResult", {})
                  .get("indexStatusResult", {})
                  .get("verdict", "")
        )
        return verdict == "PASS"
    except Exception as e:
        logger.warning(f"    [GSC] URL Inspection 失敗 ({url}): {e}")
        return None  # 判定不能 → 分類・DB更新をスキップする


# =====================================================================
# 4. メイン処理：DBを更新 + 死に記事を判定
# =====================================================================
def run_gsc():
    """GSCデータを全DB記事に反映し、死に記事アラートをDiscordに通知する。"""
    logger.info("=" * 60)
    logger.info("🔍 GSC 死に記事検知バッチ開始")
    logger.info("=" * 60)

    # --- GSC サービス初期化 ---
    try:
        service = _build_gsc_service()
    except Exception as e:
        logger.error(f"❌ GSC サービス初期化失敗: {e}")
        notify_discord(
            f"❌ **GSC バッチ失敗** ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n{e}",
            username="🔍 GSC 監視"
        )
        return

    # --- URL別データを一括取得 ---
    try:
        url_data = fetch_gsc_url_data(service)
    except Exception as e:
        logger.error(f"❌ GSC データ取得失敗: {e}")
        notify_discord(
            f"❌ **GSC データ取得失敗** ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n{e}",
            username="🔍 GSC 監視"
        )
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    threshold_date      = (datetime.now() - timedelta(days=DEAD_ARTICLE_DAYS)).strftime("%Y-%m-%d")
    inspect_since_date  = (datetime.now() - timedelta(days=INSPECT_DAYS_MIN)).strftime("%Y-%m-%d")
    recheck_cutoff      = (datetime.now() - timedelta(days=INSPECT_RECHECK_DAYS)).strftime("%Y-%m-%d %H:%M:%S")

    dead_lv1 = []  # 未インデックス
    dead_lv2 = []  # 表示0
    dead_lv3 = []  # クリック0
    inspect_count = 0  # 今日の Inspection API 呼び出し数

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STEP 1: クリック数/表示数の更新（全公開記事・毎日・スキップなし）
    # searchanalytics.query は1回の呼び出しで全URLのデータを取得済みのため
    # APIの個別呼び出しは発生しない。よって7日スキップは不要。
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    try:
        conn = db_connect(DB_FILE_UNIFIED)
        conn.row_factory = sqlite3.Row
        all_rows = conn.execute(
            """SELECT product_id, wp_post_url, published_at, gsc_last_checked
               FROM novelove_posts
               WHERE status='published'
                 AND wp_post_url != '' AND wp_post_url IS NOT NULL"""
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"  [DB] 読み込みエラー: {e}")
        all_rows = []

    clicks_updated = 0
    for row in all_rows:
        pid = row["product_id"]
        url = row["wp_post_url"]

        url_slash    = url if url.endswith('/') else url + '/'
        url_no_slash = url.rstrip('/')
        gsc_info     = url_data.get(url_slash) or url_data.get(url_no_slash)

        impressions = gsc_info["impressions"] if gsc_info else 0
        clicks      = gsc_info["clicks"]      if gsc_info else 0

        # クリック実績がある記事は永久保護フラグを付与（殿堂入り）
        should_protect = 1 if clicks >= 1 else None
        try:
            conn2 = db_connect(DB_FILE_UNIFIED)
            conn2.execute(
                """UPDATE novelove_posts
                   SET gsc_impressions  = ?,
                       gsc_clicks       = ?
                   WHERE product_id = ?""",
                (impressions, clicks, pid)
            )
            if should_protect:
                conn2.execute(
                    "UPDATE novelove_posts SET is_protected = 1 WHERE product_id = ? AND is_protected = 0",
                    (pid,)
                )
            conn2.commit()
            conn2.close()
            clicks_updated += 1
        except Exception as e:
            logger.error(f"  [DB] クリック数更新失敗 ({pid}): {e}")

    logger.info(f"  [GSC STEP1] クリック数/表示数を全件更新: {clicks_updated}件")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STEP 2: インデックス確認 + 死に記事判定（7日スキップを維持）
    # URL Inspection API は1件ずつ呼び出すため、1日600件の制限がある。
    # 7日以内に確認済みの記事はスキップして呼び出し数を節約する。
    #
    # 【絞り込み設計 v21.3.1】
    # 「gsc_indexed=1 かつ gsc_impressions>0」の記事は現在もGoogleに
    # 表示されている健全な記事のため、APIによる個別確認は不要。
    # 以下の3条件のいずれかに該当する「要確認記事」のみをループ対象にする:
    #   (A) gsc_indexed = 0        … まだインデックスされていない
    #   (B) gsc_impressions = 0    … 表示がゼロ（インデックス脱落の可能性）
    #   (C) gsc_last_checked IS NULL … 一度もチェックされていない新着記事
    # これにより記事数が何万件に増えても1日600件の上限を超えない設計を実現。
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    try:
        conn = db_connect(DB_FILE_UNIFIED)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT product_id, wp_post_url, published_at, gsc_last_checked
               FROM novelove_posts
               WHERE status='published'
                 AND wp_post_url != '' AND wp_post_url IS NOT NULL
                 AND published_at <= ?
                 AND (
                   gsc_indexed = 0
                   OR gsc_impressions = 0
                   OR gsc_last_checked IS NULL
                 )""",
            (inspect_since_date,)
        ).fetchall()
        conn.close()
        logger.info(f"  [GSC STEP2] インデックス確認対象: {len(rows)}件（健全な記事を除外済み）")
    except Exception as e:
        logger.warning(f"  [DB] 読み込みエラー: {e}")
        rows = []

    for row in rows:
        if inspect_count >= INSPECT_DAILY_LIMIT:
            logger.warning(f"  [GSC] 日次上限に達したため記事ループを退出")
            break

        pid = row["product_id"]
        url = row["wp_post_url"]
        last_checked = row["gsc_last_checked"]

        # ━━ 7日以内にインデックス確認済みの記事はスキップ ━━
        if last_checked and last_checked >= recheck_cutoff:
            logger.debug(f"  [GSC] {pid} — {INSPECT_RECHECK_DAYS}日以内に確認済みのためインデックスチェックをスキップ")
            continue

        url_slash    = url if url.endswith('/') else url + '/'
        url_no_slash = url.rstrip('/')
        gsc_info     = url_data.get(url_slash) or url_data.get(url_no_slash)

        impressions = gsc_info["impressions"] if gsc_info else 0
        clicks      = gsc_info["clicks"]      if gsc_info else 0

        # インデックス確認（表示が0のURL のみ Inspection API 呼び出し）
        if gsc_info is None or impressions == 0:
            indexed = check_indexed(service, url)
            inspect_count += 1
        else:
            indexed = True  # 表示があればインデックス済みとみなす

        # API判定不能（None）の場合はDB更新・分類をスキップ
        if indexed is None:
            logger.warning(f"  [GSC] {pid} — インデックス判定不能のためスキップ")
            continue

        # gsc_indexed と gsc_last_checked を更新（クリック数はSTEP1で更新済み）
        try:
            conn2 = db_connect(DB_FILE_UNIFIED)
            conn2.execute(
                """UPDATE novelove_posts
                   SET gsc_indexed      = ?,
                       gsc_last_checked = ?
                   WHERE product_id = ?""",
                (1 if indexed else 0, now_str, pid)
            )
            conn2.commit()
            conn2.close()
        except Exception as e:
            logger.error(f"  [DB] インデックス状態更新失敗 ({pid}): {e}")
            continue

        # 死に記事判定（公開30日以上の記事のみアラート対象）
        published_at_str = row["published_at"] if row["published_at"] else ""
        is_old_enough = published_at_str <= threshold_date

        if is_old_enough:
            if DEAD_LEVEL1_UNINDEXED and not indexed:
                dead_lv1.append({"pid": pid, "url": url})
            elif DEAD_LEVEL2_ZERO_IMPR and indexed and impressions == 0:
                dead_lv2.append({"pid": pid, "url": url})
            elif DEAD_LEVEL3_ZERO_CLICK and indexed and impressions > 0 and clicks == 0:
                dead_lv3.append({"pid": pid, "url": url})


    # --- Discord 通知 ---
    _send_discord_summary(dead_lv1, dead_lv2, dead_lv3)

    # --- v20.6.0: GSCクリック上位記事をWordPressに同期 ---
    sync_popular_to_wp()

    logger.info("=" * 60)
    logger.info("🏁 GSC 死に記事検知バッチ完了")
    logger.info("=" * 60)


# =====================================================================
# 5. Discord 日次サマリー
# =====================================================================
def _send_discord_summary(lv1: list, lv2: list, lv3: list):
    total = len(lv1) + len(lv2) + len(lv3)
    summary = (
        f"🔍 **[GSC 死に記事日次レポート]** "
        f"({datetime.now().strftime('%Y-%m-%d %H:%M')})\n"
        f"┣ 🔴 **Lv1 未インデックス**: {len(lv1)}件\n"
        f"┣ 🟡 **Lv2 表示0**: {len(lv2)}件\n"
        f"┣ 🟠 **Lv3 クリック0**: {len(lv3)}件\n"
        f"┗ 合計: {total}件"
    )

    # 詳細（最大5件ずつ）
    for level, items, label in [
        ("Lv1", lv1, "🔴 未インデックス"),
        ("Lv2", lv2, "🟡 表示0"),
        ("Lv3", lv3, "🟠 クリック0"),
    ]:
        if items:
            detail = f"\n**{label}（上位5件）**\n"
            for item in items[:5]:
                detail += f"  ・{item['pid']} {item['url']}\n"
            summary += detail

    notify_discord(summary, username="🔍 GSC 監視")
    logger.info(
        f"  [GSC] Discord通知完了: Lv1={len(lv1)} / Lv2={len(lv2)} / Lv3={len(lv3)}"
    )


# =====================================================================
# 6. GSCクリック上位記事のWordPress同期 (v20.6.0)
# =====================================================================
def sync_popular_to_wp():
    """GSCクリック数の上位10件のwp_post_idを取得し、
    WordPressのwp_optionsに 'novelove_popular_ids' として保存する。
    functions.php側でこのオプションを読み取り、注目作品セクションを表示する。
    """
    import subprocess, json
    try:
        conn = db_connect(DB_FILE_UNIFIED)
        cursor = conn.execute(
            "SELECT wp_post_id FROM novelove_posts "
            "WHERE status='published' AND gsc_clicks > 0 AND wp_post_id IS NOT NULL AND wp_post_id > 0 "
            "ORDER BY gsc_clicks DESC LIMIT 10"
        )
        ids = [row[0] for row in cursor.fetchall()]
        conn.close()

        if not ids:
            logger.info("  [Popular] GSCクリック実績のある記事が0件のため同期スキップ")
            return

        ids_json = json.dumps(ids)
        wp_path = "/home/kusanagi/myblog/DocumentRoot"
        # WP-CLIでwp_optionsに保存（autoload=yes で高速読み込み）
        result = subprocess.run(
            ["wp", "option", "update", "novelove_popular_ids", ids_json, f"--path={wp_path}", "--allow-root"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            logger.info(f"  [Popular] WordPress同期完了: {len(ids)}件のwp_post_idを保存 {ids}")
        else:
            logger.error(f"  [Popular] WordPress同期失敗: {result.stderr}")

    except Exception as e:
        logger.error(f"  [Popular] 同期エラー: {e}")


# =====================================================================
# 7. エントリーポイント
# =====================================================================
if __name__ == "__main__":
    run_gsc()

