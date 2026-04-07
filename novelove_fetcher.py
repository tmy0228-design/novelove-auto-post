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
import os
import urllib.parse
import sqlite3
import time
import re
import html
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv

# --- 環境変数の読み込み ---
env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()

from novelove_core import (
    logger, HEADERS,
    DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET,
    _clean_description, calculate_local_priority,
    get_db_path, db_connect,
    trigger_emergency_stop, notify_discord,
)

# スクレイピング構造変化の検知閾値（連続N回で緊急停止）
SCRAPE_FAIL_THRESHOLD = 5

# === 環境変数 ===
DMM_API_ID            = os.environ.get("DMM_API_ID", "")
DMM_AFFILIATE_API_ID  = os.environ.get("DMM_AFFILIATE_API_ID", "")
DMM_AFFILIATE_LINK_ID = os.environ.get("DMM_AFFILIATE_LINK_ID", "")
DLSITE_AFFILIATE_ID   = os.environ.get("DLSITE_AFFILIATE_ID", "novelove")
DIGIKET_AFFILIATE_ID  = os.environ.get("DIGIKET_AFFILIATE_ID", "novelove")

# === 取得対象ジャンル定義 ===
FETCH_TARGETS = [
    # FANZA 同人
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin", "genre": "doujin_bl", "label": "FANZA同人_BL", "keyword": "ボーイズラブ"},
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin", "genre": "doujin_tl", "label": "FANZA同人_TL", "keyword": "乙女向け"},
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
    # FANZA 同人（小説）
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin", "genre": "novel_bl",  "label": "FANZA同人_BL小説",    "keyword": "ボーイズラブ ノベル"},
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin", "genre": "novel_tl",  "label": "FANZA同人_TL小説",    "keyword": "乙女向け ノベル"},
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
AI_TAG_WHITELIST = {
    "オメガバース", "ヤンデレ", "スパダリ", "執着", "年下攻め",
    "幼なじみ", "ケンカップル", "主従", "サラリーマン", "年の差",
    "転生", "契約", "再会", "一途", "運命",
    "溺愛", "身分差", "契約結婚", "御曹司", "騎士",
    "オフィスラブ", "腹黒", "同居", "嫉妬", "強引",
    "独占欲", "初恋", "記憶喪失", "歳の差", "ハッピーエンド",
}

# v11.3.0: 「単話・分冊版」など内容の薄い作品を個別紹介から除外するヘルパー
# ※ランキング記事には適用しない。キーワードとページ数の両方が揃った場合のみ除外。
THIN_CONTENT_KEYWORDS = ["分冊版", "単話", "単話版", "【マイクロ】", "【プチ】"]


# === フィルタリング ヘルパー ===

def _is_r18_item(item, site=None):
    r18_keywords = {"R18", "18禁", "成人向け", "18歳未満", "アダルト", "sexually explicit"}
    title = item.get("title", "")
    genres = item.get("genre", []) or []
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

def _is_noise_content(title, desc=""):
    ng_words = [
        "簡体中文", "繁体中文", "繁體中文", "English", "韓国語版", "中国語",
        "简体中文", "翻訳台詞", "中文字幕", "korean", "한국어"
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


# === セッション / 画像チェック ===

def _make_fanza_session():
    session = requests.Session()
    for domain in [".dmm.co.jp", ".book.dmm.co.jp", "book.dmm.co.jp", ".dmm.co.jp"]:
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
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200: return "", "", False
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
            # v12.9: エンコーディング自動判定を強化（文字化け防止）
            # DigiKetはページによってShift-JIS/EUC-JP/UTF-8が混在するため、
            # トライデコード方式で最も適切なエンコーディングを選択する
            raw_bytes = r.content
            detected_enc = None
            for try_enc in ['utf-8', 'shift_jis', 'euc-jp', 'cp932']:
                try:
                    raw_bytes.decode(try_enc)
                    detected_enc = try_enc
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if not detected_enc:
                detected_enc = 'shift_jis'  # 最終フォールバック
            r.encoding = detected_enc
            soup = BeautifulSoup(r.text, 'html.parser')

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

                # v11.3.2: 詳細ページの「カテゴリー」ラベルから公式メディア种別を確定
                official_format = None
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

                return description, og_img_url, pages, official_format, release_date
            logger.warning(f"  [DigiKet] あらすじ特定失敗: {url}")
            return "", og_img_url, pages, None, release_date
    except Exception as e:
        logger.error(f"  [DigiKet] エラー発生: {e}")
        return "", "", None, None, ""

def scrape_description(product_url, site="FANZA", genre=""):
    if not product_url: return ""
    if "dlsite" in str(product_url).lower():
        desc, _tags, _excl = scrape_dlsite_description(product_url)
        return desc
    if "digiket" in str(product_url).lower():
        desc, _, _, _, _ = scrape_digiket_description(product_url)
        return desc
    session = _make_fanza_session()
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
        next_tag = soup.find("script", id="__NEXT_DATA__")
        if next_tag:
            try:
                ndata = json.loads(next_tag.string)
                p = ndata.get("props", {}).get("pageProps", {})
                desc = p.get("product", {}).get("description") or p.get("data", {}).get("description", "")
                if desc and len(desc.strip()) > 50:
                    return desc.strip()
            except Exception:
                pass
        # JSペイロード内の生テキスト検索 (SPA構造対応)
        best_desc = ""
        for m in re.findall(r'"description":"([^"\\]*(?:\\.[^"\\]*)*)"', text):
            try:
                decoded = json.loads('"' + m + '"')
                decoded = html.unescape(decoded)
                if len(decoded) > len(best_desc) and '<' not in decoded:
                    best_desc = decoded
            except Exception:
                continue
        if len(best_desc) > 50: return best_desc
        
        for p_tag in soup.find_all("p"):
            classes = " ".join(p_tag.get("class", []))
            if "sc-" in classes:
                t = p_tag.get_text(separator="\n", strip=True)
                if len(t) > len(best_desc):
                    best_desc = t
        if len(best_desc) > 50: return best_desc
        summary = soup.select_one(".summary__txt")
        if summary and len(summary.text.strip()) > 10: return summary.text.strip()
        for selector in [".mg-b20", ".common-description", ".product-description__text"]:
            el = soup.select_one(selector)
            if el and len(el.text.strip()) > 10: return el.text.strip()
        og = soup.find("meta", property="og:description")
        if og and len(og.get("content", "")) > 10: return og.get("content").strip()
    except Exception as e:
        logger.warning(f"スクレイピング失敗 ({product_url}): {e}")
    return ""


# === DigiKet ジャンル解析ヘルパー ===

def _extract_digiket_genre_tags(content_encoded):
    """content:encodedのHTML内からジャンルタグを抽出して返す"""
    if not content_encoded:
        return []
    tags = []
    m = re.search(r'ジャンル[：:]\\s*</strong>(.*?)(?:</td>|</div>|</p>|</li>|</span>)', content_encoded, re.S)
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
    DigiKetのジャンルタグからBL/TLを判定する。
    target=8（商業BL・TLチャンネル）: BL優勢のため全件comic_bl扱い
    target=6（商業電子書籍全般）: 女性コミック＋TL語ありのみcomic_tl、それ以外スキップ
    target=2（同人全般）: BL/TLタグで振り分け、どちらもなければスキップ（男性向け除外）
    戻り値: "comic_bl" / "comic_tl" / "doujin_bl" / "doujin_tl" / None（スキップ）
    """
    tags_str = " ".join(genre_tags)
    TL_KEYWORDS = ["ティーンズラブ", "TL", "乙女"]
    BL_KEYWORDS = ["ボーイズラブ", "BL"]
    if target_id == "8":
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
    work_type = "NRE" if is_novel else "MNG"
    url = f"https://www.dlsite.com/{floor}/new/=/work_type/{work_type}/genre/all/"
    items = []
    VOICE_KEYWORDS = ["ボイス", "音声", "ASMR", "CV.", "CV:", "cv.", "cv:", "シチュエーションCD",
                      "バイノーラル", "ドラマCD", "全年齢ボイス", "簡体中文版", "繁体中文版",
                      "繁體中文版", "English", "韓国語版", "中国語", "音楽", "サウンドトラック", "音声作品"]
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        works = soup.select(".n_worklist_item")
        for work in works[:10]:
            title_tag = work.select_one(".work_name a")
            if not title_tag: continue
            title_text = title_tag.text.strip()
            category_tag = work.select_one(".work_category")
            category_text = category_tag.text.strip() if category_tag else ""
            skip_keywords = VOICE_KEYWORDS if is_novel else VOICE_KEYWORDS + ["ノベル", "小説", "実用"]
            if any(kw in (title_text + category_text) for kw in skip_keywords):
                logger.info(f"[DLsite] 種別フィルターによりスキップ: {title_text[:40]}")
                continue
            detail_url = title_tag.get("href")
            pid = detail_url.rstrip("/").split("/")[-1].replace(".html", "")
            if not pid: continue
            image_url = ""
            try:
                dr = requests.get(detail_url, headers=headers, timeout=10)
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
        if site == "DigiKet": continue
        db_path = get_db_path(site)
        api_items = []
        if site == "DLsite":
            logger.info(f"--- [新着取得] {site} ({target['label']}) ---")
            api_items = _fetch_dlsite_items(target)
        else:
            logger.info(f"--- [新着取得] {site} ({target['label']}) ---")
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
                r = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=params, timeout=15)
                api_items = r.json().get("result", {}).get("items", [])
            except Exception as e:
                logger.error(f"API エラー ({site}/{target['label']}): {e}")
                conn = db_connect(db_path)
                c = conn.cursor()
                c.execute("INSERT OR IGNORE INTO novelove_posts (product_id, title, genre, status, last_error, inserted_at) VALUES (?,?,?,?,?,?)",
                          (f"FAIL_{int(time.time())}", f"ERROR: {site}/{target['label']}", target['genre'], 'excluded', 'fetch_failed', datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
                conn.close()
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
            if _is_thin_content(title_str, item):
                logger.info(f"  [薄いコンテンツ除外] {title_str[:40]}")
                continue
            if site == "DLsite":
                desc, dl_tags_str, dl_is_exclusive = scrape_dlsite_description(p_url)
                item["_original_tags"] = dl_tags_str
                item["_is_exclusive"] = 1 if dl_is_exclusive else 0
            else:
                desc = scrape_description(p_url, site=site, genre=target["genre"])
                item["_original_tags"] = ""
                item["_is_exclusive"] = 0
            time.sleep(1.0)
            scraped_data.append((item, desc))

        scraped_data.sort(key=lambda x: len(x[1]) if x[1] else 0, reverse=True)

        scrape_fail_count = 0  # 構造変化検知用カウンター
        for item, desc in scraped_data:
            pid = item.get("content_id")
            last_error = ""
            final_status = "excluded"
            final_score = 0
            ai_tags = []
            if desc == "__EXCLUDED_TYPE__":
                last_error = "excluded_type"
                desc = ""
            elif not desc:
                last_error = "no_description"
                failed_titles.append(item.get("title", "不明"))
                notify_discord(f"⚠️ [{site}] スクレイピング失敗: {item.get('title','')[:40]}\nURL: {item.get('URL', '')}", username="スクレイピング監視")
            elif _is_noise_content(item.get("title", ""), desc):
                last_error = "excluded_foreign"
            elif site == "FANZA" and target.get("genre") == "doujin_tl":
                item_genres = item.get("iteminfo", {}).get("genre", []) or []
                genre_names = [g.get("name", "") if isinstance(g, dict) else str(g) for g in item_genres]
                if any("男性向け" in g for g in genre_names):
                    last_error = "excluded_male_target"
                    logger.info(f"[FANZA同人TL] 男性向けタグ検知のため除外: {item.get('title','')[:30]}")
                else:
                    image_url_tmp = item.get("imageURL", {}).get("large", "")
                    if not _check_image_ok(image_url_tmp):
                        last_error = "no_image"
                    else:
                        # v11.4.0: AI審査を廃止、スクリプトフィルタ通過で即pending
                        final_status = "pending"
            else:
                image_url_tmp = item.get("imageURL", {}).get("large", "")
                if not _check_image_ok(image_url_tmp):
                    last_error = "no_image"
                else:
                    # v11.4.0: AI審査を廃止、スクリプトフィルタ通過で即pending
                    final_status = "pending"

            # 構造変化検知: 画像なし or あらすじなし が連続したらスクレイピング異常
            if last_error in ("no_description", "no_image", "no_desc_or_image"):
                scrape_fail_count += 1
                if scrape_fail_count >= SCRAPE_FAIL_THRESHOLD:
                    trigger_emergency_stop(f"[{site}/{target['label']}] スクレイピング異常検知: 画像/あらすじ取得失敗が{SCRAPE_FAIL_THRESHOLD}件連続。HTML構造変更の可能性あり")
                    break
            else:
                scrape_fail_count = 0

            # FANZAジャンルタグ取得・独占判定
            if site in ("FANZA", "DMM.com"):
                _fanza_noise = {"単行本", "マンガ誌", "アンソロジー", "雑誌", "モノクロ", "フルカラー", "GIGATOON", "単話", "無料作品", "成人向け", "全年齢向け", "男性向け", "女性向け", "乙女向け"}
                _item_genres = item.get("iteminfo", {}).get("genre", []) or []
                _genre_names = [g.get("name", "") if isinstance(g, dict) else str(g) for g in _item_genres]
                _fanza_tags = [g for g in _genre_names if g and g not in _fanza_noise]
                item["_original_tags"] = ",".join(_fanza_tags[:10])
                item["_is_exclusive"] = 1 if "独占販売" in _genre_names else 0
            # アフィリエイトURL生成
            image_url = item.get("imageURL", {}).get("large", "")
            if site == "DLsite":
                floor = target.get("floor", "girls")
                aid = os.environ.get('DLSITE_AFFILIATE_ID', 'novelove')
                aff_url = f"https://dlaf.jp/{floor}/dlaf/=/t/n/link/work/aid/{aid}/id/{pid}.html"
            else:
                base_url = item.get("URL", "")
                encoded_url = urllib.parse.quote(base_url, safe="")
                af_id = DMM_AFFILIATE_LINK_ID or "novelove-001"
                ch_params = "&ch=toolbar&ch_id=text"
                if site == "FANZA":
                    aff_url = f"https://al.fanza.co.jp/?lurl={encoded_url}&af_id={af_id}{ch_params}"
                else:
                    aff_url = f"https://al.dmm.com/?lurl={encoded_url}&af_id={af_id}{ch_params}"
            is_r18 = 1 if _is_r18_item(item, site=site) else 0
            author = _extract_author(item)
            rdate = item.get("date", "")

            save_genre = target["genre"]
            if site in ("DMM.com", "FANZA"):
                fc = str(item.get("floor_code", "")).lower()
                fn = str(item.get("floor_name", ""))
                g_ids = [g.get("id") for g in item.get("iteminfo", {}).get("genre", []) if isinstance(g, dict)]
                is_novel_official = (
                    fc == "novel"
                    or 115 in g_ids
                    or any(x in fn for x in ("ノベル", "小説"))
                )
                is_comic_official = (
                    fc == "comic"
                    or any(x in fn for x in ("コミック", "マンガ", "漫画"))
                )
                if is_novel_official:
                    save_genre = save_genre.replace("comic_", "novel_").replace("doujin_", "novel_")
                elif is_comic_official or fc == "digital_doujin":
                    if fc == "digital_doujin":
                        save_genre = save_genre.replace("novel_", "doujin_").replace("comic_", "doujin_")
                    else:
                        save_genre = save_genre.replace("novel_", "comic_").replace("doujin_", "comic_")
            elif site == "DLsite":
                badge_str = " ".join(item.get("dr_wg_links", [])).upper()
                floor_str = target.get("floor", "")
                is_pro_floor = str(floor_str).endswith("-pro")
                if "/WORK_TYPE/MNG" in badge_str:
                    if is_pro_floor:
                        save_genre = save_genre.replace("novel_", "comic_")
                    else:
                        save_genre = save_genre.replace("novel_", "doujin_").replace("comic_", "doujin_")
                elif any(x in badge_str for x in ("/WORK_TYPE/NRE", "/WORK_TYPE/TOW", "/WORK_TYPE/NVL")):
                    save_genre = save_genre.replace("doujin_", "novel_").replace("comic_", "novel_")

            ai_tags_str = ",".join(ai_tags)
            _orig_tags = item.get("_original_tags", "")
            _is_excl = item.get("_is_exclusive", 0)
            c.execute(
                """INSERT INTO novelove_posts
                    (product_id, title, author, genre, site, status, release_date, description,
                    affiliate_url, image_url, product_url, post_type, desc_score, last_error, ai_tags, wp_post_url,
                    original_tags, is_exclusive)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, item.get("title"), author, save_genre,
                 f"{site}:r18={is_r18}", final_status, rdate, desc,
                 aff_url, image_url, item.get("URL", ""), "regular", final_score, last_error, ai_tags_str, "",
                 _orig_tags, _is_excl)
            )
            logger.info(f"[{site}] [{final_status}] {item.get('title','')[:40]}")
            added += 1
        conn.commit()
        conn.close()
        if added > 0: logger.info(f"{site}/{target['label']}: {added}件処理")

def fetch_digiket_items():
    """DigiKet XML APIから新着を取得し、スクリプトフィルタでpending保存"""
    logger.info("DigiKet 新着取得開始")
    targets = [
        {"target": "8", "label": "DigiKet_商業BL"},
        {"target": "6", "label": "DigiKet_商業TL"},
        {"target": "2", "label": "DigiKet_同人"},
    ]
    conn = db_connect(DB_FILE_DIGIKET)
    c = conn.cursor()
    _img_re = r"src=[\"'](https?://[^\"']+?\.(?:jpg|jpeg|png|gif|webp)(?:\?.*)?)[\"']"
    for target_cfg in targets:
        target_id = target_cfg["target"]
        label = target_cfg["label"]
        api_url = f"https://api.digiket.com/xml/api/getxml.php?target={target_id}&sort=new"
        try:
            logger.info(f"  - 取得先: {label}")
            r = requests.get(api_url, timeout=20)
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

                    # v11.3.1/v11.3.2: 詳細スクレイピングでページ数と公式カテゴリを取得
                    desc_full, og_image_full, d_pages, d_format, d_date = scrape_digiket_description(product_url)
                    # DigiKet キータグ・専売判定
                    try:
                        _dk_r = requests.get(product_url, headers=HEADERS, timeout=10)
                        _dk_r.encoding = _dk_r.apparent_encoding or "utf-8"
                        _dk_text = _dk_r.text
                        _key_m = re.search(r"キー\s*[：:]\s*(.+)", _dk_text)
                        _dk_keys = []
                        if _key_m:
                            _key_str = _key_m.group(1).strip().split("\n")[0]
                            _key_str = re.sub(r"<[^>]+>", " ", _key_str)
                            _dk_keys = [k.strip() for k in re.split(r"[、,\s]+", _key_str) if k.strip()]
                            _dk_keys = [k for k in _dk_keys if k not in {"フルカラー", "モノクロ"}]
                        _dk_is_excl = any(kw in _dk_text for kw in ("デジケット限定", "DiGiket専売", "DigiKet限定", "限定配信"))
                        _dk_tags_str = ",".join(_dk_keys[:10])
                    except Exception:
                        _dk_tags_str = ""
                        _dk_is_excl = False
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
                    _img_re = r'src=["\"](https?://[^"\"]+?\.(?:jpg|jpeg|png|gif|webp)(?:\?.*)?)["\"]'
                    if not image_url:
                        img_match = re.search(_img_re, content_encoded, re.I)
                        image_url = img_match.group(1) if img_match else ""
                    if not image_url and description:
                        img_match_desc = re.search(_img_re, description, re.I)
                        image_url = img_match_desc.group(1) if img_match_desc else ""
                    
                    if not image_url and og_image_full:
                        image_url = og_image_full

                    affiliate_url = product_url
                    if DIGIKET_AFFILIATE_ID:
                        if not affiliate_url.endswith("/"): affiliate_url += "/"
                        affiliate_url += f"AFID={DIGIKET_AFFILIATE_ID}/"
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
                        original_tags, is_exclusive)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (pid, title, author, genre, site_str, final_status, date_str, description,
                         affiliate_url, image_url, product_url, "regular", final_score, last_error, ai_tags_str, "",
                         _dk_tags_str, 1 if _dk_is_excl else 0))
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
