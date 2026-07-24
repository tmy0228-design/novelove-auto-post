#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
novelove_fetcher.py — Novelove 新着取得・スクレイピング・フィルタリングモジュール
==========================================================
このモジュールは auto_post.py から分離された「データ取得」専任ファイルです。
各サイト（FANZA/DMM/DLsite）からのスクレイピング・API通信・
初期フィルタリング・DB格納（pendingストック）を担います。

★ 依存関係（一方通行ルール）:
    novelove_fetcher → novelove_core のみ OK
    novelove_fetcher → auto_post.py   は 禁止（循環参照になるため）
==========================================================
"""

import requests
import json
import urllib.parse
import time
import re
import html
import os
from bs4 import BeautifulSoup
from datetime import datetime

# 環境変数・.envの読み込みは novelove_core.py で一元管理
from novelove_core import (
    logger, HEADERS,
    DB_FILE_UNIFIED,
    _clean_description, calculate_local_priority,
    get_db_path, get_source_db, db_connect,
    trigger_emergency_stop, notify_discord,
    DMM_API_ID, DMM_AFFILIATE_API_ID,
    generate_affiliate_url,
    DEEPSEEK_API_KEY,
    parse_cast_names, extract_cast_from_author_detail,
)

# スクレイピング構造変化の検知閾値（連続N回で緊急停止）
SCRAPE_FAIL_THRESHOLD = 5

# === 取得対象ジャンル定義 ===
FETCH_TARGETS = [
    # 1. らぶカル_BL（R18 / らぶカル / マンガ）
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin_bl", "genre": "doujin_bl", "source_db": "lovecal", "label": "らぶカル_BL", "keyword": None},
    # 2. らぶカル_TL（R18 / らぶカル / マンガ）
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin_tl", "genre": "doujin_tl", "source_db": "lovecal", "label": "らぶカル_TL", "keyword": None},
    # 3. DLsite同人_BL小説（R18 / DLsite / 小説）
    {"site": "DLsite",  "service": None,     "floor": "bl",             "genre": "novel_bl",  "label": "DLsite同人_BL小説",   "keyword": None},
    # 4. DLsite同人_TL小説（R18 / DLsite / 小説）
    {"site": "DLsite",  "service": None,     "floor": "girls",          "genre": "novel_tl",  "label": "DLsite同人_TL小説",   "keyword": None},
    # 5. DMM_BL（全年齢 / DMM / マンガ）
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_bl",  "label": "DMM_BL",       "article": "category", "article_id": "66036", "keyword": None},
    # 6. DMM_TL（全年齢 / DMM / マンガ）
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_tl",  "label": "DMM_TL",       "article": "category", "article_id": "66060", "keyword": None},
    # 7. らぶカル_BLボイス（R18 / らぶカル / ボイス）
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin_bl", "genre": "voice_bl", "source_db": "lovecal", "label": "らぶカル_BLボイス", "keyword": "ボイス", "enabled": True},
    # 8. らぶカル_TLボイス（R18 / らぶカル / ボイス）
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin_tl", "genre": "voice_tl", "source_db": "lovecal", "label": "らぶカル_TLボイス", "keyword": "ボイス", "enabled": True},
    # 9. らぶカル同人_BL小説（R18 / らぶカル / 小説）
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin_bl", "genre": "novel_bl", "source_db": "lovecal", "label": "らぶカル同人_BL小説", "keyword": "ノベル"},
    # 10. らぶカル同人_TL小説（R18 / らぶカル / 小説）
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin_tl", "genre": "novel_tl", "source_db": "lovecal", "label": "らぶカル同人_TL小説", "keyword": "ノベル"},
    # 11. DLsite一般_BLボイス（全年齢 / DLsite一般 / ボイス）
    {"site": "DLsite",  "service": None,     "floor": "home",           "genre": "voice_bl",  "label": "DLsite一般_BLボイス", "keyword": None, "enabled": True},
    # 12. DLsite一般_TLボイス（全年齢 / DLsite一般 / ボイス）
    {"site": "DLsite",  "service": None,     "floor": "home",           "genre": "voice_tl",  "label": "DLsite一般_TLボイス", "keyword": None, "enabled": True},
    # 13. DLsite同人_BL（R18 / DLsite / マンガ）
    {"site": "DLsite",  "service": None,     "floor": "bl",             "genre": "doujin_bl", "label": "DLsite同人_BL",       "keyword": None},
    # 14. DLsite同人_TL（R18 / DLsite / マンガ）
    {"site": "DLsite",  "service": None,     "floor": "girls",          "genre": "doujin_tl", "label": "DLsite同人_TL",       "keyword": None},
    # 15. DMM_BL小説（全年齢 / DMM / 小説）
    {"site": "DMM.com", "service": "ebook",  "floor": "novel",          "genre": "novel_bl",  "label": "DMM_BL小説",          "article": "category", "article_id": "66042", "keyword": None},
    # 16. DMM_TL小説（全年齢 / DMM / 小説）
    {"site": "DMM.com", "service": "ebook",  "floor": "novel",          "genre": "novel_tl",  "label": "DMM_TL小説",          "article": "category", "article_id": "66064", "keyword": None},
    # 17. DLsite同人_BLボイス（R18 / DLsite / ボイス）
    {"site": "DLsite",  "service": None,     "floor": "bl",             "genre": "voice_bl", "label": "DLsite同人_BLボイス",  "keyword": None, "enabled": True},
    # 18. DLsite同人_TLボイス（R18 / DLsite / ボイス）
    {"site": "DLsite",  "service": None,     "floor": "girls",          "genre": "voice_tl", "label": "DLsite同人_TLボイス",  "keyword": None, "enabled": True},
    # 19. DLsite一般_BL（全年齢 / DLsite一般 / マンガ）
    {"site": "DLsite",  "service": None,     "floor": "home",           "genre": "doujin_bl", "label": "DLsite一般_BL",       "keyword": None, "enabled": True},
    # 20. DLsite一般_TL（全年齢 / DLsite一般 / マンガ）
    {"site": "DLsite",  "service": None,     "floor": "home",           "genre": "doujin_tl", "label": "DLsite一般_TL",       "keyword": None, "enabled": True},
    # 21. DLsite商業_BL小説（R18 / DLsite / 小説）
    {"site": "DLsite",  "service": None,     "floor": "bl-pro",         "genre": "novel_bl",  "label": "DLsite商業_BL小説",   "keyword": None},
    # 22. DLsite商業_TL小説（R18 / DLsite / 小説）
    {"site": "DLsite",  "service": None,     "floor": "girls-pro",      "genre": "novel_tl",  "label": "DLsite商業_TL小説",   "keyword": None},
    # 23. DLsite商業_BLボイス（R18 / DLsite / ボイス）
    {"site": "DLsite",  "service": None,     "floor": "bl-pro",         "genre": "voice_bl", "label": "DLsite商業_BLボイス",  "keyword": None, "enabled": True},
    # 24. DLsite商業_TLボイス（R18 / DLsite / ボイス）
    {"site": "DLsite",  "service": None,     "floor": "girls-pro",      "genre": "voice_tl", "label": "DLsite商業_TLボイス",  "keyword": None, "enabled": True},
    # 25. DLsite商業_BL（R18 / DLsite / マンガ）
    {"site": "DLsite",  "service": None,     "floor": "bl-pro",         "genre": "comic_bl",  "label": "DLsite商業_BL",       "keyword": None},
    # 26. DLsite商業_TL（R18 / DLsite / マンガ）
    {"site": "DLsite",  "service": None,     "floor": "girls-pro",      "genre": "comic_tl",  "label": "DLsite商業_TL",       "keyword": None},
    # 27. DLsite一般_BL小説（全年齢 / DLsite一般 / 小説）
    {"site": "DLsite",  "service": None,     "floor": "home",           "genre": "novel_bl",  "label": "DLsite一般_BL小説",   "keyword": None, "enabled": True},
    # 28. DLsite一般_TL小説（全年齢 / DLsite一般 / 小説）
    {"site": "DLsite",  "service": None,     "floor": "home",           "genre": "novel_tl",  "label": "DLsite一般_TL小説",   "keyword": None, "enabled": True},
    # 29. DLsiteがるまに_BLボイス（全年齢商業 / がるまに / ボイス）
    {"site": "DLsite",  "service": None,     "floor": "garumani",       "genre": "voice_bl",  "label": "DLsiteがるまに_BLボイス", "keyword": None, "enabled": True},
    # 30. DLsiteがるまに_TLボイス（全年齢商業 / がるまに / ボイス）
    {"site": "DLsite",  "service": None,     "floor": "garumani",       "genre": "voice_tl",  "label": "DLsiteがるまに_TLボイス", "keyword": None, "enabled": True},
]


# === 入力フィルター（3段階） ===
# DeepSeekが拒否しない一般的な性的表現は除外し、暴力・非同意系のみを残す
MASK_LIGHT_MAP = {
    "強姦": "無理やり関係を迫る", "レイプ": "無理やり関係を迫る",
    "陵辱": "辱め",
}
MASK_EXTRA_MAP = {
    "拷問": "激しい責め", "M奴隷": "快楽に身を委ねた存在",
}

def mask_input(text, level=0):
    if not text or level == 0:
        return text or ""
    result = text
    for word, replacement in MASK_LIGHT_MAP.items():
        result = result.replace(word, replacement)
    if level >= 2:
        for word, replacement in MASK_EXTRA_MAP.items():
            result = result.replace(word, replacement)
    return result


# === AI振り分けタグ ホワイトリスト ===
# C-4: 定義は novelove_soul.py へ移動。このファイルでも参照できるよう import する。
from novelove_soul import AI_TAG_WHITELIST  # noqa: E402（循環import回避のため位置はここ）

# v11.3.0: 「単話・分冊版」など内容の薄い作品を個別紹介から除外するヘルパー
# ※ランキング記事には適用しない。キーワードとページ数の両方が揃った場合のみ除外。
THIN_CONTENT_KEYWORDS = ["分冊版", "単話", "単話版", "【マイクロ】", "【プチ】"]


# === フィルタリング ヘルパー ===

def _is_r18_item(item, site=None):
    # 1. サイト・ブランドごとの絶対判定ルール
    if site == "DMM.com":
        return False  # DMM(一般)は100%全年齢
    if site in ("FANZA", "Lovecal"):
        return True   # FANZA/らぶカルは年齢確認ありの成人サイトのため100%R-18


    # 2. DLsiteのHTMLバッジ判定（_fetch_dlsite_itemsで取得したもの）
    if site == "DLsite":
        if "is_r18_badge" in item:
            return item["is_r18_badge"]
        return True # 万が一取得できなかった場合は安全のためR-18扱い

    # 3. フォールバック（原則ここには来ないが安全側に倒す）
    return True

def _extract_author(item):
    # 1. 直下フィールドの探索
    for field in ["article", "author", "writer", "artist"]:
        val = item.get(field)
        if val:
            if isinstance(val, list) and val:
                return val[0].get("name", "") if isinstance(val[0], dict) else str(val[0])
            if isinstance(val, dict): return val.get("name", "")
            if isinstance(val, str) and val.strip(): return val.strip()
            
    # 2. iteminfo 配下の探索
    iteminfo = item.get("iteminfo", {}) or {}
    for field in ["author", "writer", "artist", "maker"]:
        val = iteminfo.get(field)
        if val:
            if isinstance(val, list) and val:
                return val[0].get("name", "") if isinstance(val[0], dict) else str(val[0])
            if isinstance(val, dict): return val.get("name", "")
            if isinstance(val, str) and val.strip(): return val.strip()
    return ""

def _run_emergency_ai_extraction(product_url, site_type="DMM.com"):
    """
    【緊急AI自己修復機能】
    プログラムによるあらすじ抽出が完全に空振った場合（サイトの構造変更時等）に呼び出され、
    該当ページのHTMLから大枠のテキストを切り取ってAIに投げ、あらすじ本文と新クラス名を予測・修復させる。
    成功した場合はDiscordに通知を送り、プログラム側のクラス名更新を促す。
    """
    try:
        api_key = DEEPSEEK_API_KEY
        if not api_key:
            logger.warning("  [AI緊急修復] DEEPSEEK_API_KEY が未設定のためスキップ")
            return ""

        # 本番と同じセッション・ヘッダーでアクセス（ボット対策回避）
        session = _make_dmm_session()
        r = _fetch_with_retry(product_url, session=session,
                              headers={"User-Agent": "Mozilla/5.0", "Referer": "https://book.dmm.co.jp/"},
                              timeout=20, label="Emergency_AI")
        if not r or r.status_code != 200:
            return ""

        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, 'html.parser')

        for trash in soup(['script', 'style', 'header', 'footer', 'nav', 'aside', 'svg', 'img']):
            trash.decompose()

        body = soup.find('body')
        if not body:
            return ""

        # コスト削減のため最大8000文字にクリップ
        html_segment = str(body)[:8000]

        prompt = (
            "以下のHTMLはアダルトコンテンツ販売ページの一部ですが、あらすじ（商品説明）プログラムの抽出に失敗しました。\n"
            "この中から、作品のあらすじ本文を抽出し、さらにそれに最も近いCSSクラス名やID名を推測してください。\n"
            "準備中などのダミーテキストの場合は空文字にしてください。\n\n"
            "【出力形式（厳密なJSON）】\n"
            '{"description": "あらすじ本文（HTMLタグを含まない純粋なテキスト）", '
            '"guessed_class": "推測されるクラス名（例: .summary__txt など）"}\n\n'
            f"対象HTML:\n{html_segment}"
        )
        messages = [{"role": "user", "content": prompt}]
        # v17.5.0: DeepSeek V4 直接API
        api_url = "https://api.deepseek.com/chat/completions"
        api_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": "deepseek-chat", "messages": messages, "max_tokens": 500, "temperature": 0.1}

        # 最大3回リトライ
        for attempt in range(1, 4):
            try:
                res = requests.post(api_url, headers=api_headers, json=payload, timeout=40)
                if res.status_code == 200:
                    break
                logger.warning(f"  [AI緊急修復] API応答エラー (attempt {attempt}/3): status={res.status_code}")
                time.sleep(2 ** attempt)
            except Exception as e:
                logger.warning(f"  [AI緊急修復] API通信エラー (attempt {attempt}/3): {e}")
                time.sleep(2 ** attempt)
        else:
            return ""

        content = res.json()["choices"][0]["message"]["content"]
        data = json.loads(content)

        desc = data.get("description", "").strip()
        gc = data.get("guessed_class", "")

        if len(desc) < 50:
            return ""

        # Discord通知を送る
        notify_discord(
            f"⚠️ **[{site_type}] サイト構造変更を検知・AIが自己修復しました！**\n"
            f"URL: {product_url}\n"
            f"💡 推測される新しいクラス場所: `{gc}`\n"
            f"（プログラムの抽出機能にこのクラスを追加してください）",
            username="🚨 自己修復システム"
        )
        logger.info(f"  [AI緊急修復] 成功: {len(desc)}文字取得, 推測クラス={gc}")
        return desc
    except Exception as e:
        logger.warning(f"  [AI緊急修復] エラー: {e}")
        return ""

def _is_noise_content(title, desc=""):
    ng_words = [
        "簡体中文", "繁体中文", "繁體中文", "English", "韓国語版", "中国語",
        "简体中文", "翻訳台詞", "中文字幕", "korean", "한국어",
        "タイ語", "thai", "ภาษาไทย", "ベトナム語", "vietnamese", 
        "インドネシア語", "indonesian", "スペイン語", "spanish",
        "フランス語", "french", "ドイツ語", "german", "ロシア語", "russian"
    ]
    target_text = f"{title}_{desc}".lower()
    return any(word.lower() in target_text for word in ng_words)

def _is_thin_content(title, item=None, pages=None):
    """
    個別紹介記事から除外すべき「薄い作品（単話・分冊版）」かどうかを判定する。
    条件: 確実なキーワードが含まれる AND ページ数が 50ページ未満。
    ページ数が不明な場合はキーワードのみで判定（安全側に倒す）。
    ※ランキング記事には適用しない。
    """
    if not any(kw in title for kw in THIN_CONTENT_KEYWORDS):
        return False  # キーワードなし → 除外しない

    # 引数 pages が直接渡された場合（DLsite のスクレイピング結果など）
    if pages is not None:
        return pages < 50  # 50P 以上なら合冊版等として除外しない

    # DMM/FANZA: API レスポンスの "volume" フィールドを参照
    if item:
        vol = item.get("volume", "")
        volume_str = str(vol) if vol is not None else ""
        m = re.search(r"(\d+)", volume_str)
        if m:
            if int(m.group(1)) >= 50:
                return False  # 50P 以上なら除外しない（合冊版等）

    return True  # キーワードあり + ページ数が少ない（or 不明）→ 除外対象


# === 表示整形ユーティリティ ===

def format_author_detail(raw: str) -> str:
    """
    DB保存された「役割:名前」カンマ区切り文字列を表示用に整形する。
    同一人物が複数の役割を持つ場合、「著者・シナリオ・声優(CV):名前」のようにまとめる。
    DB保存データは変更しない（表示時にのみ使用）。

    例:
      入力: サークル:XX,著者:A,シナリオ:A,声優(CV):A
      出力: サークル:XX,著者・シナリオ・声優(CV):A
    """
    if not raw or ":" not in raw:
        return raw

    ROLE_ORDER = ["サークル", "著者", "出版社", "レーベル", "シナリオ", "イラスト", "原画", "声優(CV)", "翻訳"]

    entries = []
    for part in raw.split(","):
        part = part.strip()
        if ":" not in part:
            continue
        role, name = part.split(":", 1)
        role, name = role.strip(), name.strip()
        if role and name:
            entries.append((role, name))

    if not entries:
        return raw

    # サークルは先頭固定、それ以外は出現順を維持しながら同一人物の役割をまとめる
    result_parts = []
    name_to_roles = {}   # name -> [roles]
    name_first_pos = {}  # name -> result_partsの位置

    for role, name in entries:
        if role == "サークル":
            result_parts.append(f"{role}:{name}")

    for role, name in entries:
        if role == "サークル":
            continue
        if name not in name_to_roles:
            name_to_roles[name] = [role]
            name_first_pos[name] = len(result_parts)
            result_parts.append(None)  # プレースホルダー
        else:
            if role not in name_to_roles[name]:
                name_to_roles[name].append(role)

    for name, roles in name_to_roles.items():
        sorted_roles = sorted(roles, key=lambda r: ROLE_ORDER.index(r) if r in ROLE_ORDER else 99)
        result_parts[name_first_pos[name]] = "・".join(sorted_roles) + ":" + name

    return ",".join(result_parts)


# === HTTP リトライヘルパー ===

RETRY_STATUS_CODES = {429, 500, 502, 503, 504}  # 一時エラーとみなすHTTPステータス

def _fetch_with_retry(url, session=None, headers=None, params=None, timeout=15, max_retries=3, label=""):
    """
    一時エラー（502/429/503等）を自動リトライするシンプルなラッパー。
    全リトライ失敗時は None を返す（呼び出し元が failed 扱いにする）。
    session を指定しない場合は requests モジュールを直接使用する。
    """
    _requester = session if session else requests
    for attempt in range(1, max_retries + 1):
        try:
            r = _requester.get(url, headers=headers or HEADERS, params=params, timeout=timeout)
            if r.status_code in RETRY_STATUS_CODES:
                wait = 2 ** attempt  # 指数バックオフ: 2s → 4s → 8s
                logger.warning(f"  [リトライ {attempt}/{max_retries}] {label or url[:60]} status={r.status_code} → {wait}秒待機して再試行")
                time.sleep(wait)
                continue
            return r
        except Exception as e:
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(f"  [リトライ {attempt}/{max_retries}] {label or url[:60]} エラー: {e} → {wait}秒待機して再試行")
                time.sleep(wait)
            else:
                logger.error(f"  [リトライ失敗] {label or url[:60]} 全{max_retries}回失敗: {e}")
    return None


# === セッション / 画像チェック ===

def _make_dmm_session():
    session = requests.Session()
    for domain in [".dmm.co.jp", ".book.dmm.co.jp", "book.dmm.co.jp", ".dmm.co.jp", ".lovecul.dmm.co.jp"]:
        session.cookies.set("age_check_done", "1", domain=domain)
        session.cookies.set("ckcy", "1", domain=domain)
    return session

def _check_image_ok(image_url):
    if not image_url or not isinstance(image_url, str): return False
    low_url = image_url.lower()
    if any(p in low_url for p in ["now_printing", "no_image", "noimage", "comingsoon", "dummy", "common/img"]):
        return False
    session = _make_dmm_session()
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = session.head(image_url, headers=headers, timeout=10, allow_redirects=False)
        if r.status_code == 302: return False
        if r.status_code == 200: return True
    except Exception:
        pass
    try:
        r = session.get(image_url, headers=headers, timeout=10, stream=True)
        ok = (r.status_code == 200)
        r.close()
        return ok
    except Exception:
        return False


# === スクレイピング ===

def scrape_dlsite_description(url):
    try:
        r = _fetch_with_retry(url, headers=HEADERS, timeout=15, label="DLsite詳細")
        if r is None or r.status_code != 200: return "", "", False, "", "", "", 0
        text = r.text
        soup_pre = BeautifulSoup(text, 'html.parser')
        wg_links = [a.get("href", "") for a in soup_pre.select(".work_genre a")]
        has_mng = any("/work_type/MNG" in link for link in wg_links)
        has_nre = any("/work_type/NRE" in link for link in wg_links)  # ノベル
        has_tow = any("/work_type/TOW" in link for link in wg_links)  # テキスト・画像
        has_sou = any("/work_type/SOU" in link for link in wg_links)  # v19.0.0: ボイス
        if not has_mng and not has_nre and not has_tow and not has_sou:
            type_map = {"SOU": "ボイス", "NRE": "ノベル", "MNG": "マンガ",
                        "GME": "ゲーム", "MOV": "動画", "ANI": "アニメ", "ICG": "CG集"}
            detected = [name for code, name in type_map.items()
                        if any(f"/work_type/{code}" in link for link in wg_links)]
            logger.warning(f"[DLsite] 漫画・ノベル・ボイス以外の形式（{', '.join(detected) or '不明'}）のため除外: {url}")
            return "__EXCLUDED_TYPE__", "", False, "", "", "", 0
        lang_labels = [a.text.strip() for a in soup_pre.select(".work_genre a")]
        FOREIGN_LABELS = ["韓国語", "中国語", "繁體中文", "繁体中文", "简体中文", "English", "英語"]
        for lbl in lang_labels:
            if any(fl in lbl for fl in FOREIGN_LABELS):
                logger.warning(f"[DLsite] 外国語版ラベル（{lbl}）のため除外: {url}")
                return "__EXCLUDED_TYPE__", "", False, "", "", "", 0
        soup = BeautifulSoup(text, 'html.parser')
        
        # === 追加スペックの抽出 (2重テーブルスキャン: work_maker & work_outline) ===
        author_detail = ""
        cast_info = ""
        series_name = ""
        page_count = 0
        
        authors = []
        seen_roles = set()
        
        for table_id in ["#work_maker", "#work_outline"]:
            table = soup.select_one(table_id)
            if not table:
                continue
            for tr in table.select("tr"):
                th = tr.select_one("th")
                td = tr.select_one("td")
                if not th or not td:
                    continue
                th_text = th.get_text(strip=True)
                td_text = td.get_text(strip=True)
                
                # パース対象の見出し
                target_keys = ["サークル", "出版社", "著者", "作者", "レーベル", "ブランド", "シナリオ", "イラスト", "原画", "声優", "キャスト", "翻訳", "CV", "ページ数", "シリーズ名"]
                if any(k in th_text for k in target_keys):
                    links = [a.get_text(strip=True) for a in td.find_all("a") if a.get_text(strip=True)]
                    # UIボタンテキストを除去
                    links = [l for l in links if "フォロー" not in l and "お気に入り" not in l]
                    
                    val = ",".join(links) if links else td_text
                    
                    # ロール名を統一マッピング
                    role = th_text
                    if "声優" in th_text or "キャスト" in th_text:
                        role = "声優(CV)"
                    elif th_text == "サークル名":
                        role = "サークル"
                    elif th_text == "出版社名":
                        role = "出版社"
                    elif th_text == "作者":
                        role = "著者"
                    
                    if role == "声優(CV)":
                        cast_info = val
                    elif role == "シリーズ名":
                        series_name = val
                    elif "ページ数" in role:
                        m = re.search(r"(\d+)", val)
                        if m:
                            page_count = int(m.group(1))
                    else:
                        role_val_key = f"{role}:{val}"
                        if role_val_key not in seen_roles:
                            authors.append(role_val_key)
                            seen_roles.add(role_val_key)
                            
        author_detail = ",".join(authors)

        # === 属性タグ取得（ジャンル行の<a>タグ） ===
        attr_tags = []
        for th in soup.find_all('th'):
            if th.get_text(strip=True) == 'ジャンル':
                td = th.find_next_sibling('td')
                if td:
                    attr_tags = [a.get_text(strip=True) for a in td.find_all('a') if a.get_text(strip=True)]
                break
        # === 専売判定 ===
        is_exclusive = bool(
            soup.find(lambda tag: tag.has_attr('class') and 'type_exclusive' in tag.get('class', []))
            or soup.find(attrs={'title': '専売'})
        )
        tags_str = ','.join(attr_tags[:10])  # 最大10個
        for trash in soup.select('.work_outline, .work_parts_area.outline, .work_parts_area.chobit, .work_edition'):
            trash.decompose()
        container = soup.select_one('.work_parts_container')
        if container:
            t = container.get_text(separator="\n", strip=True)
            if "作品内容" in t:
                t = t.split("作品内容")[-1]
            if len(t) > 100: return t.strip(), tags_str, is_exclusive, author_detail, cast_info, series_name, page_count
        for h3 in soup.find_all(['h3', 'div'], string=re.compile(r'作品内容')):
            next_div = h3.find_next_sibling('div')
            if next_div:
                t = next_div.get_text(separator="\n", strip=True)
                if len(t) > 50: return t.strip(), tags_str, is_exclusive, author_detail, cast_info, series_name, page_count
        meta_desc = soup.select_one('meta[property="og:description"]')
        if meta_desc and meta_desc.get('content'):
            return meta_desc.get('content').strip(), tags_str, is_exclusive, author_detail, cast_info, series_name, page_count
        # 最終フォールバック（完全0文字ならAI起動）
        ai_desc = _run_emergency_ai_extraction(url, site_type="DLsite")
        if ai_desc:
            return ai_desc, tags_str, is_exclusive, author_detail, cast_info, series_name, page_count
        return "", tags_str, is_exclusive, author_detail, cast_info, series_name, page_count
    except Exception as e:
        logger.error(f"DLsiteスクレイピングエラー: {e}")
        return "", "", False, "", "", "", 0


def scrape_description(product_url, site="DMM.com", genre="", is_ranking=False):
    if not product_url: return "", ""
    if "dlsite" in str(product_url).lower():
        desc, _tags, _excl, _auth_det, _cast, _series, _pages = scrape_dlsite_description(product_url)
        return desc, _auth_det

    session = _make_dmm_session()
    _any_desc_found = False
    author_detail_extra = ""
    try:
        r = session.get(
            product_url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://book.dmm.co.jp/"},
            timeout=20
        )
        r.encoding = r.apparent_encoding
        text = r.text

        soup = BeautifulSoup(text, "html.parser")
        is_comic = False
        has_format_tag = False
        is_novel_target = "novel" in genre
        is_voice_target = "voice_" in genre
        for dt in soup.find_all("dt"):
            if "作品形式" in dt.text or "形式" in dt.text or "ジャンル" in dt.text:
                dd = dt.find_next_sibling("dd")
                if dd:
                    has_format_tag = True
                    fmt_text = dd.text.strip()
                    if "コミック" in fmt_text or "劇画" in fmt_text or "マンガ" in fmt_text:
                        is_comic = True
                    break
        is_ranking_final = is_ranking or (genre in ("BL", "TL"))
        if has_format_tag and not is_comic and not is_novel_target and not is_voice_target and not is_ranking_final:
            logger.warning(f"[DMM] マンガ以外の形式のため除外: {product_url}")
            return "__EXCLUDED_TYPE__", ""
        page_title_tag = soup.find("title")
        page_title_str = page_title_tag.text if page_title_tag else ""
        FOREIGN_TITLE_PATTERNS = [
            "韓国語版", "한국어", "繁体中文", "繁體中文", "简体中文", "簡体中文",
            "中国語版", "English version", "English ver"
        ]
        bracket_contents = re.findall(r'[【\[\（\(]([^】\]\）\)]+)[】\]\開\(]', page_title_str)
        for bc in bracket_contents:
            if any(fp in bc for fp in FOREIGN_TITLE_PATTERNS):
                logger.warning(f"[DMM] 外国語版タイトルパターン（{bc}）のため除外: {product_url}")
                return "__EXCLUDED_TYPE__", ""
        if any(kw in text for kw in ["カテゴリー</th><td>写真集", "カテゴリー</th><td>グラビア", "カテゴリー</th><td>文芸・小説", "カテゴリー</th><td>ライトノベル"]):
            logger.warning(f"[DMM] 禁止カテゴリーを検知: {product_url}")
            return "__EXCLUDED_TYPE__", ""

        # === らぶカル/DMM 同人追加情報のスクレイピング抽出 ===
        extra_authors = []
        for item in soup.select(".productInformation__item"):
            title_el = item.select_one(".informationList__ttl")
            txt_el = item.select_one(".informationList__txt")
            if title_el and txt_el:
                key = title_el.get_text(strip=True)
                val = txt_el.get_text(strip=True)
                if key in ["作者", "シナリオ", "イラスト", "声優", "キャスト"]:
                    role = "著者" if key == "作者" else key
                    if "声優" in key or "キャスト" in key:
                        role = "声優(CV)"
                    extra_authors.append(f"{role}:{val}")
        if extra_authors:
            author_detail_extra = ",".join(extra_authors)

        # JSON-LD
        ld_desc = ""
        for ld_match in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL):
            try:
                ld_data = json.loads(ld_match.group(1))
                items = ld_data if isinstance(ld_data, list) else [ld_data]
                for item in items:
                    if isinstance(item, dict) and "description" in item and isinstance(item["description"], str):
                        if len(item["description"]) > len(ld_desc):
                            ld_desc = item["description"]
            except Exception:
                pass

        # HTML
        html_desc = ""
        for selector in [".summary__txt", ".mg-b20", ".common-description", ".product-description__text"]:
            el = soup.select_one(selector)
            if el:
                t = el.get_text(separator="\n", strip=True)
                if len(t) > len(html_desc):
                    html_desc = t

        best_desc = ld_desc if len(ld_desc) > len(html_desc) else html_desc
        _any_desc_found = bool(ld_desc) or bool(html_desc)

        if len(best_desc) < 150 and best_desc.rstrip().endswith(("…", "...")):
            logger.warning(f"  [省略検知] 取得テキストが省略文のみ({len(best_desc)}文字): {product_url}")
            best_desc = ""

        if len(best_desc) > 50:
            return best_desc.strip(), author_detail_extra

        ai_desc = _run_emergency_ai_extraction(product_url, site_type="DMM.com")
        if ai_desc:
            return ai_desc, author_detail_extra

    except Exception as e:
        logger.warning(f"スクレイピング失敗 ({product_url}): {e}")
    
    final_desc = "__DESC_TOO_SHORT__" if _any_desc_found else ""
    return final_desc, author_detail_extra


# === 新着取得（API/スクレイピング） ===

def _fetch_dlsite_items(target):
    floor = target.get("floor", "girls")
    genre = target.get("genre", "")
    is_novel = genre in ("novel_bl", "novel_tl")
    is_voice = "voice_" in genre  # v19.0.0: ボイスジャンル判定
    is_bl = "bl" in genre.lower()
    work_type = "SOU" if is_voice else ("NRE" if is_novel else "MNG")
    # v21.5.7: DLsiteの形式絞り込み
    # - R-18の `/new/=/work_type/X/genre/all/` は形式を無視し同一一覧を返すため使用禁止。
    #   `/{floor}/fsr/=/language/jp/work_type[0]/{MNG|NRE|SOU}/order/release_d/` を使う。
    # - homeの `work_type_category[0]/MNG|SOU` も効かない。漫画は comic、小説は novel、
    #   ボイスは `work_type[0]/SOU` を使う。
    # - homeの sex_category だけでは BL/TL が分離されないため、詳細ページで
    #   BL=ボーイズラブ / TL=乙女向け を要求する。
    if floor == "home":
        sex = (
            "sex_category[0]/female/sex_category[1]/gay/"
            if is_bl else
            "sex_category[0]/female/"
        )
        if is_novel:
            type_q = "work_type_category[0]/novel"
        elif is_voice:
            type_q = "work_type[0]/SOU"
        else:
            type_q = "work_type_category[0]/comic"
        url = f"https://www.dlsite.com/home/fsr/=/language/jp/{sex}{type_q}/order/release_d/"
    elif floor == "garumani":
        # 全年齢商業ボイス。一覧は is_bl/is_tl で分離し、詳細で再確認する。
        bltl = "is_bl/1/" if is_bl else "is_tl/1/"
        url = f"https://www.dlsite.com/garumani/fsr/=/language/jp/work_type[0]/SOU/{bltl}order/release_d/"
    else:
        url = f"https://www.dlsite.com/{floor}/fsr/=/language/jp/work_type[0]/{work_type}/order/release_d/"
    items = []
    VOICE_KEYWORDS = ["ボイス", "音声", "ASMR", "CV.", "CV:", "cv.", "cv:", "シチュエーションCD",
                      "バイノーラル", "ドラマCD", "全年齢ボイス", "簡体中文版", "繁体中文版",
                      "繁體中文版", "English", "韓国語版", "中国語", "音楽", "サウンドトラック", "音声作品"]
    # v19.0.0: ボイスターゲット時は外国語版のみスキップ（ボイスキーワードはバイパス）
    FOREIGN_KEYWORDS = ["簡体中文版", "繁体中文版", "繁體中文版", "English", "韓国語版", "中国語"]
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = _fetch_with_retry(url, headers=headers, timeout=20, label=f"DLsite新着({work_type})")
        if r is None: return items
        soup = BeautifulSoup(r.text, "html.parser")
        # fsr 一覧は search_result。旧new一覧用セレクタはフォールバックのみ。
        works = soup.select(".search_result_img_box_inner") or soup.select(".n_worklist_item")
        for work in works[:20]:
            title_tag = work.select_one(".work_name a")
            if not title_tag: continue
            title_text = title_tag.text.strip()
            category_tag = work.select_one(".work_category")
            category_text = category_tag.text.strip() if category_tag else ""
            if is_voice:
                # ボイスターゲット: 外国語版のみスキップ
                skip_keywords = FOREIGN_KEYWORDS
            elif is_novel:
                skip_keywords = VOICE_KEYWORDS
            else:
                skip_keywords = VOICE_KEYWORDS + ["ノベル", "小説", "実用"]
            if any(kw in (title_text + category_text) for kw in skip_keywords):
                logger.info(f"[DLsite] 種別フィルターによりスキップ: {title_text[:40]}")
                continue
            detail_url = title_tag.get("href")
            pid = detail_url.rstrip("/").split("/")[-1].replace(".html", "")
            if not pid: continue
            image_url = ""
            is_r18_badge = False
            dr_wg_links = []
            try:
                dr = _fetch_with_retry(detail_url, headers=headers, timeout=10, label="DLsite詳細(形式判定)")
                if dr is None:
                    logger.info(f"  [DLsite] 詳細ページ取得失敗のためスキップ: {title_text[:30]}")
                    continue
                dsoup = BeautifulSoup(dr.text, "html.parser")
                # garumani は SOU が #work_outline 側に出ることがあるため両方見る
                dr_wg_links = [a.get("href", "") for a in dsoup.select(".work_genre a, #work_outline a")]
                if is_voice:
                    # v19.0.0: ボイスターゲットはSOUバッジで判定
                    valid_badge = any("/work_type/SOU" in link for link in dr_wg_links)
                elif is_novel:
                    valid_badge = any("/work_type/NRE" in link or "/work_type/TOW" in link
                                      for link in dr_wg_links)
                else:
                    valid_badge = any("/work_type/MNG" in link for link in dr_wg_links)
                if not valid_badge:
                    logger.info(f"  [DLsite] 期待する形式バッジなしのためスキップ: {title_text[:30]}")
                    continue

                # v21.5.7 / v21.7.12: home・garumani は一覧のBL/TLが不安定なため詳細ラベルで再判定
                if floor in ("home", "garumani"):
                    sex_blob = " ".join(
                        a.get_text(strip=True)
                        for a in dsoup.select("#work_outline a, .work_genre a")
                    )
                    if is_bl:
                        if "ボーイズラブ" not in sex_blob and "ゲイ" not in sex_blob:
                            logger.info(f"  [DLsite] {floor} BL対象外（ボーイズラブ/ゲイなし）: {title_text[:30]}")
                            continue
                    else:
                        if "乙女向け" not in sex_blob or "ボーイズラブ" in sex_blob:
                            logger.info(f"  [DLsite] {floor} TL対象外（乙女向け以外）: {title_text[:30]}")
                            continue

                og_img = dsoup.select_one('meta[property="og:image"]')
                if og_img: image_url = og_img.get("content", "")

                # v18.4.0: DLsiteのR-18バッジ(icon_ADL)の有無を取得して判定フラグとして保持
                is_r18_badge = bool(dsoup.select_one(".icon_ADL"))

                # v11.3.1: 審査前に「薄い作品（単話・分冊版）」を除外ｗ
                if any(kw in title_text for kw in THIN_CONTENT_KEYWORDS):
                    dlsite_pages = None
                    for tr in dsoup.select("#work_outline tr"):
                        th = tr.select_one("th")
                        td = tr.select_one("td")
                        if th and td and "ページ数" in th.text:
                            m_p = re.search(r"(\d+)", td.text)
                            if m_p:
                                dlsite_pages = int(m_p.group(1))
                            break
                    if dlsite_pages is None:
                        img_count = dsoup.select_one(".work_img_count")
                        if img_count:
                            m_p = re.search(r"(\d+)", img_count.text)
                            if m_p:
                                dlsite_pages = int(m_p.group(1))
                    if _is_thin_content(title_text, pages=dlsite_pages):
                        logger.info(f"  [DLsite 薄いコンテンツ除外] {title_text[:40]} ({dlsite_pages}P)")
                        continue
            except Exception as e:
                logger.warning(f"  [DLsite] 詳細判定失敗のためスキップ: {title_text[:30]} ({e})")
                continue
            if not image_url:
                img_tag = work.select_one("img")
                if img_tag:
                    image_url = img_tag.get("src") or img_tag.get("data-src") or ""
            if image_url.startswith("//"): image_url = "https:" + image_url
            if "sam.jpg" in image_url: image_url = image_url.replace("sam.jpg", "main.jpg")
            items.append({
                "content_id": pid, "title": title_text, "URL": detail_url,
                "imageURL": {"large": image_url},
                "article": [{"name": work.select_one(".maker_name").text.strip()}] if work.select_one(".maker_name") else [],
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "dr_wg_links": dr_wg_links,
                "is_r18_badge": is_r18_badge
            })
            time.sleep(1)
    except Exception as e:
        logger.error(f"DLsite取得エラー: {e}")
    return items

def fetch_and_stock_all():
    """
    v11.4.0: 全ジャンルの新着を取得。スクリプトフィルタのみでpending保存。
    AI審査は投稿時に一括で行う（_check_desc_ok は呼ばない）。
    """
    failed_titles = []
    for target in FETCH_TARGETS:
        # v19.0.0: enabledフラグがFalseのターゲットはスキップ（ボイス等の段階的有効化用）
        if not target.get("enabled", True):
            logger.info(f"  [{target.get('label', '?')}] enabled=False のためスキップ")
            continue
        site = target.get("site", "DMM.com")
        # v15.5.1: 通知・ログ表示用のサイト名（APIに渡す site とは別に管理）
        _is_lovecal_target = target.get("floor") in ("digital_doujin_bl", "digital_doujin_tl") or "らぶカル" in target.get("label", "")
        disp_site = "らぶカル" if _is_lovecal_target else site

        db_path = get_db_path(site)
        api_items = []
        if site == "DLsite":
            logger.info(f"--- [新着取得] {disp_site} ({target['label']}) ---")
            api_items = _fetch_dlsite_items(target)
        else:
            logger.info(f"--- [新着取得] {disp_site} ({target['label']}) ---")
            params = {
                "api_id": DMM_API_ID, "affiliate_id": DMM_AFFILIATE_API_ID,
                "site": site, "service": target["service"], "floor": target["floor"],
                "hits": 50, "sort": "date", "output": "json",
            }
            if target.get("keyword"): params["keyword"] = target["keyword"]
            if target.get("article") and target.get("article_id"):
                params["article"] = target["article"]
                params["article_id"] = target["article_id"]
            try:
                r = _fetch_with_retry(
                    "https://api.dmm.com/affiliate/v3/ItemList",
                    headers=HEADERS, params=params, timeout=15, label=f"DMM API/{target['label']}"
                )
                if r is None:
                    logger.warning(f"  [スキップ] {disp_site}/{target['label']}: DMM APIへの接続が失敗しました（次回フェッチで再試行）")
                    continue
                api_items = r.json().get("result", {}).get("items", [])
            except Exception as e:
                logger.error(f"API エラー ({disp_site}/{target['label']}): {e}")
                continue
        if not api_items:
            logger.info(f"  -> 新着なし")
            continue
        logger.info(f"  -> {len(api_items)} 件取得")
        conn = db_connect(db_path)
        c = conn.cursor()
        added = 0
        scraped_data = []
        for item in api_items:
            pid = item.get("content_id")
            if not pid: continue
            if c.execute("SELECT 1 FROM novelove_posts WHERE product_id=?", (pid,)).fetchone():
                continue
            p_url = item.get("URL") or item.get("url") or ""
            if not p_url: continue
            title_str = item.get("title", "")
            # ボイス・ASMR作品の除外（らぶカル等のBL/TLフロアに混在するため）
            # v19.0.0: ボイスターゲット時はこのフィルタをバイパス
            _img_large = item.get("imageURL", {}).get("large", "")
            is_voice_target = "voice_" in target.get("genre", "")
            if "/voice/" in _img_large and not is_voice_target:
                logger.info(f"  [ボイス作品除外] 画像URLにvoiceパスを検出: {title_str[:40]}")
                continue
            if _is_thin_content(title_str, item):
                logger.info(f"  [薄いコンテンツ除外] {title_str[:40]}")
                continue
            if site == "DLsite":
                desc, dl_tags_str, dl_is_exclusive, dl_auth_det, dl_cast, dl_series, dl_pages = scrape_dlsite_description(p_url)
                item["_original_tags"] = dl_tags_str
                item["_is_exclusive"] = 1 if dl_is_exclusive else 0
                item["_author_detail"] = dl_auth_det
                # v21.6.0: 共通パーサで正規化してから保存（表記ゆれ・区切りゆれ防止）
                item["_cast_info"] = ",".join(parse_cast_names(dl_cast))
                item["_series_name"] = dl_series
                item["_page_count"] = dl_pages
            else:
                desc, dmm_auth_det = scrape_description(p_url, site=site, genre=target["genre"])
                item["_original_tags"] = ""
                item["_is_exclusive"] = 0
                item["_author_detail"] = dmm_auth_det
                item["_cast_info"] = ""
                item["_series_name"] = ""
                item["_page_count"] = 0
            time.sleep(1.0)
            scraped_data.append((item, desc))


        scraped_data.sort(key=lambda x: len(x[1]) if x[1] else 0, reverse=True)

        scrape_fail_count = 0  # 構造変化検知用カウンター
        for item, desc in scraped_data:
            pid = item.get("content_id")
            p_url = item.get("URL") or item.get("url") or ""  # スコープ明示: ループ内で毎回再取得
            last_error = ""
            final_status = "excluded"
            final_score = 0
            ai_tags = []
            item_title = item.get("title", "")
            
            # FANZAジャンルタグ取得・独占判定 (v14.7.0: 優先順位計算前に移動)
            if site in ("FANZA", "DMM.com"):
                _fanza_noise = {"単行本", "マンガ誌", "アンソロジー", "雑誌", "モノクロ", "フルカラー", "GIGATOON", "単話", "無料作品", "成人向け", "全年齢向け", "男性向け", "女性向け", "乙女向け"}
                _item_genres = item.get("iteminfo", {}).get("genre", []) or []
                _genre_names = [g.get("name", "") if isinstance(g, dict) else str(g) for g in _item_genres]
                _fanza_tags = [g for g in _genre_names if g and g not in _fanza_noise]
                item["_original_tags"] = ",".join(_fanza_tags[:10])
                
                # 専売判定 - API統一ルール（全サイト共通）
                _has_excl = any(g in ('専売', '独占', '独占販売') for g in _genre_names)
                item["_is_exclusive"] = 1 if _has_excl else 0

                # HTMLスクレイピングですでに取得済みの作者情報があればそれをベースにする
                scraped_auth_detail = item.get("_author_detail", "")
                authors = [a.strip() for a in scraped_auth_detail.split(",") if a.strip()]
                seen_pairs = set(authors)
                
                # 一般作品（著者・作者・イラスト）
                iteminfo = item.get("iteminfo", {}) or {}
                service_code = str(item.get("service_code", "")).lower()
                floor_code = str(item.get("floor_code", "")).lower()
                is_doujin = "doujin" in service_code or "doujin" in floor_code

                for field in ["author", "writer", "artist"]:
                    vals = iteminfo.get(field, []) or []
                    for v in vals:
                        name = v.get("name", "") if isinstance(v, dict) else str(v)
                        role_label = {"author": "著者", "writer": "作者", "artist": "イラスト"}.get(field, "著者")
                        # 表記ゆれ統一
                        if role_label == "作者":
                            role_label = "著者"
                        pair = f"{role_label}:{name}"
                        if name and pair not in seen_pairs:
                            authors.append(pair)
                            seen_pairs.add(pair)
                
                # サークル・出版社 (maker)
                makers = iteminfo.get("maker", []) or []
                for m in makers:
                    name = m.get("name", "") if isinstance(m, dict) else str(m)
                    if name:
                        # 商業なら「出版社」、同人なら「サークル」としてマッピング
                        role_label = "サークル" if is_doujin else "出版社"
                        pair = f"{role_label}:{name}"
                        if pair not in seen_pairs:
                            authors.append(pair)
                            seen_pairs.add(pair)
                            
                # レーベル (label)
                labels = iteminfo.get("label", []) or []
                for l in labels:
                    name = l.get("name", "") if isinstance(l, dict) else str(l)
                    if name:
                        pair = f"レーベル:{name}"
                        if pair not in seen_pairs:
                            authors.append(pair)
                            seen_pairs.add(pair)
                            
                item["_author_detail"] = ",".join(authors) if authors else ""
                
                # 声優
                actresses = iteminfo.get("actress", []) or []
                casts = [act.get("name", "") for act in actresses if act.get("name")]
                if casts:
                    casts = parse_cast_names(",".join(casts))
                else:
                    # v21.6.0: APIのactressが空でも、スクレイピングで author_detail に
                    # 入った「声優(CV):〜」から回収する（らぶカルのcast_info欠落バグ修正）
                    casts = extract_cast_from_author_detail(item.get("_author_detail", ""))
                item["_cast_info"] = ",".join(casts) if casts else ""
                
                # シリーズ
                series_list = iteminfo.get("series", []) or []
                item["_series_name"] = series_list[0].get("name", "") if series_list else ""
                
                # ページ数 (同人作品のみ volume からページ数を抽出)
                service_code = str(item.get("service_code", "")).lower()
                floor_code = str(item.get("floor_code", "")).lower()
                is_doujin = "doujin" in service_code or "doujin" in floor_code
                
                volume = item.get("volume", "")
                page_count = 0
                if is_doujin and volume:
                    m = re.search(r"(\d+)", str(volume))
                    if m:
                        page_count = int(m.group(1))
                item["_page_count"] = page_count

            item_original_tags = item.get("_original_tags", "")
            _is_excl_bool = bool(item.get("_is_exclusive", 0))

            if desc == "__EXCLUDED_TYPE__":
                last_error = "excluded_type"
                desc = ""
            elif desc == "__DESC_TOO_SHORT__":
                # あらすじは存在するが文字数が少なすぎる商品（サイト構造変化ではない）
                last_error = "desc_too_short"
                desc = ""
            elif not desc:
                last_error = "no_description"
                failed_titles.append(item.get("title", "不明"))
                notify_discord(f"⚠️ [{disp_site}] スクレイピング失敗: {item.get('title','')[:40]}\nURL: {item.get('URL', '')}", username="スクレイピング監視")
            elif _is_noise_content(item.get("title", ""), desc):
                last_error = "excluded_foreign"
            else:
                image_url_tmp = item.get("imageURL", {}).get("large", "")
                if not _check_image_ok(image_url_tmp):
                    last_error = "no_image"
                else:
                    # v11.4.0: AI審査を廃止、スクリプトフィルタ通過で即pending
                    final_status = "pending"
                    final_score = calculate_local_priority(item_title, desc, original_tags=item_original_tags, release_date_raw=item.get("date", ""), is_exclusive=_is_excl_bool)


            # 構造変化検知: 画像なし or あらすじなし が連続したらスクレイピング異常
            if last_error in ("no_description", "no_image", "no_desc_or_image"):
                scrape_fail_count += 1
                if scrape_fail_count >= SCRAPE_FAIL_THRESHOLD:
                    trigger_emergency_stop(f"[{disp_site}/{target['label']}] スクレイピング異常検知: 画像/あらすじ取得失敗が{SCRAPE_FAIL_THRESHOLD}件連続。HTML構造変更の可能性あり")
                    break
            else:
                scrape_fail_count = 0

            # アフィリエイトURL生成

            image_url = item.get("imageURL", {}).get("large", "")
            if site == "DLsite":
                aff_url = generate_affiliate_url("DLsite", "", pid=pid, floor=target.get("floor", "girls"))
            else:
                base_url = item.get("URL", "")
                aff_url = generate_affiliate_url(site, base_url)
            is_r18 = 1 if _is_r18_item(item, site=site) else 0
            author = _extract_author(item)
            rdate = item.get("date", "")

            save_genre = target["genre"]
            if site in ("DMM.com", "FANZA"):
                fc = str(item.get("floor_code", "")).lower()
                fn = str(item.get("floor_name", ""))
                g_ids = [g.get("id") for g in item.get("iteminfo", {}).get("genre", []) if isinstance(g, dict)]
                is_target_novel = "novel_" in target["genre"]
                is_novel_official = (
                    is_target_novel
                    or fc == "novel"
                    or 115 in g_ids
                    or any(x in fn for x in ("ノベル", "小説"))
                )
                is_comic_official = (
                    fc == "comic"
                    or any(x in fn for x in ("コミック", "マンガ", "漫画"))
                )
                if is_novel_official:
                    save_genre = save_genre.replace("comic_", "novel_").replace("doujin_", "novel_")
                elif is_comic_official or fc.startswith("digital_doujin"):
                    if fc.startswith("digital_doujin"):
                        save_genre = save_genre.replace("novel_", "doujin_").replace("comic_", "doujin_")
                    else:
                        save_genre = save_genre.replace("novel_", "comic_").replace("doujin_", "comic_")
            elif site == "DLsite":
                badge_str = " ".join(item.get("dr_wg_links", [])).upper()
                floor_str = target.get("floor", "")
                is_pro_floor = str(floor_str).endswith("-pro")
                
                is_target_novel = "novel_" in target["genre"]
                has_novel_badge = any(x in badge_str for x in ("/WORK_TYPE/NRE", "/WORK_TYPE/TOW", "/WORK_TYPE/NVL"))
                has_comic_badge = "/WORK_TYPE/MNG" in badge_str

                if is_target_novel and has_novel_badge:
                    save_genre = save_genre.replace("doujin_", "novel_").replace("comic_", "novel_")
                elif has_comic_badge:
                    if is_pro_floor:
                        save_genre = save_genre.replace("novel_", "comic_")
                    else:
                        save_genre = save_genre.replace("novel_", "doujin_").replace("comic_", "doujin_")
                elif has_novel_badge:
                    save_genre = save_genre.replace("doujin_", "novel_").replace("comic_", "novel_")

            ai_tags_str = ",".join(ai_tags)
            _orig_tags = item.get("_original_tags", "")
            _is_excl = item.get("_is_exclusive", 0)
            
            is_lovecal = target.get("floor") in ("digital_doujin_bl", "digital_doujin_tl") or "らぶカル" in target.get("label", "")
            save_site = "Lovecal" if is_lovecal else site

            # 追加カラムの値の準備
            auth_det = item.get("_author_detail", "")
            cast_inf = item.get("_cast_info", "")
            ser_name = item.get("_series_name", "")
            pg_count = item.get("_page_count", 0)

            c.execute(
                """INSERT INTO novelove_posts
                    (product_id, title, author, genre, site, status, release_date, description,
                    affiliate_url, image_url, product_url, post_type, desc_score, last_error, ai_tags, wp_post_url,
                    original_tags, is_exclusive, source_db, author_detail, cast_info, series_name, page_count)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, item.get("title"), author, save_genre,
                 f"{save_site}:r18={is_r18}", final_status, rdate, desc,
                 aff_url, image_url, item.get("URL", ""), "regular", final_score, last_error, ai_tags_str, "",
                 _orig_tags, _is_excl, get_source_db(save_site),
                 auth_det, cast_inf, ser_name, pg_count)
            )
            logger.info(f"[{disp_site}] [{final_status}] {item.get('title','')[:40]}")
            added += 1
        conn.commit()
        conn.close()
        if added > 0: logger.info(f"{disp_site}/{target['label']}: {added}件処理")