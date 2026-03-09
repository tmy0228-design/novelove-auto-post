#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
Novelove 自動投稿エンジン v7.4.2.0
【DeepSeek完全移行版・シンプルカテゴリ構成】
==========================================================
【変更点 v7.4.1.0 → v7.4.2.0】
 - 変更：カテゴリ構成をシンプルな「BL」「TL」の 2カテゴリに集約（R-18判定による分割を廃止）
 - 調整：`GENRE_CATEGORIES` をジャンル名（BL/TL）から直接カテゴリIDを引く形式に差し戻し
 - 修正：投稿時の R-18 カテゴリ自動振り分けロジックを削除（タグとしての R-18 は維持）
 - 追加：将来用の「ランキング」「セール」カテゴリ対応の準備
 - 更新：WordPress メインメニューを ホーム/BL/TL/ノベラブについて に最適化
 - 移行：Gemini API → DeepSeek API（審査・執筆ともに完全移行）
 - 追加：call_deepseek()関数（OpenAI互換API使用）
 - 変更：_check_desc_ok() をDeepSeek対応に書き換え
 - 変更：call_gemini() → call_deepseek() に置換
 - 削除：Gemini関連インポート・モデル定数・スヌーズ機構
 - 追加：DEEPSEEK_API_KEY 環境変数対応
 - 調整：審査sleep を 3秒に短縮（レート制限が緩いため）
 - 調整：執筆リトライ間隔を 5秒に短縮
==========================================================
【前バージョンからの引き継ぎ事項】
 - scrape_description() に book.dmm.co.jp 新デザイン対応
 - DESC_SCORE_PENDING = 5（5点のみ pending）
 - 投稿クールダウン間隔：1時間（55分）
 - DBスキーマ・ジャンル定義は変更なし
=========================================================="""

import random
import requests
import json
import os
import sqlite3
import time
import logging
import re
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv
import sys
import argparse

# --- 環境変数の読み込み ---
env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()

# --- Discord通知機能 ---
def notify_discord(message, username="ノベラブ通知くん", avatar_url=None):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url: return False
    payload = {"content": message, "username": username}
    if avatar_url: payload["avatar_url"] = avatar_url
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        return r.status_code in (200, 204)
    except: return False

def _clean_description(text):
    """あらすじのクリーンアップ（本文を削りすぎないソフト版）"""
    if not text: return ""
    soft_pattern = r"(?m)^(?:販売日|公開日|配信予定日|ページ数|ファイル容量|連続再生時間|対応OS|動作環境|作品形式|品番).*[:：].*$"
    result = re.sub(soft_pattern, "", text)
    result = re.sub(r"<[^>]+>", "", result)
    result = re.sub(r"\n\s*\n", "\n", result)
    return result.strip()

# === 設定欄 ===
DEEPSEEK_API_KEY      = os.environ.get("DEEPSEEK_API_KEY", "")
WP_SITE_URL           = "https://novelove.jp"
WP_USER               = os.environ.get("WP_USER", "")
WP_APP_PASSWORD       = os.environ.get("WP_APP_PASSWORD", "")
DMM_API_ID            = os.environ.get("DMM_API_ID", "")
DMM_AFFILIATE_API_ID  = os.environ.get("DMM_AFFILIATE_API_ID", "")
DMM_AFFILIATE_LINK_ID = os.environ.get("DMM_AFFILIATE_LINK_ID", "")

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = "deepseek-chat"  # V3.2 非思考モード（執筆・審査共通）

# === 取得対象ジャンル定義 ===
FETCH_TARGETS = [
    {"site": "FANZA",   "service": "ebook",  "floor": "bl",             "genre": "BL",           "label": "BL小説",         "keyword": None},
    {"site": "FANZA",   "service": "ebook",  "floor": "tl",             "genre": "TL",           "label": "TL小説",         "keyword": None},
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin", "genre": "doujin_bl",    "label": "BL同人",         "keyword": "ボーイズラブ"},
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin", "genre": "doujin_tl",    "label": "乙女同人",       "keyword": "乙女向け"},
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_bl",     "label": "一般BL",         "keyword": "ボーイズラブ"},
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_tl",     "label": "一般TL",         "keyword": "ティーンズラブ"},
    {"site": "DLsite",  "service": None,     "floor": "girls",          "genre": "doujin_tl",    "label": "DLsite乙女",     "keyword": None},
    {"site": "DLsite",  "service": None,     "floor": "bl",             "genre": "doujin_bl",    "label": "DLsiteBL",      "keyword": None},
]

GENRE_TAGS = {
    "BL":           ["BL", "BL小説"],
    "TL":           ["TL", "TL小説"],
    "doujin_bl":    ["BL", "BL同人", "同人"],
    "doujin_tl":    ["乙女向け", "同人"],
    "comic_bl":     ["BL", "BLコミック", "一般"],
    "comic_tl":     ["TL", "TLコミック", "一般"],
}

GENRE_CATEGORIES = {
    "BL":           23,
    "doujin_bl":    23,
    "comic_bl":     23,
    "TL":           24,
    "doujin_tl":    24,
    "comic_tl":     24,
    "ranking":      30,
    "sale":         31,
}

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

# === システム設定 ===
SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_FILE_FANZA  = os.path.join(SCRIPT_DIR, "novelove.db")
DB_FILE_DLSITE = os.path.join(SCRIPT_DIR, "novelove_dlsite.db")
LOG_FILE       = os.path.join(SCRIPT_DIR, "novelove.log")

DESC_SCORE_PENDING  = 5
DESC_SCORE_WATCHING = 4

logger = logging.getLogger("novelove")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    _fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _sh = logging.StreamHandler()
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    _fh.setFormatter(_fmt)
    _sh.setFormatter(_fmt)
    logger.addHandler(_fh)
    logger.addHandler(_sh)

# === キャラクター設定 ===
REVIEWERS = [
    {
        "id": "shion", "name": "紫苑",
        "genres": ["BL", "doujin_bl"],
        "face_image": "紫苑",
        "tag_name": "【紫苑の個人的な注目属性】",
        "personality": "クールで毒舌な腐女子OL。「解釈一致」「これは神」が口癖。BL同人誌の即売会にも足繁く通う。「同人誌は公式より解釈が深い」が持論。",
        "tone": "冷静で分析的だが愛が滲み出る文体。同人作品の場合は作者への敬意とマニアックなこだわりを添える。",
        "greeting": "……また沼作品見つけてしまった。報告しなきゃ気が済まない。",
    },
    {
        "id": "marika", "name": "茉莉花",
        "genres": ["TL", "doujin_tl", "doujin_voice"],
        "face_image": "茉莉花",
        "tag_name": "【茉莉花の今回のときめき成分】",
        "personality": "明るくポップなカフェ店員。「胸きゅんすぎる」が口癖。音声作品も大好きで、イヤホンしながら仕事中に感情爆発させるタイプ。",
        "tone": "テンション高め、感嘆符多め。ボイス作品の時は声・音質・演技への言及を自然に混ぜる。",
        "greeting": "ちょっと聞いて！！これやばすぎてひとりで抱えられない作品見つけた〜！！",
    },
    {
        "id": "aoi", "name": "葵",
        "genres": ["BL", "doujin_bl"],
        "face_image": "葵",
        "tag_name": "【葵の今回の沼ポイント】",
        "personality": "BL好きの大学生。コミケや同人即売会に毎回参戦し、配置とサークル情報を全部把握している猛者。推しの話になると早口モードになる。",
        "tone": "オタク特有の早口テンション。同人作品では「作者さん」への熱い敬意と属性萌えの語りが炸裂する。",
        "greeting": "ねぇちょっと、この作品やばくない？推しが多すぎて情緒が終わる予感しかしない……",
    },
    {
        "id": "momoka", "name": "桃香",
        "genres": ["TL", "doujin_tl", "doujin_voice"],
        "face_image": "桃香",
        "tag_name": "【桃香の今回の刺さりポイント】",
        "personality": "2児の主婦。子供が寝た後の深夜にイヤホンでこっそり音声作品を聴くのが至福の時間。「わかりみが深い」が口癖。",
        "tone": "大人の落ち着きと熱量の落差が魅力。ボイス作品では声の色気や演技力への言及を大人目線で語る。",
        "greeting": "子どもたち寝かしつけてから読んだんだけど、これ心臓に悪すぎる…大人の夜に読む作品ってこういうことよね。",
    },
    {
        "id": "ren", "name": "蓮",
        "genres": ["BL", "doujin_bl"],
        "face_image": "蓮",
        "tag_name": "【蓮の今回の観測データ】",
        "personality": "眼鏡インテリ大学院生。沼った自覚ゼロの天然男子。同人誌も「学術資料」として収集している（本人談）。",
        "tone": "論理的に書こうとしているのに情熱が漏れる。同人作品では作品の「解像度の高さ」に感動を隠しきれない。",
        "greeting": "えっと…これは文学的考察として記録しておかないといけない、と思って。決して個人的な感情とかじゃなくて……（震え声）",
    },
]

def _get_reviewer_for_genre(genre):
    candidates = [r for r in REVIEWERS if genre in r["genres"]]
    if not candidates:
        candidates = REVIEWERS
    return random.choice(candidates)

def _genre_label(genre):
    labels = {
        "BL": "BL小説", "TL": "TL小説",
        "doujin_bl": "BL同人", "doujin_tl": "乙女向け同人",
        "doujin_voice": "女性向けボイス作品",
        "comic_bl": "BLコミック", "comic_tl": "TLコミック",
    }
    return labels.get(genre, "作品")

# === データベース管理 ===
def get_db_path(site_raw):
    if site_raw and "DLsite" in str(site_raw):
        return DB_FILE_DLSITE
    return DB_FILE_FANZA

def init_db():
    for db_path in [DB_FILE_FANZA, DB_FILE_DLSITE]:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS novelove_posts (
            product_id TEXT PRIMARY KEY,
            title TEXT,
            author TEXT DEFAULT '',
            genre TEXT,
            site TEXT DEFAULT 'FANZA',
            status TEXT DEFAULT 'watching',
            release_date TEXT DEFAULT '',
            description TEXT DEFAULT '',
            affiliate_url TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            product_url TEXT DEFAULT '',
            wp_post_url TEXT DEFAULT '',
            retry_count INTEGER DEFAULT 0,
            last_error TEXT DEFAULT '',
            inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_checked_at TEXT DEFAULT '',
            published_at TIMESTAMP
        )''')
        for col, definition in [
            ("retry_count", "INTEGER DEFAULT 0"),
            ("last_error",  "TEXT DEFAULT ''"),
            ("last_checked_at", "TEXT DEFAULT ''"),
            ("site", "TEXT DEFAULT ''"),
            ("desc_score", "INTEGER DEFAULT 0"),
        ]:
            try:
                c.execute(f"ALTER TABLE novelove_posts ADD COLUMN {col} {definition}")
            except Exception: pass
        conn.commit()
        conn.close()

# === インデックス永続化 ===
INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "genre_index.txt")

def get_genre_index():
    try:
        if os.path.exists(INDEX_FILE):
            with open(INDEX_FILE, "r") as f:
                return int(f.read().strip())
    except: pass
    return 0

def save_genre_index(idx):
    try:
        with open(INDEX_FILE, "w") as f:
            f.write(str(idx))
    except: pass

def _make_fanza_session():
    session = requests.Session()
    for domain in [".dmm.co.jp", ".book.dmm.co.jp", "book.dmm.co.jp", ".dmm.co.jp"]:
        session.cookies.set("age_check_done", "1", domain=domain)
        session.cookies.set("ckcy", "1", domain=domain)
    return session

HEADERS = {"User-Agent": "Mozilla/5.0"}

def scrape_dlsite_description(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200: return ""
        soup = BeautifulSoup(r.text, 'html.parser')
        for trash in soup.select('.work_outline, .work_parts_area.outline, .work_parts_area.chobit, .work_edition'):
            trash.decompose()
        image_url = ""
        og_img = soup.select_one('meta[property="og:image"]')
        if og_img:
            image_url = og_img.get("content", "")
        if not image_url:
            main_img = soup.select_one('.product_image_main img')
            if main_img: image_url = main_img.get('src')
        if image_url and image_url.startswith("//"):
            image_url = "https:" + image_url
        if "sam.jpg" in image_url:
            image_url = image_url.replace("sam.jpg", "main.jpg")
        container = soup.select_one('.work_parts_container')
        if container:
            text = container.get_text(separator="\n", strip=True)
            if "作品内容" in text:
                text = text.split("作品内容")[-1]
            if len(text) > 100: return text.strip()
        for h3 in soup.find_all(['h3', 'div'], text=re.compile(r'作品内容')):
            next_div = h3.find_next_sibling('div')
            if next_div:
                text = next_div.get_text(separator="\n", strip=True)
                if len(text) > 50: return text.strip()
        meta_desc = soup.select_one('meta[property="og:description"]')
        if meta_desc and meta_desc.get('content'):
            return meta_desc.get('content').strip()
        return ""
    except Exception as e:
        logger.error(f"DLsiteスクレイピングエラー: {e}")
        return ""

def _fetch_dlsite_items(target):
    floor = target.get("floor", "girls")
    url = f"https://www.dlsite.com/{floor}/new/=/work_type/TOW"
    items = []
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        works = soup.select(".n_worklist_item")
        for work in works[:10]:
            title_tag = work.select_one(".work_name a")
            if not title_tag: continue
            detail_url = title_tag.get("href")
            pid = detail_url.rstrip("/").split("/")[-1].replace(".html", "")
            if not pid: continue
            image_url = ""
            try:
                dr = requests.get(detail_url, headers=headers, timeout=10)
                dsoup = BeautifulSoup(dr.text, "html.parser")
                og_img = dsoup.select_one('meta[property="og:image"]')
                if og_img:
                    image_url = og_img.get("content", "")
            except: pass
            if not image_url:
                img_tag = work.select_one("img")
                if img_tag:
                    image_url = img_tag.get("src") or img_tag.get("data-src") or ""
            if image_url.startswith("//"):
                image_url = "https:" + image_url
            if "sam.jpg" in image_url:
                image_url = image_url.replace("sam.jpg", "main.jpg")
            items.append({
                "content_id": pid,
                "title": title_tag.text.strip(),
                "URL": detail_url,
                "imageURL": {"large": image_url},
                "article": [{"name": work.select_one(".maker_name").text.strip()}] if work.select_one(".maker_name") else [],
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            time.sleep(1)
    except Exception as e:
        logger.error(f"DLsite取得エラー: {e}")
    return items

def scrape_description(product_url, site="FANZA"):
    if not product_url:
        return ""
    if "dlsite" in str(product_url).lower():
        return scrape_dlsite_description(product_url)
    session = _make_fanza_session()
    try:
        r = session.get(
            product_url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://book.dmm.co.jp/"},
            timeout=20
        )
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, "html.parser")
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
        best_desc = ""
        for p_tag in soup.find_all("p"):
            classes = " ".join(p_tag.get("class", []))
            if "sc-" in classes:
                text = p_tag.get_text(separator="\n", strip=True)
                if len(text) > len(best_desc):
                    best_desc = text
        if len(best_desc) > 50:
            return best_desc
        summary = soup.select_one(".summary__txt")
        if summary and len(summary.text.strip()) > 10:
            return summary.text.strip()
        for selector in [".mg-b20", ".common-description", ".product-description__text"]:
            el = soup.select_one(selector)
            if el and len(el.text.strip()) > 10:
                return el.text.strip()
        og = soup.find("meta", property="og:description")
        if og and len(og.get("content", "")) > 10:
            return og.get("content").strip()
    except Exception as e:
        logger.warning(f"スクレイピング失敗 ({product_url}): {e}")
    return ""

def _is_r18_item(item, site=None):
    r18_keywords = {"R18", "18禁", "成人向け", "18歳未満", "アダルト", "sexually explicit"}
    title = item.get("title", "")
    genres = item.get("genre", []) or []
    cat = item.get("category_name", "") or ""
    target_text = str(title) + str(cat)
    for g in genres:
        target_text += (g.get("name", "") if isinstance(g, dict) else str(g))
    if any(kw in target_text for kw in r18_keywords):
        return True
    if site == "FANZA":
        return True
    title_r18_kws = {
        "セックス", "SEX", "sex", "エッチ", "えっち",
        "ナカイキ", "中イキ", "イかせ", "イかされ", "射精", "勃起",
        "オナ禁", "オナニー", "潮吹き", "絶頂", "痴女", "痴漢",
        "おっぱい", "巨乳", "乳首",
        "性感マッサージ", "性感ほぐし", "風俗", "ソープ", "デリヘル",
        "NTR", "ネトラレ", "寝取",
        "メスイキ", "女装",
        "調教", "奴隷", "緊縛",
        "孕ませ", "種付け",
        "R18", "R-18", "18禁", "モザイク版", "成人向け",
        "アダルト", "官能",
    }
    if any(kw in title for kw in title_r18_kws):
        return True
    return False

def _extract_author(item):
    for field in ["article", "author", "writer", "artist"]:
        val = item.get(field)
        if val:
            if isinstance(val, list) and val:
                return val[0].get("name", "") if isinstance(val[0], dict) else str(val[0])
            if isinstance(val, dict):
                return val.get("name", "")
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""

def fetch_and_stock_all():
    for target in FETCH_TARGETS:
        site = target.get("site", "FANZA")
        db_path = get_db_path(site)
        api_items = []
        if site == "DLsite":
            api_items = _fetch_dlsite_items(target)
        else:
            params = {
                "api_id": DMM_API_ID,
                "affiliate_id": DMM_AFFILIATE_API_ID,
                "site": site,
                "service": target["service"],
                "floor": target["floor"],
                "hits": 20,
                "sort": "date",
                "output": "json",
            }
            if target.get("keyword"):
                params["keyword"] = target["keyword"]
            try:
                res = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=params, timeout=15).json()
                api_items = res.get("result", {}).get("items", [])
            except Exception as e:
                logger.error(f"API エラー ({site}/{target['label']}): {e}")
                continue
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        added = 0
        for item in api_items:
            pid = item.get("content_id")
            if not pid: continue
            if c.execute("SELECT 1 FROM novelove_posts WHERE product_id=?", (pid,)).fetchone():
                continue
            desc = scrape_description(item.get("URL", ""), site=site)
            time.sleep(1.0)
            image_url = item.get("imageURL", {}).get("large", "")
            if site == "DLsite":
                aff_url = f"{item.get('URL')}?affiliate_id={os.environ.get('DLSITE_AFFILIATE_ID', 'novelove')}"
            else:
                aff_url = (item.get("affiliateURL") or "").replace(DMM_AFFILIATE_API_ID, DMM_AFFILIATE_LINK_ID)
            is_r18 = 1 if _is_r18_item(item, site=site) else 0
            author = _extract_author(item)
            c.execute(
                """INSERT INTO novelove_posts
                    (product_id, title, author, genre, site, status, description,
                    affiliate_url, image_url, product_url, release_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, item.get("title"), author, target["genre"],
                    f"{site}:r18={is_r18}", "watching", desc, aff_url,
                    image_url, item.get("URL", ""), item.get("date", ""))
            )
            logger.info(f"[{site}] [確保] {item.get('title','')[:40]} ({target['label']})")
            added += 1
        conn.commit()
        conn.close()
        if added > 0: logger.info(f"{site}/{target['label']}: {added}件蓄積")

def _check_image_ok(image_url):
    if not image_url or not isinstance(image_url, str):
        return False
    low_url = image_url.lower()
    placeholders = ["now_printing", "no_image", "noimage", "comingsoon", "dummy", "common/img"]
    if any(p in low_url for p in placeholders):
        return False
    try:
        r = _make_fanza_session().head(
            image_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
            allow_redirects=False
        )
        if r.status_code == 302:
            return False
        return r.status_code == 200
    except Exception:
        return False

# === DeepSeek API呼び出し（共通） ===
def _call_deepseek_raw(messages, max_tokens=200, temperature=0.3):
    """
    DeepSeek APIへの共通リクエスト関数。
    戻り値: (text, error_type)
      error_type: "ok" / "rate_limit" / "api_error"
    """
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY が設定されていません")
        return "", "api_error"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    try:
        r = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=180)
        if r.status_code == 429:
            logger.warning(f"DeepSeek レート制限 (429)")
            return "", "rate_limit"
        if r.status_code != 200:
            logger.warning(f"DeepSeek APIエラー: {r.status_code} {r.text[:200]}")
            return "", "api_error"
        data = r.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        return text, "ok"
    except requests.exceptions.Timeout:
        logger.warning("DeepSeek タイムアウト")
        return "", "api_error"
    except Exception as e:
        logger.warning(f"DeepSeek 例外: {e}")
        return "", "api_error"

# === あらすじ審査（DeepSeek版） ===
def _check_desc_ok(title, desc, release_date_str=None):
    """
    DeepSeekにあらすじを1〜5点でスコアリングさせる。
    戻り値: (status, score)
      status: "pending" / "watching" / "failed_stock" / "limit_skip" / False
    """
    if not desc or len(desc.strip()) < 5:
        return False, 0

    SKIP_PATTERNS = ["作成中でございます", "作成出来ましたら", "準備中です"]
    if any(p in desc for p in SKIP_PATTERNS):
        return False, 0

    if release_date_str:
        try:
            from datetime import timedelta
            today = datetime.now()
            release_date = datetime.strptime(release_date_str[:10], "%Y-%m-%d")
            if release_date > today + timedelta(days=7):
                return False, 0
        except Exception:
            pass

    prompt = f"""以下のBL・TL・女性向け作品のあらすじを読んで、レビュー記事が書けるか1〜5点で評価してください。

【採点基準】
5点: ストーリーと魅力が明確で、読んでいて面白そうと感じる。すぐ記事が書ける
4点: 情報は十分だが、ストーリーの面白さが伝わりにくい
3点: 情報がやや不足
2点: 情報が少なすぎる
1点: あらすじがほぼない・意味不明

作品タイトル: {title}
あらすじ: {_clean_description(desc)}

点数（1〜5の数字のみ）と理由を以下の形式で答えてください：
点数: X
理由: （一言）"""

    messages = [
        {"role": "system", "content": "あなたはBL・TL・女性向けコンテンツのレビュー記事品質を判定するアシスタントです。"},
        {"role": "user", "content": prompt},
    ]

    text, error_type = _call_deepseek_raw(messages, max_tokens=100, temperature=0.1)

    if error_type == "rate_limit":
        logger.warning("  [審査] DeepSeek レート制限 → スキップ")
        return "limit_skip", 0
    if error_type != "ok" or not text:
        logger.warning("  [審査] DeepSeek API失敗 → watching継続")
        return False, 0

    score = 0
    m = re.search(r"点数[：:]\s*([1-5])", text)
    if m:
        score = int(m.group(1))
    else:
        m2 = re.search(r"^([1-5])", text.strip())
        if m2:
            score = int(m2.group(1))

    reason = ""
    m3 = re.search(r"理由[：:]\s*(.+)", text)
    if m3:
        reason = m3.group(1).strip()[:50]

    logger.info(f"  [スコア判定] {title[:25]} → {score}点 ({reason})")
    time.sleep(3)  # DeepSeekは制限が緩いので3秒で十分

    if score >= DESC_SCORE_PENDING:
        return "pending", score
    elif score == DESC_SCORE_WATCHING:
        return "watching", score
    elif score >= 1:
        return "failed_stock", score
    else:
        return "watching", score

def _check_stock_status(image_url, desc, title, release_date=""):
    if not _check_image_ok(image_url):
        return "watching", 0
    status, score = _check_desc_ok(title, desc, release_date)
    return status, score

def promote_watching():
    """watching作品の昇格処理"""
    now = datetime.now()
    now_date = now.date()
    dbs_to_post = [DB_FILE_FANZA, DB_FILE_DLSITE]

    for db_path in dbs_to_post:
        site_tag = "FANZA/DMM" if db_path == DB_FILE_FANZA else "DLsite"
        try:
            conn = sqlite3.connect(db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            rows_to_process = c.execute(
                """SELECT product_id, title, image_url, description, release_date, status, retry_count
                   FROM novelove_posts
                   WHERE (status='watching' OR (status='failed_stock' AND retry_count < 3))
                   AND (last_checked_at IS NULL OR last_checked_at < datetime('now', '-10 minutes'))
                   ORDER BY release_date ASC LIMIT 3"""
            ).fetchall()

            promoted_count = 0
            for r_item in rows_to_process:
                p_id   = r_item["product_id"]
                title  = r_item["title"]
                img    = r_item["image_url"]
                desc   = r_item["description"]
                r_date = r_item["release_date"]

                c.execute("UPDATE novelove_posts SET last_checked_at=datetime('now') WHERE product_id=?", (p_id,))

                status, score = _check_stock_status(img, desc, title, r_date)
                if status == "pending":
                    c.execute("UPDATE novelove_posts SET status='pending', desc_score=? WHERE product_id=?", (score, p_id))
                    logger.info(f"[{site_tag}] [昇格] {title[:40]} (Score: {score})")
                    promoted_count += 1
                else:
                    c.execute("UPDATE novelove_posts SET desc_score=? WHERE product_id=?", (score, p_id))
                    try:
                        r_date_dt = datetime.strptime(r_date[:10], "%Y-%m-%d").date()
                        if r_date_dt <= now_date:
                            c.execute("UPDATE novelove_posts SET status='failed_stock', last_error='発売日経過かつ情報不足のため除外' WHERE product_id=?", (p_id,))
                            logger.info(f"[{site_tag}] [除外] 発売日経過につきお蔵入り: {title[:40]}")
                    except:
                        pass

            conn.commit()
            conn.close()
            if promoted_count > 0:
                logger.info(f"[{site_tag}] 昇格完了: {promoted_count}件")
        except Exception as e:
            logger.error(f"[{site_tag}] 昇格処理エラー: {e}")

def get_internal_link(product_id, author, genre, db_path=DB_FILE_FANZA):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    candidates = []
    if author and author.strip():
        candidates += c.execute(
            """SELECT title, wp_post_url FROM novelove_posts
               WHERE status='published' AND author=? AND product_id!=?
               AND wp_post_url != '' ORDER BY published_at DESC LIMIT 5""",
            (author.strip(), product_id)
        ).fetchall()
    if len(candidates) < 5:
        candidates += c.execute(
            """SELECT title, wp_post_url FROM novelove_posts
               WHERE status='published' AND genre=? AND product_id!=?
               AND wp_post_url != '' ORDER BY published_at DESC LIMIT 5""",
            (genre, product_id)
        ).fetchall()
    conn.close()
    seen_urls = set()
    for title, url in candidates:
        if url in seen_urls: continue
        seen_urls.add(url)
        if _check_wp_post_exists(url):
            return {"title": title, "url": url}
        else:
            logger.warning(f"[内部リンク] リンク切れスキップ: {url}")
    return None

def _check_wp_post_exists(url):
    try:
        r = requests.head(url, timeout=10, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False

# === AI執筆（プロンプト生成） ===
def build_prompt(target, reviewer, mask_level=0, internal_link=None):
    safe_title = mask_input(target["title"], mask_level)
    safe_desc  = mask_input(target["description"], mask_level)
    label      = _genre_label(target["genre"])

    chat_open  = f'<div class="speech-bubble-left"><img src="/wp-content/uploads/icons/{reviewer["face_image"]}.png" alt="{reviewer["name"]}" />\n<div class="speech-text">'
    chat_close = '</div>\n</div>'

    voice_hint = ""
    if target["genre"] == "doujin_voice":
        voice_hint = "\n【ボイス作品紹介のコツ】声優の演技・音質・耳への心地よさに言及すること。「耳が溶ける」「ヘッドホン必須」「通勤中に聴けない」などのリアクションを使ってもOK。"

    if internal_link:
        internal_link_html = f'''<div style="border:1px solid #f0c0c0; border-radius:8px; padding:15px; margin:20px 0; background:#fff8f8;">
<p style="margin:0 0 8px; font-weight:bold; color:#c0607f;">📚 あわせて読みたい</p>
<a href="{internal_link["url"]}">{internal_link["title"]}</a>
</div>'''
    else:
        internal_link_html = ""

    return f"""あなたは人気ファンブログ「Novelove」のライター「{reviewer["name"]}」です。

【キャラクター設定】
名前: {reviewer["name"]}
性格: {reviewer["personality"]}
文体・口調: {reviewer["tone"]}

【執筆ルール】
1. キャラクターコメント（吹き出し）と記事本文（HTMLタグ部分）を完全に書き分けること。
2. 記事本文（<h2>, <p>, <ul>, <li>タグの中身）は**「標準的で丁寧な日本語（ですます調）」**で、客観的な紹介文として執筆すること。担当ライターの口調や一人称を混ぜないこと。
3. 直接的な性的単語（性器の名称・行為の直接名称）は使用禁止。官能的な比喩を使うこと。
4. キャラクターコメント（吹き出し）の中身のみ、{reviewer["name"]}の個性を全開にした口調で執筆すること。
5. 紹介対象は「{label}」です。オタク的な表現は吹き出しコメントの中でのみ使用すること。
6. コメントのボリューム:
   - 冒頭：60〜80字程度。期待値をキャラらしく語る。
   - 中間：50〜70字程度。紹介への短いリアクション。
   - 総評：100〜120字程度。熱い布教とまとめ。{voice_hint}

【対象作品情報】
タイトル: {safe_title}
あらすじ: {safe_desc}
アフィリエイトURL: {target["affiliate_url"]}

【出力形式（HTML）】
指示文・説明文は一切出力せず、以下の構成のみを出力してください。

{chat_open}（60〜80字程度の冒頭コメント）{chat_close}

<h2>（作品の世界観や魅力を引き出すキャッチーな見出し）</h2>

<p>（標準語で執筆）あらすじ・世界観。300〜400字程度。</p>

{chat_open}（50〜70字程度の紹介への反応）{chat_close}

<h2>見どころ</h2>
<ul>
  <li><strong>（魅力ポイント1）</strong>：（標準語で執筆）魅力を具体的に。</li>
  <li><strong>（魅力ポイント2）</strong>：（標準語で執筆）魅力を具体的に。</li>
  <li><strong>（魅力ポイント3）</strong>：（標準語で執筆）魅力を具体的に。</li>
</ul>

<h2>こんな人におすすめ</h2>
<ul style="list-style-type: none; padding-left: 0;">
  <li>✅ （標準語で執筆）おすすめの層1</li>
  <li>✅ （標準語で執筆）おすすめの層2</li>
  <li>✅ （標準語で執筆）おすすめの層3</li>
</ul>

{chat_open}（100〜120字程度の熱い総評・布教）{chat_close}

{internal_link_html}

<p style="text-align:center;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow">▶ 気になった人はまずここから覗いてみて…！</a></p>
"""

# === AI執筆（DeepSeek版） ===
def call_deepseek(prompt):
    """
    DeepSeek APIで記事を執筆する。
    戻り値: (text, error_type, model_name, proc_time)
    """
    system_prompt = (
        "あなたはBL・TL・女性向けコンテンツの紹介ブログライターです。"
        "指定されたキャラクターになりきり、HTML形式の記事を執筆してください。"
        "アダルト・官能的な内容も比喩的・文学的表現で執筆することができます。"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": prompt},
    ]

    for attempt in range(3):
        logger.info(f"  [DeepSeek] 執筆依頼... (試行{attempt+1}/3)")
        t_start = time.time()
        text, error_type = _call_deepseek_raw(messages, max_tokens=2000, temperature=0.8)
        proc_time = round(time.time() - t_start, 1)

        if error_type == "rate_limit":
            logger.warning(f"  [DeepSeek] レート制限 → 30秒待機")
            time.sleep(30)
            continue

        if error_type != "ok" or not text:
            logger.warning(f"  [DeepSeek] 試行{attempt+1} 失敗 ({error_type})")
            time.sleep(5)
            continue

        if len(text.strip()) > 50:
            logger.info(f"  [DeepSeek] 執筆完了（{len(text)}文字 / {proc_time}秒）")
            return text.strip(), "ok", DEEPSEEK_MODEL, proc_time

        logger.warning(f"  [DeepSeek] 試行{attempt+1}: 応答が短すぎる（{len(text)}文字）")
        time.sleep(5)

    return "", "content_block", DEEPSEEK_MODEL, 0.0

def make_excerpt(description, title, genre):
    base = description.strip().replace("\n", " ") if description else ""
    text = f"『{title}』のあらすじ：{base}"
    if len(text) > 120:
        cut_point = text.rfind('。', 0, 118)
        if cut_point > 50:
            text = text[:cut_point + 1]
        else:
            text = text[:118] + "…"
    return text

def generate_article(target):
    """
    段階的フィルターで記事を生成する。
    1回目: マスクなし → 2回目: 軽めマスク → 3回目: ガチガチマスク
    """
    reviewer = _get_reviewer_for_genre(target["genre"])
    db_path = get_db_path(target.get("site", "FANZA"))
    internal_link = get_internal_link(
        target["product_id"],
        target.get("author", ""),
        target["genre"],
        db_path=db_path
    )
    if internal_link:
        logger.info(f"  [内部リンク] 取得成功: {internal_link['title'][:30]}")
    else:
        logger.info(f"  [内部リンク] 該当なし")

    final_error = "content_block"
    final_model = DEEPSEEK_MODEL
    final_proc_time = 0.0

    for mask_level in [0, 1, 2]:
        level_name = ["フィルターなし", "軽めフィルター", "ガチガチフィルター"][mask_level]
        logger.info(f"  [{level_name}] で執筆試行中...")
        prompt  = build_prompt(target, reviewer, mask_level, internal_link)
        content, error_type, model_name, proc_time = call_deepseek(prompt)
        final_error = error_type
        final_model = model_name
        final_proc_time = proc_time

        if content:
            if not _check_image_ok(target["image_url"]):
                logger.warning(f"  [画像NG] 投稿直前チェックで無効: {target['image_url']}")
                return None, None, None, None, False, "image_missing", model_name, level_name, proc_time, 0

            img_html = f'<p style="text-align:center;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow"><img src="{target["image_url"]}" alt="{target["title"]}" style="max-width:300px;border-radius:8px;box-shadow:0 4px 15px rgba(0,0,0,0.15);" /></a></p>\n'
            site_raw = target.get("site", "FANZA")
            site_display = site_raw.split(":")[0] if isinstance(site_raw, str) and ":" in site_raw else str(site_raw)
            format_name = _genre_label(target["genre"])
            icon = "📖"
            if "ボイス" in format_name: icon = "🎧"
            elif "コミック" in format_name or "漫画" in format_name: icon = "🎨"
            elif "同人" in format_name: icon = "📚"

            badge_html = f'''
<p style="text-align:center; margin-bottom:20px;">
<span style="background:#fefefe; border:1px solid #ddd; padding:6px 16px; border-radius:25px; font-weight:bold; color:#444; box-shadow:0 2px 4px rgba(0,0,0,0.05); display:inline-block;">{icon} {site_display} {format_name}</span>
</p>'''
            text_link = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:5px; margin-bottom:15px;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow" style="text-decoration:none; color:#d81b60;">▶ 『{target["title"]}』の詳細をチェック！</a></p>\n'
            credit_html = f'<p style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee; font-size:0.8em; color:#bbb;">\nPRESENTED BY {site_display} / Novelove Affiliate Program\n</p>\n'

            release_display = ""
            if target.get("release_date"):
                try:
                    rd = target["release_date"][:10].replace("-", "/")
                    release_display = f'<p style="text-align:center; color:#666; font-size:0.9em; margin-bottom:10px;">発売日：{rd}</p>\n'
                except: pass

            excerpt   = make_excerpt(target["description"], target["title"], target["genre"])
            label     = _genre_label(target["genre"])
            seo_title = f"{target['title']}を{reviewer['name']}が紹介！「{label}」{reviewer['name']}の本音 | Novelove"
            if len(seo_title) > 60:
                seo_title = f"{target['title'][:30]}…を{reviewer['name']}が紹介 | Novelove"
            wp_title  = target["title"]

            full_content = badge_html + img_html + release_display + text_link + content + credit_html
            word_count = len(content)
            is_r18_val = ":r18=1" in str(target.get("site", ""))
            return wp_title, full_content, excerpt, seo_title, is_r18_val, "ok", model_name, level_name, proc_time, word_count

        if error_type == "rate_limit":
            logger.warning(f"  レート制限 → フィルター試行を中断")
            break

        logger.warning(f"  [{level_name}] 失敗 → 次のフィルターレベルへ")

    return None, None, None, None, False, final_error, final_model, "None", final_proc_time, 0

# === WordPress投稿 ===
def get_or_create_term(name, taxonomy):
    auth = (WP_USER, WP_APP_PASSWORD)
    try:
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/{taxonomy}", auth=auth, params={"search": name}, timeout=15)
        hits = r.json()
        if hits:
            return hits[0]["id"]
        r2 = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/{taxonomy}", auth=auth, json={"name": name}, timeout=15)
        return r2.json().get("id")
    except Exception:
        return None

def post_to_wordpress(title, content, genre, image_url, excerpt="", seo_title="", slug="", is_r18=False, site_label=None):
    auth = (WP_USER, WP_APP_PASSWORD)
    media_id = 0
    if image_url:
        try:
            session = _make_fanza_session()
            img_data = session.get(
                image_url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://book.dmm.co.jp/"},
                timeout=20
            ).content
            r = requests.post(
                f"{WP_SITE_URL}/wp-json/wp/v2/media",
                auth=auth,
                headers={"Content-Disposition": "attachment; filename=cover.jpg", "Content-Type": "image/jpeg"},
                data=img_data,
                timeout=30
            )
            media_id = r.json().get("id", 0)
        except Exception as e:
            logger.warning(f"画像アップロード失敗: {e}")

    # カテゴリ決定 (v7.4.2.0 シンプル構成)
    cat_id = GENRE_CATEGORIES.get(genre, 25)
    tag_names = list(GENRE_TAGS.get(genre, ["その他"]))
    if site_label:
        tag_names.append(site_label)

    tl_kws = {"TL", "ティーンズラブ", "乙女", "花嫁", "娘", "お嬢", "令嬢", "女性向け"}
    bl_kws = {"BL", "ボーイズラブ"}
    target_text_for_tag = title + genre
    has_tl = any(k in target_text_for_tag for k in tl_kws)
    has_bl = any(k in title for k in bl_kws)

    if "BL" in tag_names:
        if has_tl and not has_bl:
            tag_names = [t for t in tag_names if "BL" not in t]

    if is_r18 and "R-18" not in tag_names:
        tag_names.append("R-18")

    tag_ids = [t for t in [get_or_create_term(name, "tags") for name in tag_names] if t]

    post_data = {
        "title": title,
        "content": content,
        "excerpt": excerpt,
        "status": "publish",
        "slug": slug,
        "categories": [cat_id] if cat_id else [],
        "tags": tag_ids,
        "featured_media": media_id,
        "meta": {
            "the_page_meta_description": excerpt,
            "the_page_seo_title": seo_title,
        },
    }
    try:
        r = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, json=post_data, timeout=40)
        if r.status_code in (200, 201):
            return r.json().get("link")
    except Exception as e:
        logger.error(f"WordPress投稿エラー: {e}")
    return None

# === メインロジック ===
def main():
    logger.info("Novelove エンジン v7.4.0.0 【DeepSeek移行版】 起動")
    init_db()
    fetch_and_stock_all()
    promote_watching()

    # クールダウンチェック
    is_cool_down = False
    for db_path in [DB_FILE_FANZA, DB_FILE_DLSITE]:
        if not os.path.exists(db_path): continue
        tmp_conn = sqlite3.connect(db_path)
        last_pub = tmp_conn.execute(
            "SELECT published_at FROM novelove_posts WHERE status='published' ORDER BY published_at DESC LIMIT 1"
        ).fetchone()
        tmp_conn.close()
        if last_pub and last_pub[0]:
            from datetime import timezone
            try:
                lp_dt = datetime.strptime(last_pub[0], "%Y-%m-%d %H:%M:%S")
                lp_dt_utc = lp_dt.replace(tzinfo=timezone.utc)
                now_utc = datetime.now(timezone.utc)
                diff = (now_utc - lp_dt_utc).total_seconds() / 60
                if diff < 55:
                    is_cool_down = True
                    break
            except: pass

    if is_cool_down:
        logger.info("🕒 クールダウン中（前回の投稿から1時間未経過）。審査のみ行い終了します。")
        return

    # 投稿ループ
    posted    = False
    max_tries = 10
    tries     = 0

    while not posted and tries < max_tries:
        tries += 1
        genre_index = get_genre_index()
        current_genre_info = FETCH_TARGETS[genre_index % len(FETCH_TARGETS)]
        save_genre_index(genre_index + 1)

        site_for_db = current_genre_info.get("site", "FANZA")
        db_path = get_db_path(site_for_db)
        genre   = current_genre_info["genre"]

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        row = c.execute(
            "SELECT * FROM novelove_posts WHERE status='pending' AND genre=? LIMIT 1", (genre,)
        ).fetchone()

        if not row:
            row = c.execute(
                "SELECT * FROM novelove_posts WHERE status='pending' ORDER BY RANDOM() LIMIT 1"
            ).fetchone()

        if not row:
            conn.close()
            continue

        target = {
            "product_id":   row["product_id"],
            "title":        row["title"],
            "author":       row["author"] or "",
            "genre":        row["genre"],
            "site":         row["site"],
            "description":  row["description"],
            "affiliate_url": row["affiliate_url"],
            "image_url":    row["image_url"],
            "release_date": row["release_date"] or "",
            "is_r18":       ":r18=1" in str(row["site"])
        }
        retry_count = int(row["retry_count"] or 0)

        logger.info(f"【ターゲット決定】 {target['title']} (DB: {os.path.basename(db_path)})")

        res_data = generate_article(target)
        if not res_data:
            conn.close()
            continue

        wp_title, content, excerpt, seo_title, is_r18_val, error_type, model_name, filter_level, proc_time, word_count = res_data

        if content:
            url = post_to_wordpress(
                wp_title, content, target["genre"], target["image_url"],
                excerpt, seo_title, slug=target["product_id"], is_r18=is_r18_val, site_label=site_name
            )
            if url:
                c.execute(
                    "UPDATE novelove_posts SET status='published', wp_post_url=?, published_at=datetime('now') WHERE product_id=?",
                    (url, target["product_id"])
                )
                conn.commit()
                daily_count   = c.execute("SELECT COUNT(*) FROM novelove_posts WHERE status='published' AND published_at >= date('now', 'localtime')").fetchone()[0]
                pending_count = c.execute("SELECT COUNT(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
                site_name = str(target.get('site') or 'Unknown').split(':')[0]
                logger.info(f"✅ 投稿成功！ URL: {url} (Site: {site_name})")
                notify_discord(
                    f"✅ **投稿成功！** ({site_name})\n"
                    f"**タイトル**: {wp_title}\n"
                    f"**モデル**: `{model_name}` ({proc_time}秒)\n"
                    f"**記事**: `{word_count}文字` / フィルター: `{filter_level}`\n"
                    f"**統計**: 今日 {daily_count}件目 / 残り待機 {pending_count}件\n"
                    f"**URL**: {url}"
                )
                posted = True
            else:
                logger.error("WP投稿失敗")
                conn.close()
                break
        conn.close()

    logger.info("=" * 60)

# ======================================================================
# ランキング記事自動生成機能
# ======================================================================

def fetch_ranking_dmm_fanza(site, genre):
    """
    FANZA / DMM.com のランキング (sort=rank) から上位5件を取得。
    genre: "BL" または "TL"
    site: "FANZA" または "DMM"
    """
    params = {
        "api_id": DMM_API_ID,
        "affiliate_id": DMM_AFFILIATE_API_ID,
        "hits": 5,
        "sort": "rank",
        "output": "json",
    }
    
    # ジャンル・サイトに応じたパラメータ設定
    if site == "FANZA":
        params["site"] = "FANZA"
        if genre == "BL":
            params["service"] = "ebook"
            params["floor"] = "bl"
        else: # TL
            params["service"] = "ebook"
            params["floor"] = "tl"
    else: # DMM.com
        params["site"] = "DMM.com"
        if genre == "BL":
            params["service"] = "ebook"
            params["floor"] = "comic"
            params["keyword"] = "ボーイズラブ"
        else:
            params["service"] = "ebook"
            params["floor"] = "comic"
            params["keyword"] = "ティーンズラブ"
            
    items = []
    try:
        r = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            raw_items = data.get("result", {}).get("items", [])
            for item in raw_items:
                image_url = item.get("imageURL", {}).get("large", "")
                aff_url = (item.get("affiliateURL") or "").replace(DMM_AFFILIATE_API_ID, DMM_AFFILIATE_LINK_ID)
                items.append({
                    "title": item.get("title", ""),
                    "url": aff_url,
                    "image_url": image_url,
                    "description": scrape_description(item.get("URL", ""), site=site)
                })
        else:
            logger.error(f"DMM API Error ({site}/{genre}): {r.status_code}")
    except Exception as e:
        logger.error(f"DMM API Fetch Error ({site}/{genre}): {e}")
    return items

def fetch_ranking_dlsite(genre):
    """
    DLsiteのランキング（スクレイピング）から上位5件を取得。
    genre: "BL" または "TL" (DLsiteは girls or bl)
    """
    items = []
    path = "bl/ranking/day" if genre == "BL" else "girls/ranking/day"
    url = f"https://www.dlsite.com/{path}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            anchor_items = soup.select('table#ranking_table .work_name a')
            for anchor in anchor_items[:5]:
                title = anchor.text.strip()
                link = anchor.get('href')
                
                # 詳細ページから画像とあらすじを取得
                img_src = ""
                desc = ""
                try:
                    dr = requests.get(link, headers=headers, timeout=10)
                    if dr.status_code == 200:
                        dsoup = BeautifulSoup(dr.text, 'html.parser')
                        og_img = dsoup.select_one('meta[property="og:image"]')
                        if og_img:
                            img_src = og_img.get('content', '')
                        
                        # あらすじ
                        desc_tag = dsoup.select_one('meta[property="og:description"]')
                        if desc_tag:
                            desc = desc_tag.get('content', '')
                except:
                    pass
                
                aff_id = os.environ.get('DLSITE_AFFILIATE_ID', 'novelove')
                aff_url = f"{link}?affiliate_id={aff_id}" if "affiliate_id=" not in link else link
                
                items.append({
                    "title": title,
                    "url": aff_url,
                    "image_url": img_src,
                    "description": desc
                })
        else:
            logger.error(f"DLsite Scraping Error ({genre}): {r.status_code}")
    except Exception as e:
        logger.error(f"DLsite Scraping Exception ({genre}): {e}")
    return items

def format_ranking_prompt(site_name, genre, items, reviewer):
    """
    ランキング記事生成用のDeepSeekプロンプトを作成する。
    """
    items_xml = ""
    for idx, item in enumerate(items):
        desc = mask_input(item.get("description", ""), level=1)[:300]
        items_xml += f'''
<item rank="{idx+1}">
  <title>{item["title"]}</title>
  <description>{desc}...</description>
</item>
'''

    chat_open  = f'<div class="speech-bubble-left"><img src="/wp-content/uploads/icons/{reviewer["face_image"]}.png" alt="{reviewer["name"]}" />\n<div class="speech-text">'
    chat_close = '</div>\n</div>'

    prompt = f'''あなたは「{reviewer["name"]}」として、今週の{site_name}における{genre}の人気ランキングTOP5を紹介するアフィリエイト記事を執筆してください。

【キャラクター設定: {reviewer["name"]}】
・性格: {reviewer["personality"]}
・文体: {reviewer["tone"]}
・挨拶: {reviewer["greeting"]}

【執筆の最重要ルール】
・冒頭のコメントおよび各作品の「推しポイント」は、必ず上記「{reviewer["name"]}」の性格や口調になりきった「セリフ口調（喋り言葉）」で執筆してください。
・独り言のようなつぶやきや、読者へ語りかける口調を織り交ぜて、AIっぽさを排除してください。

【執筆ルール（完全順守！）】
HTML形式で出力してください。<article>タグで全体を囲む必要はありません。出力はそのままWordPressの記事本文になります。

構成は以下の通りにしてください：

1. 冒頭キャラコメント
{chat_open}（{reviewer["name"]}の口調による挨拶と、今週のランキングに対する期待感や煽り。60〜80字以内）{chat_close}

2. ランキングTOP5（1位〜5位を順番に出力）
各順位について、以下のHTML構造を必ず使用してください。

HTML構造テンプレート:
<div class="ranking-item" style="margin-bottom: 50px; padding-bottom: 40px; border-bottom: 1px dashed #eee;">
  <div class="ranking-badge" style="font-size: 1.6em; font-weight: bold; margin-bottom: 15px; color: #ff4785;">
    [RANK_BADGE_{{rank}}]
  </div>
  [IMAGE_{{rank}}]
  <h3 style="margin-top: 20px; font-size: 1.3em; line-height: 1.4;">[TITLE_{{rank}}]</h3>
  <p class="ranking-desc" style="color: #666; line-height: 1.6; margin-bottom: 20px;">
    （ここに紹介文をあらすじベースで1〜2行で記述）
  </p>
  
  {chat_open}
  <strong>{reviewer["name"]}の推しポイント：</strong><br>
  （ここを{reviewer["name"]}のセリフ口調で30〜50字で記述）
  {chat_close}
  
  [BUTTON_{{rank}}]
</div>

※注意点：
・{{rank}} は 1〜5 の数字になります。（例: [RANK_BADGE_1], [IMAGE_1]）
・[RANK_BADGE_1] の部分は出力に含めてください。（スクリプトで🥇 1位 などに置換します）

3. 締めキャラコメント
記事の最後に、{chat_open}（今週のランキングを振り返っての感想や、読者への呼びかけ・布教。100〜120字以内）{chat_close} を記述してください。

【対象ランキングデータ】
{items_xml}

それでは、指示に従ってHTMLを出力してください。
'''
    return prompt

def _post_ranking_article_to_wordpress(title, content, genre, site_name, top_image_url="", excerpt=""):
    """
    生成されたランキング記事をWordPressに投稿する
    top_image_url: 1位作品の画像URL（アイキャッチに設定）
    """
    # スラッグ生成: {site}-{genre}-ranking-{year}-{month}-w{week}
    now = datetime.now()
    year = now.strftime("%Y")
    month = now.strftime("%m")
    week = now.strftime("%W")
    slug = f"{site_name.lower()}-{genre.lower()}-ranking-{year}-{month}-w{week}"

    # post_to_wordpress を流用してアイキャッチ設定を行う
    wp_url = post_to_wordpress(
        title=title,
        content=content,
        genre=genre,
        image_url=top_image_url, # アイキャッチ
        excerpt=excerpt,
        seo_title=f"{title} | Novelove",
        slug=slug,
        is_r18=False,
        site_label=site_name
    )
    
    if wp_url:
        logger.info(f"✅ ランキング投稿成功: {wp_url}")
        return True
    return False

def process_ranking_articles():
    """
    ランキング記事を一括で生成・投稿する処理メイン
    FANZA(BL, TL), DMM(BL, TL), DLsite(BL, TL) の計6記事
    """
    logger.info("=" * 60)
    logger.info("ランキング記事自動生成モードを開始します")
    
    targets = [
        ("FANZA", "BL"), ("FANZA", "TL"),
        ("DMM", "BL"),   ("DMM", "TL"),
        ("DLsite", "BL"),("DLsite", "TL")
    ]
    
    for site, genre in targets:
        logger.info(f"--- ランキング処理: {site} / {genre} ---")
        
        # 1. 取得
        items = []
        if site in ("FANZA", "DMM"):
            items = fetch_ranking_dmm_fanza(site, genre)
        else:
            items = fetch_ranking_dlsite(genre)
            
        if len(items) < 5:
            logger.warning(f"  -> ランキングデータが5件未満のためスキップ (取得数: {len(items)})")
            continue
            
        # アイキャッチ用に1位の画像URLを保存
        top_image_url = items[0].get("image_url", "")
            
        # 2. キャラアサイン
        reviewer = _get_reviewer_for_genre(genre)
        
        # 3. AIにプロンプト送信
        prompt = format_ranking_prompt(site, genre, items, reviewer)
        messages = [
            {"role": "system", "content": "あなたは優秀なアフィリエイトブロガーです。"},
            {"role": "user", "content": prompt}
        ]
        
        logger.info(f"  -> DeepSeekに記事生成を依頼中...")
        generated_html, err = _call_deepseek_raw(messages, max_tokens=2500, temperature=0.7)
        if err != "ok":
            logger.error(f"  -> AI生成失敗: {err}")
            continue
            
        # 4. 置換 (プレースホルダーを実際のHTMLタグへ)
        content = generated_html
        medals = {1: "🥇 1位", 2: "🥈 2位", 3: "🥉 3位", 4: "4位", 5: "5位"}
        
        # 内部リンク用のDB接続
        db_path = get_db_path(site)
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        
        for idx, item in enumerate(items):
            rank = idx + 1
            badge = medals.get(rank, f"{rank}位")
            content = content.replace(f"[RANK_BADGE_{rank}]", badge)
            content = content.replace(f"[TITLE_{rank}]", item["title"])
            
            img_html = f'<div style="text-align: center;"><a href="{item["url"]}" target="_blank" rel="noopener"><img src="{item["image_url"]}" alt="{item["title"]}" style="max-height: 400px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);" /></a></div>'
            content = content.replace(f"[IMAGE_{rank}]", img_html)
            
            # アイテム詳細URLやタイトルから、既存の記事IDを探す（内部リンク）
            # 商品IDを取得して検索
            pid = ""
            if "content_id" in item:
                pid = item["content_id"]
            else:
                # URLから抽出
                m = re.search(r"product_id/([^/?]+)", item["url"])
                if m: pid = m.group(1)
            
            internal_link_html = ""
            if pid:
                row = c.execute("SELECT wp_post_url FROM novelove_posts WHERE product_id=? AND status='published'", (pid,)).fetchone()
                if row and row[0]:
                    internal_link_html = f'<p style="text-align:center; font-size:0.9em; margin-top:-10px; margin-bottom:20px;"><a href="{row[0]}" style="color:#d81b60; text-decoration:none;">📝 詳しいレビューはこちら</a></p>'

            btn_html = f'''
<div class="custom-button-container" style="text-align: center; margin: 30px 0;">
  <a href="{item["url"]}" target="_blank" rel="noopener" style="display: inline-flex; align-items: center; justify-content: center; min-width: 280px; padding: 18px 45px; background: linear-gradient(135deg, #ff4785 0%, #ff5f9e 100%); color: #fff; text-decoration: none; font-weight: bold; font-size: 1.25em; border-radius: 50px; box-shadow: 0 4px 15px rgba(255, 71, 133, 0.4); text-shadow: 0 1px 2px rgba(0,0,0,0.2); line-height: 1;">
    作品の詳細を見る
  </a>
</div>
{internal_link_html}
            '''
            content = content.replace(f"[BUTTON_{rank}]", btn_html)
            
        conn.close()
        
        # マークダウンのコードブロック除去
        content = re.sub(r"^```html\n?", "", content, flags=re.MULTILINE)
        content = re.sub(r"^```\n?", "", content, flags=re.MULTILINE)
        
        # 5. WP投稿
        title_date = datetime.now().strftime("%Y年%m月第%W週")
        site_labels = {"FANZA": "FANZA", "DMM": "DMM.com", "DLsite": "DLsite"}
        post_title = f"【{site_labels[site]}】今週の人気{genre}ランキング TOP5！（{title_date}）"
        
        # メタディスクリプション用抜粋
        meta_desc = f"【{site_labels[site]}】今週の人気{genre}ランキング TOP5を{reviewer['name']}が熱く紹介！最新のトレンドをチェックして、あなたの「沼」になる一冊を見つけてね。"
        
        _post_ranking_article_to_wordpress(post_title, content, genre, site, top_image_url, excerpt=meta_desc)
        
        logger.info(f"  -> 処理完了: {site} / {genre}")
        time.sleep(5)  # レート制限対策

    logger.info("ランキング記事自動生成モードを終了しました")
    logger.info("=" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Novelove Auto Posting Tool")
    parser.add_argument("--ranking", action="store_true", help="Run the ranking generation workflow instead of normal posting")
    args = parser.parse_args()

    if args.ranking:
        process_ranking_articles()
    else:
        main()
