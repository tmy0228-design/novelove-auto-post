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

from novelove_soul import REVIEWERS

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

# === データベース管理 ===
def get_db_path(site_raw):
    site_str = str(site_raw)
    if "DLsite" in site_str: return DB_FILE_DLSITE
    if "DigiKet" in site_str: return DB_FILE_DIGIKET
    return DB_FILE_FANZA

def db_connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn

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
            reviewer      TEXT DEFAULT ''
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
