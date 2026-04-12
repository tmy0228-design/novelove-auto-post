#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
novelove_core.py — Novelove インフラ・共通機能モジュール
==========================================================
【役割】
  データベース接続、ログ出力、Discord 通知、文字列整形など
  「一生触る必要のない機械的な処理」を集約する裏方モジュールです。
==========================================================
"""

import os
import re
import random
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
import requests
import datetime
from dotenv import load_dotenv

from novelove_soul import REVIEWERS

# === ArticleResult: generate_article の戻り値データクラス (v13.10.0) ===
# auto_post.py / nexus_rewrite.py で共通利用。13要素タプルからの移行。
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ArticleResult:
    wp_title:      Optional[str]  = None
    content:       Optional[str]  = None
    excerpt:       Optional[str]  = None
    seo_title:     Optional[str]  = None
    is_r18:        bool           = False
    status:        str            = "ok"
    model:         str            = ""
    level:         str            = "None"
    proc_time:     float          = 0.0
    word_count:    int            = 0
    reviewer_name: str            = ""
    ai_tags:       list           = field(default_factory=list)
    ai_score:      int            = 0


# === .env 読み込み（全モジュール共通、ここで1回だけ行う） ===
_env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(_env_path):
    load_dotenv(_env_path)
else:
    load_dotenv()

# === システム設定定数 ===
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
DB_FILE_FANZA   = os.path.join(SCRIPT_DIR, "novelove.db")
DB_FILE_DLSITE  = os.path.join(SCRIPT_DIR, "novelove_dlsite.db")
DB_FILE_DIGIKET = os.path.join(SCRIPT_DIR, "novelove_digiket.db")
LOG_FILE        = os.path.join(SCRIPT_DIR, "novelove.log")
MAIN_LOCK_FILE  = os.path.join(SCRIPT_DIR, "main.lock")
RANK_LOCK_FILE  = os.path.join(SCRIPT_DIR, "ranking.lock")
INDEX_FILE      = os.path.join(SCRIPT_DIR, "genre_index.txt")
EMERGENCY_LOCK_FILE = os.path.join(SCRIPT_DIR, "emergency_stop.lock")
WP_SITE_URL     = "https://novelove.jp"

# === 環境変数（一元管理） ===
DEEPSEEK_API_KEY      = os.environ.get("DEEPSEEK_API_KEY", "")
WP_USER               = os.environ.get("WP_USER", "")
WP_APP_PASSWORD       = os.environ.get("WP_APP_PASSWORD", "")
DMM_API_ID            = os.environ.get("DMM_API_ID", "")
DMM_AFFILIATE_API_ID  = os.environ.get("DMM_AFFILIATE_API_ID", "")
DMM_AFFILIATE_LINK_ID = os.environ.get("DMM_AFFILIATE_LINK_ID", "")
DLSITE_AFFILIATE_ID   = os.environ.get("DLSITE_AFFILIATE_ID", "novelove")
DIGIKET_AFFILIATE_ID  = os.environ.get("DIGIKET_AFFILIATE_ID", "novelove")
# ※ セキュリティ上、デフォルト値なし。未設定時は呼び出し元がエラー終了する。
SSH_PASS              = os.environ.get("SSH_PASS", "")
# === WP-CLI / サーバー環境依存パス（移転時はここの環境変数を更新するだけでOK）===
WP_PHP_PATH = os.environ.get("WP_PHP_PATH", "/opt/kusanagi/php/bin/php")
WP_CLI_PATH = os.environ.get("WP_CLI_PATH", "/opt/kusanagi/bin/wp")
WP_DOC_ROOT = os.environ.get("WP_DOC_ROOT", "/home/kusanagi/myblog/DocumentRoot")

# === 共通ヘッダー・UA ===
HEADERS = {"User-Agent": "Mozilla/5.0"}

# === ロガー設定 ===
logger = logging.getLogger("novelove")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    _fh = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
    _sh = logging.StreamHandler()
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    _fh.setFormatter(_fmt)
    _sh.setFormatter(_fmt)
    logger.addHandler(_fh)
    logger.addHandler(_sh)

# === 緊急停止（サーキットブレーカー） ===
def is_emergency_stop():
    """緊急停止中ならTrueを返す"""
    return os.path.exists(EMERGENCY_LOCK_FILE)

def trigger_emergency_stop(reason):
    """緊急停止を発動し、Discord通知を送る"""
    try:
        with open(EMERGENCY_LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat()}\n{reason}\n")
    except Exception:
        pass
    notify_discord(
        f"🚨 **緊急停止が発動しました**\n"
        f"**理由**: {reason}\n"
        f"**解除方法**: サーバーで以下を実行\n"
        f"`rm /home/kusanagi/scripts/emergency_stop.lock`",
        username="🚨 緊急停止通知"
    )
    logger.error(f"🚨 緊急停止発動: {reason}")

# === Discord 通知機能 ===
ERROR_LABELS = {
    "excluded_type": "種別除外(漫画/ボイス等)",
    "no_description": "あらすじ無し",
    "excluded_foreign": "海外作品除外",
    "excluded_male_target": "男性向け除外",
    "no_image": "画像なし",
    "no_desc_or_image": "説明/画像無し",
    "wp_post_failed": "WP投稿失敗",
    "duplicate": "重複(既出)",
    "duplicate_fuzzy": "重複(類似タイトル)",
    "fetch_failed": "取得失敗",
    "expired": "有効期限切れ",
    "inventory_full": "在庫上限超過",
    "low_score": "品質スコア不足",
    "thin_score3": "スコア3タグ不足",
    "content_block": "AI執筆ブロック",
    "image_missing": "画像無効(直前チェック)",
    "excluded_by_pre_filter": "事前キーワード除外",
    "ai_failed": "AI執筆失敗",
}

def notify_discord(message, username="ノベラブ通知くん", avatar_url=None):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url: return False
    payload = {"content": message, "username": username}
    if avatar_url: payload["avatar_url"] = avatar_url
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.error(f"Discord通知失敗: {e}")
        return False

# === ユーティリティ機能 ===
def _clean_description(text):
    if not text: return ""
    soft_pattern = r"(?m)^(?:販売日|公開日|配信予定日|ページ数|ファイル容量|連続再生時間|対応OS|動作環境|作品形式|品番).*[:：].*$"
    result = re.sub(soft_pattern, "", text)
    result = re.sub(r"<[^>]+>", "", result)
    result = re.sub(r"\n\s*\n", "\n", result)
    return result.strip()

def _get_reviewer_for_genre(genre):
    """
    ジャンルに合うレビュアーを返す。
    90%の確率で専門担当者を、10%の確率でゲスト（専門外）を選出。
    戻り値: (reviewer_dict, is_guest: bool)
    """
    specialists = [r for r in REVIEWERS if genre in r["genres"]]
    if not specialists:
        specialists = REVIEWERS
    if random.random() < 0.1:
        others = [r for r in REVIEWERS if r not in specialists]
        if others:
            return random.choice(others), True
    return random.choice(specialists), False

def _genre_label(genre, title=""):
    g_lower = str(genre).lower()
    if "novel" in g_lower:
        is_novel = True
    elif any(x in g_lower for x in ("comic", "manga", "doujin")):
        is_novel = False
    else:
        is_novel = False

    is_bl = "bl" in g_lower or "BL" in str(genre)
    if is_novel:
        return "BL小説" if is_bl else "TL小説"
    else:
        return "BL漫画" if is_bl else "TL漫画"

# === アフィリエイトボタンの共通スタイル ===
AFFILIATE_BUTTON_STYLE = (
    "display:block;width:300px;margin:0 auto;padding:18px 0;"
    "background:#ffebf2;"
    "color:#d81b60 !important;text-decoration:none !important;"
    "font-weight:bold;font-size:1.1em;border-radius:50px;"
    "box-shadow:0 4px 10px rgba(216,27,96,0.15);border:2px solid #ffcfdf !important;"
    "text-align:center;line-height:1;outline:none !important;"
)

def get_affiliate_button_html(url, label="作品の詳細を見る"):
    return (
        f'<div class="novelove-button-container" style="margin:35px 0;text-align:center;">'
        f'<a href="{url}" target="_blank" rel="noopener" style="{AFFILIATE_BUTTON_STYLE}">'
        f'{label}</a></div>'
    )

# === アフィリエイトURL生成（一元管理）===
# ASP側の仕様変更はここ一箇所を修正するだけで全サイトに反映される。
def generate_affiliate_url(site: str, product_url: str, **kwargs) -> str:
    """
    サイト別にアフィリエイトURLを生成して返す共通関数。
    
    Args:
        site:        "FANZA" | "DMM.com" | "DLsite" | "DigiKet"
        product_url: 商品ページURL（FANZA/DMM/DigiKet）または空文字（DLsite）
        **kwargs:
            pid   (str): 商品ID（DLsite必須）
            floor (str): フロア名（DLsite必須: "bl", "girls", "bl-pro"等）
    Returns:
        str: アフィリエイトURL（生成失敗時はproduct_urlをそのまま返す）
    """
    import urllib.parse
    try:
        if site == "DLsite":
            pid   = kwargs.get("pid", "")
            floor = kwargs.get("floor", "girls")
            aid   = DLSITE_AFFILIATE_ID
            return f"https://dlaf.jp/{floor}/dlaf/=/t/n/link/work/aid/{aid}/id/{pid}.html"

        if site == "DigiKet":
            url = product_url
            if DIGIKET_AFFILIATE_ID:
                if not url.endswith("/"): url += "/"
                url += f"AFID={DIGIKET_AFFILIATE_ID}/"
            return url

        # FANZA / DMM.com
        af_id      = DMM_AFFILIATE_LINK_ID or "novelove-001"
        ch_params  = "&ch=toolbar&ch_id=text"
        encoded    = urllib.parse.quote(product_url, safe="")
        
        # 🌟 Lovecal (らぶカル) のURLはFANZA仕様のアフィリンクだと死ぬためDMM用に強制置換
        if "lovecul.dmm.co.jp" in product_url:
            return f"https://al.dmm.com/?lurl={encoded}&af_id={af_id}{ch_params}"
            
        if site == "FANZA":
            return f"https://al.fanza.co.jp/?lurl={encoded}&af_id={af_id}{ch_params}"
        # DMM.com
        return f"https://al.dmm.com/?lurl={encoded}&af_id={af_id}{ch_params}"

    except Exception:
        return product_url  # フォールバック: 元のURLをそのまま返す


def get_db_path(site_raw):
    site_str = str(site_raw)
    if "DLsite" in site_str: return DB_FILE_DLSITE
    if "DigiKet" in site_str: return DB_FILE_DIGIKET
    return DB_FILE_FANZA

def db_connect(path, read_only=False):
    """
    SQLite 接続を取得する共通関数。
    - WALモード: 読み取りと書き込みの並行処理を許可し、ロック頻度を大幅に削減。
    - timeout=60: cron同士の衝突時に60秒まで待機し、クラッシュせず再試行する。
    - busy_timeout=60000: OSレベルのロック待機を60秒に設定（timeout と二重で保護）。
    - isolation_level="IMMEDIATE": BEGIN IMMEDIATE で書き込みロックを即時予約し、
      複数のライタープロセスが同時に走る際のデッドロックを防止する。
    - read_only=True: ダッシュボード閲覧専用。書き込みロックを取得しないため
      バッチ処理への影響がゼロ（uri=True で ?mode=ro を指定）。
    """
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    else:
        conn = sqlite3.connect(path, timeout=60, isolation_level="IMMEDIATE")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=60000;")
    return conn

def calculate_local_priority(title: str, desc: str, tags: str = "", original_tags: str = "", release_date_raw: str = "") -> int:
    """
    APIコストゼロで、面白そうな記事の期待値を算出する（仮スコア計算）。
    ダッシュボード等の「期待値スコア(desc_score)」専用。
    """
    score = 0
    title_str = title or ""
    desc_str = desc or ""
    tags_str = tags or ""
    original_tags_str = original_tags or ""
    release_date_str = release_date_raw or ""
    
    full_text = f"{title_str} {desc_str} {tags_str} {original_tags_str}"
    
    # 1. 発売日ボーナス（段階的減衰）
    if release_date_str:
        try:
            # YYYY-MM-DD or YYYY/MM/DD 形式をパース
            rd_clean = release_date_str.strip().replace("/", "-")[:10]
            release_dt = datetime.datetime.strptime(rd_clean, "%Y-%m-%d").date()
            today_dt = datetime.datetime.now().date()
            days_ago = (today_dt - release_dt).days
            if days_ago == 0:
                score += 50   # 当日発売
            elif days_ago == 1:
                score += 30   # 昨日
            elif days_ago == 2:
                score += 15   # 2日前
            elif days_ago == 3:
                score += 5    # 3日前
        except (ValueError, TypeError):
            pass

    # 2. 文字数情報量（100〜600がスイートスポット）
    desc_len = len(desc_str.strip())
    if desc_len < 100:
        score += 0
    elif 100 <= desc_len < 200:
        score += 5
    elif 200 <= desc_len <= 600:
        score += 10
    else:
        score += 8

    # 3. パワーワード加点（需要高タグ）
    power_words = ["溺愛", "ヤンデレ", "スパダリ", "オメガバース", "執着", "独占欲", "幼なじみ", "NTR", "身分差", "再会", "契約結婚", "一途", "初恋"]
    for pw in power_words:
        if pw in full_text:
            score += 2

    # 4. ノイズワード減点
    noise_words = ["セール", "まとめ買い", "大幅値下げ", "体験版", "値下げ", "半額", "期間限定"]
    for nw in noise_words:
        if nw in full_text:
            score -= 5
            
    return score

def init_db():
    for db_path in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
        conn = db_connect(db_path)
        c = conn.cursor()
        # v11.4.11: CURRENT_TIMESTAMP はUTCのため、JST(localtime)を明示的に指定
        c.execute('''CREATE TABLE IF NOT EXISTS novelove_posts (
            product_id    TEXT PRIMARY KEY,
            title         TEXT,
            author        TEXT DEFAULT '',
            genre         TEXT,
            site          TEXT DEFAULT 'FANZA',
            status        TEXT DEFAULT 'excluded',
            release_date  TEXT DEFAULT '',
            description   TEXT DEFAULT '',
            affiliate_url TEXT DEFAULT '',
            image_url     TEXT DEFAULT '',
            product_url   TEXT DEFAULT '',
            wp_post_url   TEXT DEFAULT '',
            last_error    TEXT DEFAULT '',
            inserted_at   TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            published_at  TIMESTAMP,
            post_type     TEXT DEFAULT 'regular',
            desc_score    INTEGER DEFAULT 0,
            ai_tags       TEXT DEFAULT '',
            reviewer      TEXT DEFAULT '',
            wp_tags       TEXT DEFAULT ''
        )''')
        for col, definition in [
            ("last_error",        "TEXT DEFAULT ''"),
            ("desc_score",        "INTEGER DEFAULT 0"),
            ("post_type",         "TEXT DEFAULT 'regular'"),
            ("site",              "TEXT DEFAULT ''"),
            ("ai_tags",           "TEXT DEFAULT ''"),
            ("reviewer",          "TEXT DEFAULT ''"),
            # === フェーズ2（Nexusダッシュボード）向けカラム ===
            ("sale_discount_rate", "INTEGER DEFAULT 0"),   # セール割引率（%）
            ("last_revived_at",    "TIMESTAMP DEFAULT NULL"), # 最後に蘇生処理をした日時
            ("revive_score",       "INTEGER DEFAULT 0"),   # 蘇生ポテンシャルスコア
            # === 専売タグ・公式属性タグ連携 ===
            ("original_tags",     "TEXT DEFAULT ''"),      # 公式属性タグ（カンマ区切り）
            ("is_exclusive",      "INTEGER DEFAULT 0"),    # 専売・独占フラグ（1=専売）
            # === 完成品タグキャッシュ (v12.8.0) ===
            ("wp_tags",           "TEXT DEFAULT ''"),      # 実際にWPへ送信した/送信予定の完成品タグ一覧
            # === リライトエンジン基盤 (v12.9.0) ===
            ("rewrite_count",     "INTEGER DEFAULT 0"),    # リライト回数
            ("is_desc_updated",   "INTEGER DEFAULT 0"),    # あらすじ更新検知フラグ
            # === あらすじ更新検知 (S4) ===
            ("prev_description",  "TEXT DEFAULT ''"),      # 更新前の旧あらすじ（差分ビュー用）
            # === Google Search Console 連携 (S5) ===
            ("gsc_indexed",       "INTEGER DEFAULT 0"),    # インデックス登録済みか（0/1）
            ("gsc_impressions",   "INTEGER DEFAULT 0"),    # 直近30日間の表示回数
            ("gsc_clicks",        "INTEGER DEFAULT 0"),    # 直近30日間のクリック数
            ("gsc_last_checked",  "TIMESTAMP DEFAULT NULL"),  # GSC最終チェック日時
            # === リライト日時追跡 (S6) ===
            ("last_rewritten_at", "TIMESTAMP DEFAULT NULL"),  # 最終リライト実行日時
        ]:
            try:
                c.execute(f"ALTER TABLE novelove_posts ADD COLUMN {col} {definition}")
            except Exception:
                pass
        conn.commit()
        conn.close()

# === インデックス管理 ===
def get_genre_index():
    try:
        if os.path.exists(INDEX_FILE):
            with open(INDEX_FILE, "r") as f:
                return int(f.read().strip())
    except:
        pass
    return 0

def save_genre_index(idx):
    try:
        with open(INDEX_FILE, "w") as f:
            f.write(str(idx))
    except:
        pass
