#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
novelove_fetcher.py — Novelove 新着取得・スクレイピング・フィルタリングモジュール
==========================================================
このモジュールは auto_post.py から分離された「データ取得」専任ファイルです。
各サイト（FANZA/DMM/DLsite/DigiKet）からのスクレイピング・API通信・
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
    DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET, DB_FILE_UNIFIED,
    _clean_description, calculate_local_priority,
    get_db_path, get_source_db, db_connect,
    trigger_emergency_stop, notify_discord,
    DMM_API_ID, DMM_AFFILIATE_API_ID,
    generate_affiliate_url,
    DEEPSEEK_API_KEY,
)

# スクレイピング構造変化の検知閾値（連続N回で緊急停止）
SCRAPE_FAIL_THRESHOLD = 5

# === 取得対象ジャンル定義 ===
FETCH_TARGETS = [
    # らぶカル（FANZA同人 BL/TL 専用フロア）
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin_bl", "genre": "doujin_bl", "label": "らぶカル_BL", "keyword": None},
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin_tl", "genre": "doujin_tl", "label": "らぶカル_TL", "keyword": None},
    # FANZA 商業R18（新規追加）
    {"site": "FANZA",   "service": "ebook",  "floor": "bl",             "genre": "comic_bl",  "label": "FANZA商業_BL", "keyword": None},
    {"site": "FANZA",   "service": "ebook",  "floor": "tl",             "genre": "comic_tl",  "label": "FANZA商業_TL", "keyword": None},
    # DMM.com 商業一般
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_bl",  "label": "DMM_BL",       "article": "category", "article_id": "66036", "keyword": None},
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_tl",  "label": "DMM_TL",       "article": "category", "article_id": "66060", "keyword": None},
    # DLsite 同人（漫画）
    {"site": "DLsite",  "service": None,     "floor": "bl",             "genre": "doujin_bl", "label": "DLsite同人_BL",       "keyword": None},
    {"site": "DLsite",  "service": None,     "floor": "girls",          "genre": "doujin_tl", "label": "DLsite同人_TL",       "keyword": None},
    # DLsite 商業（漫画）
    {"site": "DLsite",  "service": None,     "floor": "bl-pro",         "genre": "comic_bl",  "label": "DLsite商業_BL",       "keyword": None},
    {"site": "DLsite",  "service": None,     "floor": "girls-pro",      "genre": "comic_tl",  "label": "DLsite商業_TL",       "keyword": None},
    # DLsite 同人（小説）
    {"site": "DLsite",  "service": None,     "floor": "bl",             "genre": "novel_bl",  "label": "DLsite同人_BL小説",   "keyword": None},
    {"site": "DLsite",  "service": None,     "floor": "girls",          "genre": "novel_tl",  "label": "DLsite同人_TL小説",   "keyword": None},
    # DLsite 商業（小説）
    {"site": "DLsite",  "service": None,     "floor": "bl-pro",         "genre": "novel_bl",  "label": "DLsite商業_BL小説",   "keyword": None},
    {"site": "DLsite",  "service": None,     "floor": "girls-pro",      "genre": "novel_tl",  "label": "DLsite商業_TL小説",   "keyword": None},
    # DMM.com 商業（小説）
    {"site": "DMM.com", "service": "ebook",  "floor": "novel",          "genre": "novel_bl",  "label": "DMM_BL小説",          "article": "category", "article_id": "66042", "keyword": None},
    {"site": "DMM.com", "service": "ebook",  "floor": "novel",          "genre": "novel_tl",  "label": "DMM_TL小説",          "article": "category", "article_id": "66064", "keyword": None},
    # FANZA 商業（小説）
    {"site": "FANZA",   "service": "ebook",  "floor": "bl",             "genre": "novel_bl",  "label": "FANZA商業_BL小説",    "keyword": "小説"},
    {"site": "FANZA",   "service": "ebook",  "floor": "tl",             "genre": "novel_tl",  "label": "FANZA商業_TL小説",    "keyword": "小説"},
    # らぶカル 同人（小説）—— v15.5.0: 旧FANZA同人小説フロアをらぶカル専用フロアに統一
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin_bl", "genre": "novel_bl",  "label": "らぶカル同人_BL小説", "keyword": "ノベル"},
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin_tl", "genre": "novel_tl",  "label": "らぶカル同人_TL小説", "keyword": "ノベル"},
    # DigiKet（fetch_digiket_items()で処理するためsite=DigiKetのみ記載）
    {"site": "DigiKet", "service": None,     "floor": None,             "genre": "comic_bl",  "label": "DigiKet商業_BL",      "keyword": None},
    {"site": "DigiKet", "service": None,     "floor": None,             "genre": "comic_tl",  "label": "DigiKet商業_TL",      "keyword": None},
    {"site": "DigiKet", "service": None,     "floor": None,             "genre": "doujin_bl", "label": "DigiKet同人_BL",      "keyword": None},
    {"site": "DigiKet", "service": None,     "floor": None,             "genre": "doujin_tl", "label": "DigiKet同人_TL",      "keyword": None},
    # DigiKet（小説）
    {"site": "DigiKet", "service": None,     "floor": None,             "genre": "novel_bl",  "label": "DigiKet_BL小説",      "keyword": None},
    {"site": "DigiKet", "service": None,     "floor": None,             "genre": "novel_tl",  "label": "DigiKet_TL小説",      "keyword": None},
]


# === 入力フィルター（3段階） ===
MASK_LIGHT_MAP = {
    "セックス": "●●●ス", "SEX": "S●X", "sex": "熱く溶け合う",
    "強姦": "無理やり関係を迫る", "レイプ": "無理やり関係を迫る",
    "陵辱": "辱め", "生ハメ": "無防備な行為", "ナカに入れ": "深く求め",
    "乳首": "敏感な場所", "性器": "秘めた部分", "精液": "愛の雫",
    "孕ませ": "宿らせ", "種付け": "命を宿らせ",
}
MASK_EXTRA_MAP = {
    "巨根": "大きすぎるモノ", "アクメ": "絶頂", "絶頂": "クライマックス",
    "アクメ堕ち": "絶頂", "孕み堕ち": "宿らせ", "おま◯こ": "秘部",
    "ド巨根": "大きなモノ", "中出し": "最奥への放出", "膣内": "最奥",
    "肉棒": "熱い塊", "クリトリス": "秘密の突起",
    "SMクラブ": "背徳の社交場", "M奴隷": "快楽に身を委ねた存在",
    "ご主人様": "支配者", "拷問": "激しい責め", "調教": "快楽に染めていく",
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
    r18_keywords = {"R18", "18禁", "成人向け", "18歳未満", "アダルト", "sexually explicit"}
    title = item.get("title", "")
    genres = item.get("genre", []) or item.get("iteminfo", {}).get("genre", []) or []
    cat = item.get("category_name", "") or ""
    target_text = str(title) + str(cat)
    for g in genres:
        target_text += (g.get("name", "") if isinstance(g, dict) else str(g))
    if any(kw in target_text for kw in r18_keywords): return True
    if site == "FANZA": return True
    title_r18_kws = {
        "セックス", "SEX", "sex", "エッチ", "えっち", "ナカイキ", "中イキ",
        "イかせ", "イかされ", "射精", "勃起", "オナ禁", "オナニー", "潮吹き",
        "絶頂", "痴女", "痴漢", "おっぱい", "巨乳", "乳首",
        "性感マッサージ", "性感ほぐし", "風俗", "ソープ", "デリヘル",
        "NTR", "ネトラレ", "寝取", "メスイキ", "女装", "調教", "奴隷", "緊縛",
        "孕ませ", "種付け", "R18", "R-18", "18禁", "モザイク版", "成人向け", "アダルト", "官能",
    }
    if any(kw in title for kw in title_r18_kws): return True
    return False

def _extract_author(item):
    for field in ["article", "author", "writer", "artist"]:
        val = item.get(field)
        if val:
            if isinstance(val, list) and val:
                return val[0].get("name", "") if isinstance(val[0], dict) else str(val[0])
            if isinstance(val, dict): return val.get("name", "")
            if isinstance(val, str) and val.strip(): return val.strip()
    return ""

def _run_emergency_ai_extraction(product_url, site_type="FANZA"):
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
        session = _make_fanza_session()
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

    # 引数 pages が直接渡された場合（DLsite/DigiKet のスクレイピング結果など）
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

def _make_fanza_session():
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
    session = _make_fanza_session()
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
        if r is None or r.status_code != 200: return "", "", False
        text = r.text
        soup_pre = BeautifulSoup(text, 'html.parser')
        wg_links = [a.get("href", "") for a in soup_pre.select(".work_genre a")]
        has_mng = any("/work_type/MNG" in link for link in wg_links)
        has_nre = any("/work_type/NRE" in link for link in wg_links)  # ノベル
        has_tow = any("/work_type/TOW" in link for link in wg_links)  # テキスト・画像
        if not has_mng and not has_nre and not has_tow:
            type_map = {"SOU": "ボイス", "NRE": "ノベル", "MNG": "マンガ",
                        "GME": "ゲーム", "MOV": "動画", "ANI": "アニメ", "ICG": "CG集"}
            detected = [name for code, name in type_map.items()
                        if any(f"/work_type/{code}" in link for link in wg_links)]
            logger.warning(f"[DLsite] 漫画・ノベル以外の形式（{', '.join(detected) or '不明'}）のため除外: {url}")
            return "__EXCLUDED_TYPE__", "", False
        lang_labels = [a.text.strip() for a in soup_pre.select(".work_genre a")]
        FOREIGN_LABELS = ["韓国語", "中国語", "繁體中文", "繁体中文", "简体中文", "English", "英語"]
        for lbl in lang_labels:
            if any(fl in lbl for fl in FOREIGN_LABELS):
                logger.warning(f"[DLsite] 外国語版ラベル（{lbl}）のため除外: {url}")
                return "__EXCLUDED_TYPE__", "", False
        soup = BeautifulSoup(text, 'html.parser')
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
            if len(t) > 100: return t.strip(), tags_str, is_exclusive
        for h3 in soup.find_all(['h3', 'div'], string=re.compile(r'作品内容')):
            next_div = h3.find_next_sibling('div')
            if next_div:
                t = next_div.get_text(separator="\n", strip=True)
                if len(t) > 50: return t.strip(), tags_str, is_exclusive
        meta_desc = soup.select_one('meta[property="og:description"]')
        if meta_desc and meta_desc.get('content'):
            return meta_desc.get('content').strip(), tags_str, is_exclusive
        # 最終フォールバック（完全0文字ならAI起動）
        ai_desc = _run_emergency_ai_extraction(url, site_type="DLsite")
        if ai_desc:
            return ai_desc, tags_str, is_exclusive
        return "", tags_str, is_exclusive
    except Exception as e:
        logger.error(f"DLsiteスクレイピングエラー: {e}")
        return "", "", False


def scrape_digiket_description(url):
    """
    DigiKetの商品詳細ページからあらすじ、og:image、ページ数、公式メディア種別、登録日をタプルで返す。
    戻り値: (description: str, og_img_url: str, pages: int or None, official_format: str or None, release_date: str)
    official_format: "comic" / "novel" / None（判定不能時）
    release_date: "YYYY-MM-DD" 形式、取得できなければ ""
    """
    try:
        with requests.Session() as session:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
            }
            session.headers.update(headers)
            if "/show/_/" in url:
                url = url.replace("/show/_/", "/show/_data/")
            parsed = urllib.parse.urlparse(url)
            encoded_url = urllib.parse.quote(parsed.path, safe='')
            age_check_url = f"https://www.digiket.com/age_check.php?declared=yes&url={encoded_url}"
            session.get(age_check_url, timeout=20)
            r = session.get(url, timeout=20)
            if r.status_code != 200:
                logger.warning(f"  [DigiKet] HTTPエラー {r.status_code}: {url}")
                return "", "", None, None, ""
            # v15.3.3: エンコーディング自動判別 — DigiKetはUTF-8が増加中のため、UTF-8から優先的に試す
            try:
                html_text = r.content.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    html_text = r.content.decode('euc-jp')
                except Exception:
                    html_text = r.content.decode('cp932', errors='replace')
            soup = BeautifulSoup(html_text, 'html.parser')

            # v11.3.1: ページ数の抽出ｗ
            pages = None
            spec_table = soup.select_one(".spec_table")
            if spec_table:
                # dl/dt/dd 形式または table/tr/th/td 形式を探す
                remarks_text = ""
                for dt in spec_table.select("dt"):
                    if "備考" in dt.text:
                        dd = dt.find_next_sibling("dd")
                        if dd: remarks_text = dd.text
                        break
                if not remarks_text:
                    for tr in spec_table.select("tr"):
                        th = tr.select_one("th")
                        td = tr.select_one("td")
                        if th and td and "備考" in th.text:
                            remarks_text = td.text
                            break
                if remarks_text:
                    m_p = re.search(r"(\d+)ページ", remarks_text)
                    if m_p:
                        pages = int(m_p.group(1))

            labels = ["作品内容", "作品説明", "作品詳細", "作品概要", "ストーリー", "商品の説明"]
            description = ""
            for label in labels:
                target_tag = soup.find(string=re.compile(label))
                if not target_tag: continue
                candidate = target_tag.find_next(['td', 'div', 'p', 'span'])
                if candidate:
                    t = candidate.get_text("\n", strip=True)
                    if t == label:
                        candidate = candidate.find_next(['td', 'div', 'p', 'span'])
                        if candidate:
                            t = candidate.get_text("\n", strip=True)
                    if len(t) > 20:
                        description = t
                        logger.info(f"  [DigiKet] 取得成功 (ラベル: {label})")
                        break
            if not description:
                long_texts = [tag.get_text(strip=True) for tag in soup.find_all(['td', 'div', 'p'])
                              if len(tag.find_all()) <= 5 and len(tag.get_text(strip=True)) > 100]
                if long_texts:
                    description = max(long_texts, key=len)
                    logger.info("  [DigiKet] 取得成功 (フォールバック)")
            og_img_url = ""
            og_img = soup.select_one('meta[property="og:image"]')
            if og_img and og_img.get('content'):
                og_img_url = og_img.get('content')

            # v12.2.3: 登録日（発売日）の取得
            release_date = ""
            for label_text in ["登録日", "発売日", "発行日", "配信日"]:
                dt_tag = soup.find("div", class_="sub2", string=lambda t: t and label_text in t)
                if dt_tag:
                    dd_tag = dt_tag.find_next_sibling("div", class_="sub-data2")
                    if dd_tag:
                        raw = dd_tag.text.strip()
                        m_d = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", raw)
                        if m_d:
                            release_date = f"{m_d.group(1)}-{m_d.group(2).zfill(2)}-{m_d.group(3).zfill(2)}"
                    break

            if description:
                description = html.unescape(description)
                description = _clean_description(description)

                # v11.3.2: 詳細ページの「カテゴリー」ラベルから公式メディア種別を確定
                # v15.3.3: DigiKetの構造変更(table -> div/dd)に伴う「ジャンル：」表示への対応
                official_format = None
                
                # 1. 新構造対応 (dl/dd等による「ジャンル：」表示)
                for tag in soup.find_all(string=re.compile(r"ジャンル[：:]")):
                    parent = tag.find_parent()
                    if parent:
                        parent_dl = parent.find_parent("dl")
                        t = parent_dl.get_text() if parent_dl else parent.parent.get_text()
                        if any(x in t for x in ["コミック", "マンガ", "漫画"]):
                            official_format = "comic"
                            break
                        elif any(x in t for x in ["小説", "ノベル", "ライトノベル"]):
                            official_format = "novel"
                            break
                
                # 2. 旧構造対応 (tableによる「カテゴリー」表示)
                if not official_format:
                    for tbl in soup.find_all("table"):
                        if "カテゴリー" in tbl.get_text():
                            for tr in tbl.find_all("tr"):
                                tr_text = tr.get_text()
                                if "カテゴリー" in tr_text:
                                    if any(x in tr_text for x in ["コミック", "マンガ", "漫画"]):
                                        official_format = "comic"
                                    elif any(x in tr_text for x in ["小説", "ノベル", "ライトノベル"]):
                                        official_format = "novel"
                                    break
                            if official_format:
                                break
                
                # 専売・限定 判定 (v13.5.1 改修: 厳格なDOM・画像判定)
                is_exclusive = False
                
                # ユーザーからのご指摘通り、DigiKet限定作品には必ず専用のバッジ画像が付与される
                if soup.find('img', src=lambda s: s and 'digiket.gif' in s.lower()):
                    is_exclusive = True
                else:
                    # サブデータ内のテキスト（例：キーワード: DiGiket限定）を念のため確認。
                    # aタグ完全一致のみとする（本文中テキストは絶対に拾わない）
                    for a_tag in soup.find_all('a'):
                        if a_tag.text.strip() == "DiGiket限定":
                            is_exclusive = True
                            break

                # 戻り値: (description, og_img_url, pages, official_format, release_date, is_exclusive)
                # A-5: 6要素タプルは unpack エラーの温床（過去 v13.7.2/v13.7.3 で2回発生）。
                #      要素を増やす際は必ず全呼び出し箇所を更新し、NamedTuple化も検討すること。
                return description, og_img_url, pages, official_format, release_date, is_exclusive
            logger.warning(f"  [DigiKet] あらすじ特定失敗: {url}")
            # 最終フォールバック（完全0文字ならAI起動）
            ai_desc = _run_emergency_ai_extraction(url, site_type="DigiKet")
            if ai_desc:
                return ai_desc, og_img_url, pages, None, release_date, False
            return "", og_img_url, pages, None, release_date, False
    except Exception as e:
        logger.error(f"  [DigiKet] エラー発生: {e}")
        return "", "", None, None, "", False

def scrape_description(product_url, site="FANZA", genre=""):
    if not product_url: return ""
    if "dlsite" in str(product_url).lower():
        desc, _tags, _excl = scrape_dlsite_description(product_url)
        return desc
    if "digiket" in str(product_url).lower():
        desc, _, _, _, _, _excl = scrape_digiket_description(product_url)
        return desc
    session = _make_fanza_session()
    _any_desc_found = False  # あらすじテキストが何らか存在したか（短くても）
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
        for dt in soup.find_all("dt"):
            if "作品形式" in dt.text or "形式" in dt.text or "ジャンル" in dt.text:
                dd = dt.find_next_sibling("dd")
                if dd:
                    has_format_tag = True
                    fmt_text = dd.text.strip()
                    if "コミック" in fmt_text or "劇画" in fmt_text or "マンガ" in fmt_text:
                        is_comic = True
                    break
        if has_format_tag and not is_comic and not is_novel_target:
            logger.warning(f"[FANZA] マンガ以外の形式のため除外: {product_url}")
            return "__EXCLUDED_TYPE__"
        page_title_tag = soup.find("title")
        page_title_str = page_title_tag.text if page_title_tag else ""
        FOREIGN_TITLE_PATTERNS = [
            "韓国語版", "한국어", "繁体中文", "繁體中文", "简体中文", "簡体中文",
            "中国語版", "English version", "English ver"
        ]
        bracket_contents = re.findall(r'[【\[\（\(]([^】\]\）\)]+)[】\]\）\)]', page_title_str)
        for bc in bracket_contents:
            if any(fp in bc for fp in FOREIGN_TITLE_PATTERNS):
                logger.warning(f"[FANZA] 外国語版タイトルパターン（{bc}）のため除外: {product_url}")
                return "__EXCLUDED_TYPE__"
        if any(kw in text for kw in ["カテゴリー</th><td>写真集", "カテゴリー</th><td>グラビア", "カテゴリー</th><td>文芸・小説", "カテゴリー</th><td>ライトノベル"]):
            logger.warning(f"[FANZA] 禁止カテゴリーを検知: {product_url}")
            return "__EXCLUDED_TYPE__"
        # === あらすじ抽出（MAX文字数採用型ハイブリッド） ===
        # JSON-LD と HTMLクラスの「両方」から取得し、文字数が多い方を自動採用する。
        # - DMMブックス: HTMLクラスが存在しない(React SPA)ため JSON-LD が唯一の情報源（全文格納）
        # - FANZA同人/らぶカル: JSON-LDは110文字に省略されるため HTMLクラスが唯一の全文情報源
        # どちらか片方だけに依存すると、サイトによって全文が取れなくなるため必ず両方走らせる。

        # ソース1: JSON-LD (Schema.org構造化データ)
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

        # ソース2: HTMLの固定クラス要素（FANZA同人/らぶカルの全文はここにしかない）
        html_desc = ""
        for selector in [".summary__txt", ".mg-b20", ".common-description", ".product-description__text"]:
            el = soup.select_one(selector)
            if el:
                t = el.get_text(separator="\n", strip=True)
                if len(t) > len(html_desc):
                    html_desc = t

        # MAX判定: 文字数が多い方を採用（全文が取れる方が自動的に勝つ）
        best_desc = ld_desc if len(ld_desc) > len(html_desc) else html_desc
        _any_desc_found = bool(ld_desc) or bool(html_desc)  # フィルター前に「何か存在」をフラグ保存

        # 省略検知フィルター: DMM側が省略した切り詰め文（末尾「…」等）だけを掴まされた場合を検出
        # サイト構造変更で全文が取れなくなった際のサイレントエラーを防止する
        if len(best_desc) < 150 and best_desc.rstrip().endswith(("…", "...")):
            logger.warning(f"  [省略検知] 取得テキストが省略文のみ({len(best_desc)}文字): {product_url}")
            best_desc = ""  # 省略文を破棄してAI修復へフォールスルー

        # 結果判定
        if len(best_desc) > 50:
            return best_desc.strip()

        # 取得失敗 → 緊急AI修復（JSON-LDもHTMLクラスも不十分 = サイト構造変更の可能性が高いため通知）
        ai_desc = _run_emergency_ai_extraction(product_url, site_type="FANZA/DMM")
        if ai_desc:
            return ai_desc

    except Exception as e:
        logger.warning(f"スクレイピング失敗 ({product_url}): {e}")
    # あらすじが見つかったが短すぎた場合と、そもそも何も見つからなかった場合を区別する
    return "__DESC_TOO_SHORT__" if _any_desc_found else ""


# === DigiKet ジャンル解析ヘルパー ===

def _extract_digiket_genre_tags(content_encoded):
    """content:encodedのHTML内からジャンルタグを抽出して返す"""
    if not content_encoded:
        return []
    tags = []
    m = re.search(r'<strong>\s*ジャンル[：:](.*?)</strong>', content_encoded, re.S)
    if m:
        tag_html = m.group(1)
        tags = re.findall(r'<a[^>]*>([^<]+)</a>', tag_html)
        tags = [t.strip() for t in tags if t.strip()]
    if tags:
        return tags
    try:
        soup = BeautifulSoup(content_encoded, "html.parser")
        for label in soup.find_all(string=re.compile(r'ジャンル')):
            parent = label.find_parent()
            if not parent:
                continue
            target_el = parent.parent if parent.parent else parent
            found = [a.get_text(strip=True) for a in target_el.find_all("a")]
            if found:
                return found
    except Exception:
        pass
    return tags

def _classify_digiket_genre(genre_tags, target_id):
    """
    DigiKetのジャンルタグからBL/TL/小説を判定する。
    target=8（商業BL・TLチャンネル）: タグを元にcomic/novelとTL/BLを振り分け（v15.3.3改修）
        - TL + 小説 → novel_tl / 小説のみ → novel_bl / TLのみ → comic_tl / それ以外 → comic_bl
    target=6（商業電子書籍全般）: 女性コミック＋TL語ありのみcomic_tl、それ以外スキップ
    target=2（同人全般）: TLタグ → doujin_tl / BLタグ → doujin_bl / どちらもなければスキップ（男性向け除外）
    戻り値: "comic_bl" / "comic_tl" / "novel_bl" / "novel_tl" / "doujin_bl" / "doujin_tl" / None（スキップ）
    """
    tags_str = " ".join(genre_tags)
    TL_KEYWORDS = ["ティーンズラブ", "TL", "乙女"]
    BL_KEYWORDS = ["ボーイズラブ", "BL", "腐向け"]
    NOVEL_KEYWORDS = ["小説", "ノベル", "ライトノベル"]
    if target_id == "8":
        # ★v15.3.3修正: target=8はBL・TL混在(小説も含む)チャンネル。タグを元にcomic/novelとTL/BLを振り分け
        is_tl = any(kw in tags_str for kw in TL_KEYWORDS)
        is_novel = any(kw in tags_str for kw in NOVEL_KEYWORDS)
        if is_tl and is_novel:
            return "novel_tl"
        elif is_novel:
            return "novel_bl"
        elif is_tl:
            return "comic_tl"
        return "comic_bl"
    elif target_id == "6":
        if "女性コミック" not in tags_str:
            return None
        if any(kw in tags_str for kw in TL_KEYWORDS):
            return "comic_tl"
        return None
    elif target_id == "2":
        if any(kw in tags_str for kw in TL_KEYWORDS):
            return "doujin_tl"
        if any(kw in tags_str for kw in BL_KEYWORDS):
            return "doujin_bl"
        return None
    return None


# === 新着取得（API/スクレイピング） ===

def _fetch_dlsite_items(target):
    floor = target.get("floor", "girls")
    genre = target.get("genre", "")
    is_novel = genre in ("novel_bl", "novel_tl")
    work_category = "novel" if is_novel else "manga"
    url = f"https://www.dlsite.com/{floor}/fsr/=/language/jp/work_type_category[0]/{work_category}/order/release_d/per_page/30/"
    items = []
    VOICE_KEYWORDS = ["ボイス", "音声", "ASMR", "CV.", "CV:", "cv.", "cv:", "シチュエーションCD",
                      "バイノーラル", "ドラマCD", "全年齢ボイス", "簡体中文版", "繁体中文版",
                      "繁體中文版", "English", "韓国語版", "中国語", "音楽", "サウンドトラック", "音声作品"]
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = _fetch_with_retry(url, headers=headers, timeout=20, label=f"DLsite新着({work_category})")
        if r is None: return items
        soup = BeautifulSoup(r.text, "html.parser")
        # fsr ページは作品リンクが /work/=/product_id/ 形式のaタグに集約されている
        work_links = soup.select("a[href*='/work/=/product_id/']")
        seen_pids = set()
        for title_tag in work_links[:30]:
            title_text = title_tag.get("title") or title_tag.text.strip()
            if not title_text: continue
            skip_keywords = VOICE_KEYWORDS if is_novel else VOICE_KEYWORDS + ["ノベル", "小説", "実用"]
            if any(kw in title_text for kw in skip_keywords):
                logger.info(f"[DLsite] 種別フィルターによりスキップ: {title_text[:40]}")
                continue
            detail_url = title_tag.get("href")
            pid = detail_url.rstrip("/").split("/")[-1].replace(".html", "")
            if not pid or pid in seen_pids: continue
            seen_pids.add(pid)
            image_url = ""
            try:
                dr = _fetch_with_retry(detail_url, headers=headers, timeout=10, label="DLsite詳細(形式判定)")
                if dr is None:
                    logger.info(f"  [DLsite] 詳細ページ取得失敗のためスキップ: {title_text[:30]}")
                    continue
                dsoup = BeautifulSoup(dr.text, "html.parser")
                dr_wg_links = [a.get("href", "") for a in dsoup.select(".work_genre a")]
                if is_novel:
                    valid_badge = any("/work_type/NRE" in link or "/work_type/TOW" in link
                                      for link in dr_wg_links)
                else:
                    valid_badge = any("/work_type/MNG" in link for link in dr_wg_links)
                if not valid_badge:
                    logger.info(f"  [DLsite] 期待する形式バッジなしのためスキップ: {title_text[:30]}")
                    continue
                og_img = dsoup.select_one('meta[property="og:image"]')
                if og_img: image_url = og_img.get("content", "")

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
            except:
                pass
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
                "dr_wg_links": dr_wg_links
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
        site = target.get("site", "FANZA")
        # v15.5.1: 通知・ログ表示用のサイト名（APIに渡す site とは別に管理）
        _is_lovecal_target = target.get("floor") in ("digital_doujin_bl", "digital_doujin_tl") or "らぶカル" in target.get("label", "")
        disp_site = "らぶカル" if _is_lovecal_target else site
        if site == "DigiKet": continue
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
            _img_large = item.get("imageURL", {}).get("large", "")
            if "/voice/" in _img_large:
                logger.info(f"  [ボイス作品除外] 画像URLにvoiceパスを検出: {title_str[:40]}")
                continue
            if _is_thin_content(title_str, item):
                logger.info(f"  [薄いコンテンツ除外] {title_str[:40]}")
                continue
            if site == "DLsite":
                desc, dl_tags_str, dl_is_exclusive = scrape_dlsite_description(p_url)
                item["_original_tags"] = dl_tags_str
                item["_is_exclusive"] = 1 if dl_is_exclusive else 0
            else:
                desc = scrape_description(p_url, site=site, genre=target["genre"])
                
                # v14.4.0: 専売判定はAPI統一ルール（後段の共通処理で実施）
                item["_original_tags"] = ""
                item["_is_exclusive"] = 0
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

            item_original_tags = item.get("_original_tags", "")
            _is_excl_bool = bool(item.get("_is_exclusive", 0))

            if desc == "__EXCLUDED_TYPE__":
                last_error = "excluded_type"
                desc = ""
            elif desc == "__DESC_TOO_SHORT__":
                # あらすじは存在するが文字数が少なすぎる商品（サイト構造変化ではない）
                last_error = "desc_too_short"
                desc = ""
                notify_discord(f"ℹ️ [{disp_site}] あらすじ短すぎスキップ: {item.get('title','')[:40]}\nURL: {item.get('URL', '')}", username="スクレイピング監視")
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

            c.execute(
                """INSERT INTO novelove_posts
                    (product_id, title, author, genre, site, status, release_date, description,
                    affiliate_url, image_url, product_url, post_type, desc_score, last_error, ai_tags, wp_post_url,
                    original_tags, is_exclusive, source_db)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, item.get("title"), author, save_genre,
                 f"{save_site}:r18={is_r18}", final_status, rdate, desc,
                 aff_url, image_url, item.get("URL", ""), "regular", final_score, last_error, ai_tags_str, "",
                 _orig_tags, _is_excl, get_source_db(save_site))
            )
            logger.info(f"[{disp_site}] [{final_status}] {item.get('title','')[:40]}")
            added += 1
        conn.commit()
        conn.close()
        if added > 0: logger.info(f"{disp_site}/{target['label']}: {added}件処理")

def fetch_digiket_items():
    """DigiKet XML APIから新着を取得し、スクリプトフィルタでpending保存"""
    logger.info("DigiKet 新着取得開始")
    targets = [
        {"target": "8", "label": "DigiKet_商業BL"},
        {"target": "6", "label": "DigiKet_商業TL"},
        {"target": "2", "label": "DigiKet_同人"},
    ]
    conn = db_connect(DB_FILE_UNIFIED)
    c = conn.cursor()
    _img_re = r"src=[\"'](https?://[^\"']+?\.(?:jpg|jpeg|png|gif|webp)(?:\?.*)?)[\"']"
    for target_cfg in targets:
        target_id = target_cfg["target"]
        label = target_cfg["label"]
        api_url = f"https://api.digiket.com/xml/api/getxml.php?target={target_id}&sort=new"
        try:
            logger.info(f"  - 取得先: {label}")
            r = _fetch_with_retry(api_url, timeout=20, label=f"DigiKet API/{label}")
            if r is None:
                logger.warning(f"  [スキップ] {label}: DigiKet APIへの接続が失敗しました（次回フェッチで再試行）")
                continue
            content = r.content
            try:
                decoded_text = content.decode('utf-8')
            except UnicodeDecodeError:
                decoded_text = content.decode('cp932', errors='replace')
            soup = BeautifulSoup(decoded_text, "html.parser")
            items = soup.find_all("item")
            logger.info(f"  - 取得数: {len(items)}件")
            new_count = 0
            skip_count = 0
            scraped_items = []
            for item in items:
                try:
                    title = item.find("title").text if item.find("title") else ""
                    product_url = ""
                    link_tag = item.find("link")
                    if link_tag and link_tag.text.strip():
                        product_url = link_tag.text.strip()
                    else:
                        product_url = item.get("rdf:about") or item.get("about") or ""
                    if not product_url: continue

                    m = re.search(r"ID=(ITM\d+)", product_url) or re.search(r"ITM\d+", product_url)
                    if not m: continue
                    pid = m.group(1) if m.groups() else m.group(0)
                    if c.execute("SELECT 1 FROM novelove_posts WHERE product_id=?", (pid,)).fetchone():
                        continue

                    content_tag = item.find(re.compile(r"encoded", re.I))
                    content_encoded = content_tag.text if content_tag else ""
                    genre_tags = _extract_digiket_genre_tags(content_encoded)
                    genre = _classify_digiket_genre(genre_tags, target_id)
                    if genre is None: continue

                    # v11.3.1/v11.3.2/v13.5.1: 詳細スクレイピングでページ数・公式カテゴリ・専売判定を取得
                    # ★v13.5.1修正: 6番目の戻り値(is_exclusive)を正しく受け取る（以前は5値で受けて捨てていた致命的バグ）
                    desc_full, og_image_full, d_pages, d_format, d_date, _dk_is_excl_from_scrape = scrape_digiket_description(product_url)
                    # DigiKet キータグ取得（専売判定はscrape_digiket_descriptionの結果を信頼源とする）
                    try:
                        _dk_r = _fetch_with_retry(product_url, headers=HEADERS, timeout=10, label="DigiKet詳細(キータグ取得)")
                        if _dk_r is None: raise Exception("DigiKet詳細取得失敗")
                        # scrape_digiket_description と同じ3段階エンコーディング判定に統一
                        try:
                            _dk_text = _dk_r.content.decode('utf-8')
                        except UnicodeDecodeError:
                            try:
                                _dk_text = _dk_r.content.decode('euc-jp')
                            except Exception:
                                _dk_text = _dk_r.content.decode('cp932', errors='replace')
                        _key_m = re.search(r"キー\s*[：:]\s*(.+)", _dk_text)
                        _dk_keys = []
                        if _key_m:
                            _key_str = _key_m.group(1).strip().split("\n")[0]
                            _key_str = re.sub(r"<[^>]+>", " ", _key_str)
                            _dk_keys = [k.strip() for k in re.split(r"[、,\s]+", _key_str) if k.strip()]
                            _dk_keys = [k for k in _dk_keys if k not in {"フルカラー", "モノクロ"}]
                        _dk_tags_str = ",".join(_dk_keys[:10])
                    except Exception:
                        _dk_tags_str = ""
                    # 専売判定: scrape_digiket_descriptionのDOM判定結果（digiket.gif / <a>DiGiket限定</a>）を使用
                    _dk_is_excl = _dk_is_excl_from_scrape
                    time.sleep(1.0)

                    if d_format == "comic":
                        if "novel" in genre:
                            logger.info(f"      - [DigiKet ジャンル補正] 小説 -> 漫画: {title[:30]}")
                        genre = genre.replace("novel_", "doujin_" if target_id == "2" else "comic_")
                    elif d_format == "novel":
                        if "comic" in genre or "doujin" in genre:
                            logger.info(f"      - [DigiKet ジャンル補正] 漫画 -> 小説: {title[:30]}")
                        genre = genre.replace("doujin_", "novel_").replace("comic_", "novel_")

                    if _is_thin_content(title, pages=d_pages):
                        logger.info(f"      - [DigiKet 薄いコンテンツ除外] {title[:40]} ({d_pages}P)")
                        continue

                    desc_text = desc_full if desc_full else (item.find("description").text if item.find("description") else "")
                    scraped_items.append((item, desc_text, og_image_full, pid, genre, title, product_url, d_date, _dk_tags_str, _dk_is_excl))
                except Exception as e:
                    logger.error(f"      - DigiKet 予備スクレイプエラー: {e}")
                    continue

            scraped_items.sort(key=lambda x: len(x[1]) if x[1] else 0, reverse=True)

            digiket_scrape_fail_count = 0  # 構造変化検知用カウンター
            for item, description, og_image_full, pid, genre, title, product_url, d_date, _dk_tags_str, _dk_is_excl in scraped_items:
                try:
                    content_tag = item.find(re.compile(r"encoded", re.I))
                    content_encoded = content_tag.text if content_tag else ""
                    creator_tag = item.find(re.compile(r"creator", re.I))
                    author = creator_tag.text if creator_tag else ""
                    date_tag = item.find(re.compile(r"date", re.I))
                    date_str = date_tag.text if date_tag else ""
                    # v12.2.3: 詳細ページから取得した発売日を優先する
                    if d_date:
                        date_str = d_date
                    elif not date_str:
                        date_str = datetime.now().strftime("%Y-%m-%d")
                    package_tag = item.find("package")
                    image_url = package_tag.text.strip() if package_tag else ""
                    if image_url.startswith("//"): image_url = "https:" + image_url
                    # _img_re は関数冒頭で定義済み
                    if not image_url:
                        img_match = re.search(_img_re, content_encoded, re.I)
                        image_url = img_match.group(1) if img_match else ""
                    if not image_url and description:
                        img_match_desc = re.search(_img_re, description, re.I)
                        image_url = img_match_desc.group(1) if img_match_desc else ""
                    
                    if not image_url and og_image_full:
                        image_url = og_image_full

                    affiliate_url = generate_affiliate_url("DigiKet", product_url)
                    is_r18 = 1 if _is_r18_item({"title": title}, site="DigiKet") else 0
                    
                    last_error = ""
                    final_status = "excluded"
                    final_score = 0
                    ai_tags_str = ""

                    if not description or len(description) <= 50:
                        last_error = "no_desc_or_image"
                        notify_discord(f"⚠️ [DigiKet] スクレイピング失敗: {title[:40]}\nURL: {product_url}", username="スクレイピング監視")
                    elif not image_url:
                        last_error = "no_image"
                    elif _is_noise_content(title, description):
                        last_error = "excluded_foreign"
                    elif not _check_image_ok(image_url):
                        last_error = "no_image"
                    else:
                        # v11.4.0: AI審査を廃止、スクリプトフィルタ通過で即pending
                        final_status = "pending"
                        final_score = calculate_local_priority(title, description, original_tags=_dk_tags_str, release_date_raw=date_str, is_exclusive=bool(_dk_is_excl))

                    # 構造変化検知: 画像なし or あらすじなし が連続したらスクレイピング異常
                    if last_error in ("no_desc_or_image", "no_image"):
                        digiket_scrape_fail_count += 1
                        if digiket_scrape_fail_count >= SCRAPE_FAIL_THRESHOLD:
                            trigger_emergency_stop(f"[DigiKet/{label}] スクレイピング異常検知: 画像/あらすじ取得失敗が{SCRAPE_FAIL_THRESHOLD}件連続。HTML構造変更の可能性あり")
                            break
                    else:
                        digiket_scrape_fail_count = 0

                    site_str = f"DigiKet:r18={is_r18}"
                    c.execute("""INSERT INTO novelove_posts
                        (product_id, title, author, genre, site, status, release_date, description,
                        affiliate_url, image_url, product_url, post_type, desc_score, last_error, ai_tags, wp_post_url,
                        original_tags, is_exclusive, source_db)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (pid, title, author, genre, site_str, final_status, date_str, description,
                         affiliate_url, image_url, product_url, "regular", final_score, last_error, ai_tags_str, "",
                         _dk_tags_str, 1 if _dk_is_excl else 0, "digiket"))
                    new_count += 1
                    logger.info(f"    - 追加: {title[:30]} [{final_status}] genre:{genre}")
                except Exception as e:
                    logger.error(f"    - item処理エラー（スキップ）: {getattr(item.find('title'), 'text', '不明')[:20]} / {e}")
                    skip_count += 1
                finally:
                    time.sleep(1.0)
            conn.commit()
            logger.info(f"  - 完了: {label} (新規: {new_count}件, スキップ: {skip_count}件)")
        except Exception as e:
            logger.error(f"DigiKet取得エラー ({label}): {e}")
    conn.close()
