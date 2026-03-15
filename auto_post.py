#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
Novelove 自動投稿エンジン v9.0.0
【DigiKet 統合・安定運用版】
==========================================================
【変更点 v9.0.0】
 - 統合：DigiKet 取得ロジックを完全統合。外部ファイルの依存を排除
 - 修正：DigiKet RSS (RDF) の URL 抽出および名前空間パースバグを修正
 - 機能：詳細記事からのあらすじ全文取得・補完機能を実装
【変更点 v8.9.5】
 - 改善：AI審査基準を厳格化（4点以上のみ採用）
 - 改善：あらすじ文字数順（LENGTH DESC）の優先審査ロジックを導入
==========================================================
"""

import random
import requests
import json
import os
import urllib.parse
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
DIGIKET_AFFILIATE_ID  = os.environ.get("DIGIKET_AFFILIATE_ID", "novelove")

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = "deepseek-chat"  # V3.2 非思考モード（執筆・審査共通）

# === 取得対象ジャンル定義 ===
FETCH_TARGETS = [
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin", "genre": "doujin_bl", "label": "FANZA_BL",   "keyword": "ボーイズラブ"},
    {"site": "DLsite",  "service": None,     "floor": "girls",          "genre": "doujin_tl", "label": "DLsite_乙女","keyword": None},
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_bl",  "label": "DMM_BL",     "keyword": "ボーイズラブ"},
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin", "genre": "doujin_tl", "label": "FANZA_TL",   "keyword": "乙女向け"},
    {"site": "DLsite",  "service": None,     "floor": "bl",             "genre": "doujin_bl", "label": "DLsite_BL",  "keyword": None},
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_tl",  "label": "DMM_TL",     "keyword": "ティーンズラブ"},
    {"site": "DigiKet", "service": None,     "floor": None,             "genre": "comic_bl",  "label": "DigiKet_BL", "keyword": None},
    {"site": "DigiKet", "service": None,     "floor": None,             "genre": "comic_tl",  "label": "DigiKet_TL", "keyword": None},
    {"site": "DigiKet", "service": None,     "floor": None,             "genre": "doujin_bl", "label": "DigiKet同人_BL", "keyword": None},
    {"site": "DigiKet", "service": None,     "floor": None,             "genre": "doujin_tl", "label": "DigiKet同人_TL", "keyword": None},
]

GENRE_TAGS = {
    "BL":           ["BL", "BLコミック"],
    "TL":           ["TL", "TLコミック"],
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
DB_FILE_DIGIKET = os.path.join(SCRIPT_DIR, "novelove_digiket.db")
LOG_FILE       = os.path.join(SCRIPT_DIR, "novelove.log")

DESC_SCORE_PENDING  = 4
DESC_SCORE_WATCHING = 0

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

# ----------------------------------------------------------------------
# 共通デザイン定数
# ----------------------------------------------------------------------
AFFILIATE_BUTTON_STYLE = (
    "display:block;width:300px;margin:0 auto;padding:18px 0;"
    "background:#ffebf2;"
    "color:#d81b60 !important;text-decoration:none !important;"
    "font-weight:bold;font-size:1.1em;border-radius:50px;"
    "box-shadow:0 4px 10px rgba(216,27,96,0.15);border:2px solid #ffcfdf !important;"
    "text-align:center;line-height:1;outline:none !important;"
)

def get_affiliate_button_html(url, label="作品の詳細を見る"):
    """共通のアフィリエイトボタンHTMLを生成する"""
    return (
        f'<div class="novelove-button-container" style="margin:35px 0;text-align:center;">'
        f'<a href="{url}" target="_blank" rel="noopener" style="{AFFILIATE_BUTTON_STYLE}">'
        f'{label}</a></div>'
    )

# === キャラクター設定 ===
REVIEWERS = [
    {
        "id": "shion", "name": "紫苑",
        "genres": ["BL", "doujin_bl", "comic_bl"],
        "face_image": "紫苑",
        "tag_name": "【紫苑の個人的な注目属性】",
        "personality": "クールで毒舌な腐女子OL。「解釈一致」「これは神」が口癖。BL同人誌の即売会にも足繁く通う。「同人誌は公式より解釈が深い」が持論。",
        "tone": "冷静で分析的だが愛が滲み出る文体。同人作品の場合は作者への敬意とマニアックなこだわりを添える。",
        "greeting": "……また沼作品見つけてしまった。報告しなきゃ気が済まない。",
    },
    {
        "id": "marika", "name": "茉莉花",
        "genres": ["TL", "doujin_tl", "doujin_voice", "comic_tl"],
        "face_image": "茉莉花",
        "tag_name": "【茉莉花の今回のときめき成分】",
        "personality": "明るくポップなカフェ店員。「胸きゅんすぎる」が口癖。音声作品も大好きで、イヤホンしながら仕事中に感情爆発させるタイプ。",
        "tone": "テンション高め、感嘆符多め。ボイス作品の時は声・音質・演技への言及を自然に混ぜる。",
        "greeting": "ちょっと聞いて！！これやばすぎてひとりで抱えられない作品見つけた〜！！",
    },
    {
        "id": "aoi", "name": "葵",
        "genres": ["BL", "doujin_bl", "comic_bl"],
        "face_image": "葵",
        "tag_name": "【葵の今回の沼ポイント】",
        "personality": "BL好きの大学生。コミケや同人即売会に毎回参戦し、配置とサークル情報を全部把握している猛者。推しの話になると早口モードになる。",
        "tone": "オタク特有の早口テンション。同人作品では「作者さん」への熱い敬意と属性萌えの語りが炸裂する。",
        "greeting": "ねぇちょっと、この作品やばくない？推しが多すぎて情緒が終わる予感しかしない……",
    },
    {
        "id": "momoka", "name": "桃香",
        "genres": ["TL", "doujin_tl", "doujin_voice", "comic_tl"],
        "face_image": "桃香",
        "tag_name": "【桃香の今回の刺さりポイント】",
        "personality": "2児の主婦。子供が寝た後の深夜にイヤホンでこっそり音声作品を聴くのが至福の時間。「わかりみが深い」が口癖。",
        "tone": "大人の落ち着きと熱量の落差が魅力。ボイス作品では声の色気や演技力への言及を大人目線で語る。",
        "greeting": "子どもたち寝かしつけてから読んだんだけど、これ心臓に悪すぎる…大人の夜に読む作品ってこういうことよね。",
    },
    {
        "id": "ren", "name": "蓮",
        "genres": ["BL", "doujin_bl", "comic_bl"],
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
    site_str = str(site_raw)
    if "DLsite" in site_str:
        return DB_FILE_DLSITE
    if "DigiKet" in site_str:
        return DB_FILE_DIGIKET
    return DB_FILE_FANZA

def init_db():
    for db_path in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
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
            published_at TIMESTAMP,
            post_type TEXT DEFAULT 'regular'
        )''')
        for col, definition in [
            ("retry_count", "INTEGER DEFAULT 0"),
            ("last_error",  "TEXT DEFAULT ''"),
            ("desc_score",  "INTEGER DEFAULT 0"),
            ("rewrite_status", "TEXT DEFAULT NULL"),
            ("post_type", "TEXT DEFAULT 'regular'"),
            ("last_checked_at", "TEXT DEFAULT ''"),
            ("site", "TEXT DEFAULT ''")
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
        text = r.text
        # --- 作品形式メタデータチェック（MNGホワイトリスト方式）---
        # .work_genre 内のリンクから作品タイプを正確に判定する。
        # RelatedアイテムのURLに /work_type/SOU が含まれることがあるため、
        # 全文字列検索ではなく HTMLパース後の公式バッジリンクのみを判定対象にする。
        soup_pre = BeautifulSoup(text, 'html.parser')
        wg_links = [a.get("href", "") for a in soup_pre.select(".work_genre a")]
        has_mng = any("/work_type/MNG" in link for link in wg_links)
        if not has_mng:
            type_map = {"SOU": "ボイス", "NRE": "ノベル", "MNG": "マンガ",
                        "GME": "ゲーム", "MOV": "動画", "ANI": "アニメ", "ICG": "CG集"}
            detected = [name for code, name in type_map.items()
                        if any(f"/work_type/{code}" in link for link in wg_links)]
            detected_str = ", ".join(detected) if detected else "不明"
            logger.warning(f"[DLsite] マンガ以外の形式（{detected_str}）のため除外: {url}")
            return "__EXCLUDED_TYPE__"
        # --- DLsite 外国語版公式ラベルチェック ---
        # 「マンガ」形式でも「韓国語」「中国語(繁体字)」等の公式ラベルが付いていれば翻訳版なので除外する。
        # テキスト検索ではなく、.work_genre エリアの公式ラベルのみ対象にするため誤爆しない。
        lang_labels = [a.text.strip() for a in soup_pre.select(".work_genre a")]
        FOREIGN_LABELS = ["韓国語", "中国語", "繁體中文", "繁体中文", "简体中文", "English", "英語"]
        for lbl in lang_labels:
            if any(flabel in lbl for flabel in FOREIGN_LABELS):
                logger.warning(f"[DLsite] 外国語版ラベル（{lbl}）のため除外: {url}")
                return "__EXCLUDED_TYPE__"
        
        soup = BeautifulSoup(text, 'html.parser')
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
    # MNG(マンガ)のみを取得対象に限定（TOW:ノベル, SOU:ボイス は最初から除外）
    url = f"https://www.dlsite.com/{floor}/new/=/work_type/MNG"
    items = []
    # ボイス・ノイズ系および外国語版作品を除外するキーワードリスト（再強化）
    VOICE_KEYWORDS = ["ボイス", "音声", "ASMR", "CV.", "CV:", "cv.", "cv:", "シチュエーションCD", "バイノーラル", "ドラマCD",
                      "簡体中文版", "繁体中文版", "繁體中文版", "English", "韓国語版", "中国語"]
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        works = soup.select(".n_worklist_item")
        for work in works[:10]:
            title_tag = work.select_one(".work_name a")
            if not title_tag: continue

            # --- ボイス・ノイズ系作品フィルター ---
            title_text = title_tag.text.strip()
            category_tag = work.select_one(".work_category")
            category_text = category_tag.text.strip() if category_tag else ""
            
            # カテゴリバッジやタイトルからボイス・ノベルを排除
            if any(kw in (title_text + category_text) for kw in VOICE_KEYWORDS + ["ノベル", "小説", "実用"]):
                print(f"[DLsite] 作品種別フィルターによりスキップ: {title_text[:40]}")
                continue
            # --- フィルターここまで ---

            detail_url = title_tag.get("href")
            pid = detail_url.rstrip("/").split("/")[-1].replace(".html", "")
            if not pid: continue
            image_url = ""
            try:
                dr = requests.get(detail_url, headers=headers, timeout=10)
                dsoup = BeautifulSoup(dr.text, "html.parser")
                # --- [MNGホワイトリスト] 詳細ページで作品タイプを再確認 ---
                dr_wg_links = [a.get("href", "") for a in dsoup.select(".work_genre a")]
                if not any("/work_type/MNG" in link for link in dr_wg_links):
                    logger.info(f"  [DLsite取得] マンガ以外の形式のため取得スキップ: {title_text[:30]}")
                    continue
                # --------------------------------------------------------
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
    if "digiket" in str(product_url).lower():
        # 統合された内部関数を呼び出す
        return scrape_digiket_description(product_url)
    session = _make_fanza_session()
    try:
        r = session.get(
            product_url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://book.dmm.co.jp/"},
            timeout=20
        )
        r.encoding = r.apparent_encoding
        text = r.text
        # --- FANZA 作品形式ホワイトリスト・カテゴリーチェック ---
        soup = BeautifulSoup(text, "html.parser")
        
        # 1. 確実なホワイトリスト（作品形式が「コミック」または「劇画」かどうか）
        is_comic = False
        has_format_tag = False
        for dt in soup.find_all("dt"):
            if "作品形式" in dt.text or "形式" in dt.text or "ジャンル" in dt.text:
                dd = dt.find_next_sibling("dd")
                if dd:
                    has_format_tag = True
                    fmt_text = dd.text.strip()
                    if "コミック" in fmt_text or "劇画" in fmt_text or "マンガ" in fmt_text:
                        is_comic = True
                    break
                    
        # 作品形式タグが見つかったのにコミックじゃない場合（CG、動画、音声、ゲームなど）は即弾く
        if has_format_tag and not is_comic:
            logger.warning(f"[FANZA] マンガ以外の形式のため除外: {product_url}")
            return "__EXCLUDED_TYPE__"
        # --- FANZA 外国語版タイトルパターンチェック ---
        # FANZAには公式言語タグがないため、タイトルの「【】」「［］」内の表記のみを厳格に判定する。
        # あらすじや作品説明文は一切見ないため、「英語教師」等の一般単語で誤爆しない。
        title_str = str(product_url)  # URLにcidが含まれる場合の補助。実際はsoup.titleで取得する。
        page_title_tag = soup.find("title")
        page_title_str = page_title_tag.text if page_title_tag else ""
        FOREIGN_TITLE_PATTERNS = [
            "韓国語版", "한국어", "繁体中文", "繁體中文", "简体中文", "簡体中文",
            "中国語版", "English version", "English ver"
        ]
        import re as _re
        bracket_contents = _re.findall(r'[【\[\（\(]([^】\]\）\)]+)[】\]\）\)]', page_title_str)
        for bc in bracket_contents:
            if any(fp in bc for fp in FOREIGN_TITLE_PATTERNS):
                logger.warning(f"[FANZA] 外国語版タイトルパターン（{bc}）のため除外: {product_url}")
                return "__EXCLUDED_TYPE__"

        # 2. 禁止カテゴリーの保険的チェック (写真集, グラビア, 文芸・小説, ライトノベル 等)
        if any(kw in text for kw in ["カテゴリー</th><td>写真集", "カテゴリー</th><td>グラビア", "カテゴリー</th><td>文芸・小説", "カテゴリー</th><td>ライトノベル"]):
            logger.warning(f"[FANZA] 禁止カテゴリーを検知（除外対象）: {product_url}")
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

def _is_noise_content(title, desc=""):
    """
    タイトルやあらすじにNGワードが含まれているか判定する（外国語版特化フィルタ）
    ※ MANGAホワイトリストと併用し、マンガ形式の翻訳版のみを弾くためのリスト。
    ※ 特典ボイス等は許容するため音声関連ワードは含めない。
    """
    ng_words = [
        "簡体中文", "繁体中文", "繁體中文", "English", "韓国語版", "中国語",
        "简体中文", "翻訳台詞", "中文字幕", "korean", "한국어"
    ]
    target_text = f"{title}_{desc}".lower()
    for word in ng_words:
        if word.lower() in target_text:
            return True
    return False

def fetch_and_stock_all():
    for target in FETCH_TARGETS:
        site = target.get("site", "FANZA")
        if site == "DigiKet": continue
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
                "hits": 50,
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
        skip_count = 0 # Added for consistency with DigiKet
        for item in api_items:
            pid = item.get("content_id")
            if not pid: continue
            if c.execute("SELECT 1 FROM novelove_posts WHERE product_id=?", (pid,)).fetchone():
                skip_count += 1
                continue
            desc = scrape_description(item.get("URL", ""), site=site)
            # --- 作品形式による強制除外 ---
            if desc == "__EXCLUDED_TYPE__":
                desc = "" # 保存時は空にするか、適宜
                status = 'excluded'
            elif not desc:
                # あらすじが取得できなかった場合（かつ除外対象でない場合）は警告通知
                status = 'watching'
                title = item.get("title", "不明なタイトル")
                product_url = item.get("URL", "")
                notify_discord(
                    f"⚠️ **あらすじ取得失敗（サイト構造変化の可能性あり）**\n"
                    f"**サイト**: {site}\n"
                    f"**作品**: {title}\n"
                    f"**URL**: {product_url}",
                    username="ノベラブ異常検知"
                )
            else:
                status = 'excluded' if _is_noise_content(item.get("title", ""), desc) else 'watching'

            time.sleep(1.0)
            image_url = item.get("imageURL", {}).get("large", "")
            if site == "DLsite":
                # DLsite: dlaf.jp 形式
                # 構造: https://dlaf.jp/{floor}/dlaf/=/t/n/link/work/aid/{aid}/id/{id}.html
                floor = target.get("floor", "girls")
                aid = os.environ.get('DLSITE_AFFILIATE_ID', 'novelove')
                aff_url = f"https://dlaf.jp/{floor}/dlaf/=/t/n/link/work/aid/{aid}/id/{pid}.html"
            else:
                # DMM/FANZA: al.dmm.com / al.fanza.co.jp 形式
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

            # --- APIコスト最適化判定 ---
            final_status = status
            final_score = 0
            if status == 'watching' and desc and len(desc) > 50:
                # 1. 画像があるか？
                # 2. 発売日が近い（7日以内）か？
                is_img_ready = _check_image_ok(image_url)
                is_date_ready = False
                try:
                    rd_dt = datetime.strptime(rdate[:10], "%Y-%m-%d")
                    if rd_dt <= datetime.now() + timedelta(days=7):
                        is_date_ready = True
                except: is_date_ready = True # 日付不明なら進める

                if is_img_ready and is_date_ready:
                    # 本格審査へ
                    review_status, score = _check_desc_ok(item.get("title", ""), desc, rdate)
                    final_score = score
                    if review_status == "pending":
                        final_status = "pending"
                    elif score >= 1 and score <= 3:
                        final_status = "excluded"
                    elif review_status == "limit_skip" or review_status == "api_error":
                        final_status = "watching"
                else:
                    # 条件不十分（画像なし or 発売日が遠い）
                    # AIを呼ばずに watching でキープ（費用の節約）
                    final_status = "watching"

            c.execute(
                """INSERT INTO novelove_posts
                    (product_id, title, author, genre, site, status, description,
                    affiliate_url, image_url, product_url, release_date, post_type, desc_score)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, item.get("title"), author, target["genre"],
                    f"{site}:r18={is_r18}", final_status, desc, aff_url,
                    image_url, item.get("URL", ""), rdate, "regular", final_score)
            )
            logger.info(f"[{site}] [確保({final_status}/{final_score}点)] {item.get('title','')[:40]} ({target['label']})")
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
5点: 超新着・最高品質。読者が確実に惹きつけられる独自性や魅力があり、文句なしの最高傑作。
4点: 良作・採用。ストーリーやキャラクターの魅力が具体的に書かれ、読者が内容を明確にイメージできる。
3点: 情報はあるが設定がありきたり。またはストーリーの面白さが伝わりにくい（不採用）。
2点: 情報が少なすぎる。
1点: あらすじがほぼない・意味不明。

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
        return "api_error", 0

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
    time.sleep(5)  # 安定性重視で5秒待機（連射によるBAN防止）

    if score >= DESC_SCORE_PENDING:
        return "pending", score
    elif score == DESC_SCORE_WATCHING:
        return "watching", score
    elif score >= 1:
        return "failed_stock", score
    else:
        return "watching", score


# _check_stock_status() および promote_watching() は v8.4.0 で廃止され、main() に統合されました。

def get_internal_link(product_id, author, genre, db_path):
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

    chat_open  = f'<div class="speech-bubble-left"><img src="/wp-content/uploads/icons/{reviewer["face_image"]}.png" alt="{reviewer["name"]}" /><div class="speech-text">'
    chat_close = '</div></div>'

    voice_hint = ""
    if target["genre"] == "doujin_voice":
        voice_hint = "\n【ボイス作品紹介のコツ】声優の演技・音質・耳への心地よさに言及すること。「耳が溶ける」「ヘッドホン必須」「通勤中に聴けない」などのリアクションを使ってもOK。"

    return f"""あなたは人気ファンブログ「Novelove」のライター「{reviewer["name"]}」です。

【事前審査（最初に必ず実行すること）】
以下の基準で対象作品を0〜5点でスコアリングしてください。
- 5点：超新着・最高品質。読者が確実に惹きつけられる独自性や魅力があり、文句なしの最高傑作。
- 4点：良作・採用。ストーリーやキャラの魅力が具体的に書かれ、内容が明確にイメージできる。
- 3点：標準的だが、独自性や熱量が不足している（不採用）。
- 2〜1点：情報が少なすぎる、またはジャンルや内容がズレている。
- 0点：外国語版（韓国・中国・英語等）、ボイスのみ、動画のみ、マンガではない。

スコアが0点の場合は、**数字「0」とだけ回答してください**（記事は一切書かないこと）。
スコアが1〜3点の場合は、**スコアの数字のみ**回答してください。
スコアが4〜5点の場合のみ、以下の形式で記事を執筆してください。

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
6. **【重要】スコアが4〜5点の場合、スコアの数字は出力せず、記事本文（HTML）のみを出力してください。**
7. コメントのボリューム:
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

        stripped = text.strip()
        # --- AI審査スコアチェック（0〜3点なら不採用）---
        if stripped in ("0", "1", "2", "3"):
            score = int(stripped)
            score_reason = {0: "審査対象外（外国語/非マンガ）", 1: "適合度低（スコア1）", 2: "適合度低（スコア2）", 3: "熱量不足（スコア3）"}[score]
            logger.warning(f"  [DeepSeek] AIスコア{score}点 → {score_reason}。投稿スキップ。")
            return "", f"ai_score_{score}", DEEPSEEK_MODEL, proc_time
        # --- 先頭スコア数字の除去（4〜5点合格でも数字が残る場合がある）---
        # AIが「5\n\n<div...」のように数字を先頭に出力してしまう場合の対策
        cleaned = re.sub(r'^[4-5]\s*\n+', '', stripped)
        if cleaned != stripped:
            logger.info(f"  [クリーニング] 先頭のスコア数字を除去しました。")
            stripped = cleaned.strip()
        if len(stripped) > 50:
            logger.info(f"  [DeepSeek] 執筆完了（{len(stripped)}文字 / {proc_time}秒）")
            return stripped, "ok", DEEPSEEK_MODEL, proc_time

        logger.warning(f"  [DeepSeek] 試行{attempt+1}: 応答が短すぎる（{len(stripped)}文字）")
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
    internal_link = get_internal_link(
        target["product_id"],
        target.get("author", ""),
        target["genre"],
        db_path=get_db_path(target.get("site"))
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

            img_html = f'<p style="text-align:center;margin:20px 0;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow"><img src="{target["image_url"]}" alt="{target["title"]}" style="max-width:500px;width:100%;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,0.18);" /></a></p>\n'
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
            # 上部のリンクはテキストのまま（中央寄せ）
            text_link = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:5px; margin-bottom:15px;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow" style="text-decoration:none; color:#d81b60;">▶ 『{target["title"]}』の詳細をチェック！</a></p>\n'
            
            # 画像下テキストリンク
            text_link = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:5px; margin-bottom:15px;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow" style="text-decoration:none; color:#d81b60;">▶ 『{target["title"]}』の詳細をチェック！</a></p>\n'
            
            # 末尾ボタンHTML生成
            button_html = get_affiliate_button_html(target["affiliate_url"], "作品の詳細を見る")
            
            # クレジット表示
            if "FANZA" in site_display:
                credit_html = (
                    f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;">\n'
                    f'<a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/r18_135_17.gif" width="135" height="17" alt="WEB SERVICE BY FANZA" style="border:none;"></a>\n'
                    f'</div>\n'
                )
            elif "DMM" in site_display:
                credit_html = (
                    f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;">\n'
                    f'<a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/com_135_17.gif" width="135" height="17" alt="WEB SERVICE BY DMM.com" style="border:none;"></a>\n'
                    f'</div>\n'
                )
            else:
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

            # 関連記事HTML
            internal_link_html = ""
            if internal_link:
                internal_link_html = (
                    f'<div style="border:1px solid #f0c0c0; border-radius:8px; padding:15px; margin:20px 0; background:#fff8f8;">\n'
                    f'<p style="margin:0 0 8px; font-weight:bold; color:#c0607f;">📚 あわせて読みたい</p>\n'
                    f'<p><a href="{internal_link["url"]}">{internal_link["title"]}</a></p>\n'
                    f'</div>\n'
                )

            # 【重要】画像下はテキリン、末尾はボタン→関連記事の順で固定 (v8.5.0以降)
            full_content = (
                badge_html + img_html + release_display + text_link + 
                content + button_html + internal_link_html + credit_html
            )
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

    # カテゴリ・タグ設定
    categories = [cat_id] if cat_id else []
    # ランキング記事の場合はランキングカテゴリ(30)を追加
    if "ranking" in str(slug).lower() or "ランキング" in title:
        ranking_cat_id = 30
        if ranking_cat_id not in categories:
            categories.append(ranking_cat_id)

    post_data = {
        "title": title,
        "content": content,
        "excerpt": excerpt,
        "status": "publish",
        "slug": slug,
        "categories": categories,
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
    logger.info("Novelove エンジン v9.0.0 【DigiKet 統合・安定運用版】 起動")
    init_db()
    
    # ロックファイルチェック (排他制御)
    lock_file = os.path.join(SCRIPT_DIR, "ranking.lock")
    if os.path.exists(lock_file):
        logger.info("🚫 ランキング記事生成中のため、通常投稿を一時停止します。")
        return

    fetch_and_stock_all()
    # DigiKet 新着取得 (内部関数を呼び出す)
    try:
        fetch_digiket_items()
    except Exception as e:
        logger.error(f"DigiKet取得エラー: {e}")

    # クールダウンチェック（サイト全体で1時間1件ペース）
    is_cool_down = False
    for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
        if not os.path.exists(db_p): continue
        tmp_conn = sqlite3.connect(db_p)
        last_pub = tmp_conn.execute("SELECT published_at FROM novelove_posts WHERE status='published' ORDER BY published_at DESC LIMIT 1").fetchone()
        tmp_conn.close()
        if last_pub and last_pub[0]:
            try:
                from datetime import timezone
                lp_dt = datetime.strptime(last_pub[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                diff = (datetime.now(timezone.utc) - lp_dt).total_seconds() / 60
                if diff < 55: is_cool_down = True
            except: pass
    if is_cool_down:
        logger.info("🕒 クールダウン中（1時間未経過）。終了します。")
        return

    # 全ジャンルを順番にチェックし、1件投稿できたら終了するロジック
    g_idx_base = get_genre_index()
    posted = False

    for i in range(len(FETCH_TARGETS)):
        target_info = FETCH_TARGETS[(g_idx_base + i) % len(FETCH_TARGETS)]
        db_path = get_db_path(target_info.get("site", "FANZA"))
        genre = target_info["genre"]
        
        logger.info(f"--- 投稿チェック開始: {target_info['label']} ({genre}) ---")
        
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # --- ステップ1: 即投稿枠 (pending) ---
        pending_row = c.execute(
            "SELECT * FROM novelove_posts WHERE status='pending' AND genre=? ORDER BY LENGTH(description) DESC, inserted_at DESC LIMIT 1",
            (genre,)
        ).fetchone()

        if pending_row:
            logger.info(f"✨ [新着即投稿] {pending_row['title'][:40]} (Score: {pending_row['desc_score']}点)")
            if _execute_posting_flow(pending_row, c, conn, post_label="新着投稿"):
                posted = True

        # --- ステップ2: 保険枠 (行列消化) ---
        if not posted:
            queue_row = c.execute(
                """SELECT * FROM novelove_posts 
                   WHERE status='watching' 
                     AND genre=? 
                     AND release_date <= date('now', 'localtime')
                   ORDER BY release_date ASC, inserted_at ASC LIMIT 1""",
                (genre,)
            ).fetchone()
            
            if queue_row:
                pid = queue_row["product_id"]
                title = queue_row["title"]
                logger.info(f"🔄 [保険枠・行列消化] {title[:40]} を再審査...")
                
                new_desc = scrape_description(queue_row["product_url"], site=queue_row["site"].split(":")[0])
                if new_desc == "__EXCLUDED_TYPE__":
                    logger.info(f"  -> 再スキャンで除外対象のため除外: {title[:30]}")
                    c.execute("UPDATE novelove_posts SET status='excluded' WHERE product_id=?", (pid,))
                    conn.commit()
                elif not new_desc:
                    logger.warning(f"  -> あらすじの再取得に失敗しました: {title[:30]}")
                    # 個別記事フェッチ中ならここで警告
                    notify_discord(
                        f"⚠️ **【再審査】あらすじ取得失敗（サイト構造変化の可能性あり）**\n"
                        f"**作品**: {title}\n"
                        f"**URL**: {queue_row['product_url']}",
                        username="ノベラブ異常検知"
                    )
                else:
                    if not _check_image_ok(queue_row["image_url"]):
                        logger.info(f"  -> 画像が依然として無いため、本採用を見送り除外: {title[:30]}")
                        c.execute("UPDATE novelove_posts SET status='excluded' WHERE product_id=?", (pid,))
                        conn.commit()
                    else:
                        status, score = _check_desc_ok(title, new_desc or queue_row["description"], queue_row["release_date"])
                        if status == "pending":
                            logger.info(f"  -> 審査合格（{score}点）！保険枠から昇格投稿します。")
                            if _execute_posting_flow(queue_row, c, conn, post_label="保険枠・再審査投稿", override_score=score):
                                posted = True
                        else:
                            logger.info(f"  -> 審査落選（{score}点）。行列から除外します。")
                            c.execute("UPDATE novelove_posts SET status='excluded', desc_score=? WHERE product_id=?", (score, pid))
                            conn.commit()

        conn.close()
        
        if posted:
            # 成功したら次のジャンルインデックスを保存して終了
            save_genre_index(g_idx_base + i + 1)
            logger.info(f"✅ {target_info['label']} にて投稿成功。本日の処理を終了します。")
            break
        else:
            logger.info(f"  -> {target_info['label']} に投稿可能アイテムなし。次へ...")

    if not posted:
        logger.info("❌ 全ジャンル確認しましたが、本日投稿できる作品はありませんでした。")
    
    logger.info("=" * 60)

def _execute_posting_flow(row, cursor, conn, post_label="新着投稿", override_score=None):
    """共通の執筆・投稿・通知フロー。成功すればTrueを返す。"""
    pid = row["product_id"]
    score = override_score if override_score is not None else row["desc_score"]
    
    target = {
        "product_id":    pid,
        "title":         row["title"],
        "author":        row["author"] or "",
        "genre":         row["genre"],
        "site":          row["site"],
        "description":   row["description"],
        "affiliate_url": row["affiliate_url"],
        "image_url":     row["image_url"],
        "release_date":  row["release_date"],
        "is_r18":        ":r18=1" in str(row["site"])
    }
    
    res_data = generate_article(target)
    if not res_data: return False
    
    wp_title, content, excerpt, seo_title, is_r18_val, error_type, model_name, filter_level, proc_time, word_count = res_data
    site_name = str(target.get('site') or 'Unknown').split(':')[0]
    
    url = post_to_wordpress(
        wp_title, content, target["genre"], target["image_url"],
        excerpt, seo_title, slug=pid, is_r18=is_r18_val, site_label=site_name
    )
    
    if url:
        cursor.execute(
            "UPDATE novelove_posts SET status='published', wp_post_url=?, published_at=datetime('now'), desc_score=? WHERE product_id=?",
            (url, score, pid)
        )
        conn.commit()
        
        # 本日の投稿数集計 (FANZA + DLsite + DigiKet の合計)
        total_daily = 0
        for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
            if not os.path.exists(db_p): continue
            _conn = sqlite3.connect(db_p)
            # 日本時間 (JST) で今日の日付の投稿をカウント
            count = _conn.execute(
                "SELECT COUNT(*) FROM novelove_posts WHERE status='published' AND date(published_at, '+9 hours') = date('now', '+9 hours')"
            ).fetchone()[0]
            total_daily += count
            _conn.close()
        
        emoji = "✅" if "新着" in post_label else "🔄"
        site_disp = str(row['site'] or 'Unknown').split(':')[0]
        genre_disp = _genre_label(row['genre'])
        notify_discord(
            f"{emoji} **[{site_disp}] [{genre_disp}] {post_label}成功！**\n"
            f"**タイトル**: {wp_title}\n"
            f"**AIスコア**: `{score}点` / モデル: `{model_name}`\n"
            f"**統計**: 今日 {total_daily}件目 / {word_count}文字\n"
            f"**URL**: {url}"
        )
        logger.info(f"✅ {post_label}成功！ Score: {score}, URL: {url}")
        return True
    return False

def fetch_ranking_dmm_fanza(site, genre):
    """
    FANZA / DMM.com のランキング (sort=rank) から上位を取得し、
    漫画以外のノイズ（小説・ボイス等）を排除した上で5件を返す。
    genre: "BL" または "TL"
    site: "FANZA" または "DMM"
    """
    params = {
        "api_id": DMM_API_ID,
        "affiliate_id": DMM_AFFILIATE_API_ID,
        "hits": 10, # ランキングAPIはあまり大きいhitsを指定するとエラーになる場合があるため10件
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
                title = item.get("title", "")
                
                # 取得する前に簡易チェック（タイトルで弾けるなら通信を節約）
                if _is_noise_content(title, ""):
                    continue
                    
                image_url = item.get("imageURL", {}).get("large", "")
                # DMM/FANZAランキング用のURL生成
                base_url = item.get("URL", "")
                encoded_url = urllib.parse.quote(base_url, safe="")
                af_id = DMM_AFFILIATE_LINK_ID or "novelove-001"
                ch_params = "&ch=toolbar&ch_id=text"
                if site == "FANZA":
                    aff_url = f"https://al.fanza.co.jp/?lurl={encoded_url}&af_id={af_id}{ch_params}"
                else:
                    aff_url = f"https://al.dmm.com/?lurl={encoded_url}&af_id={af_id}{ch_params}"
                desc = scrape_description(item.get("URL", ""), site=site)
                
                # あらすじ取得後に再度厳密チェック
                if _is_noise_content(title, desc):
                    continue
                    
                items.append({
                    "title": title,
                    "url": aff_url,
                    "image_url": image_url,
                    "description": desc
                })
                
                if len(items) >= 5:
                    break # 5件揃ったら終了
        else:
            logger.error(f"DMM API Error ({site}/{genre}): {r.status_code}")
    except Exception as e:
        logger.error(f"DMM API Fetch Error ({site}/{genre}): {e}")
    return items

def fetch_ranking_dlsite(genre):
    """
    DLsiteのランキング（スクレイピング）を取得し、
    MNG（マンガ）タグを持つ作品のみを上位からピックアップして5件を返す。
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
            # フィルタリングで弾かれるのを想定して多めに上位20件までチェック
            for anchor in anchor_items[:20]:
                title = anchor.text.strip()
                link = anchor.get('href')
                
                # 事前タイトルチェック
                if _is_noise_content(title, ""):
                    continue
                
                # 詳細ページから画像とあらすじを取得
                img_src = ""
                desc = ""
                is_manga = False
                try:
                    dr = requests.get(link, headers=headers, timeout=10)
                    if dr.status_code == 200:
                        dsoup = BeautifulSoup(dr.text, 'html.parser')
                        
                        # MNGホワイトリストチェック
                        work_genres = dsoup.select('.work_genre a')
                        for wg in work_genres:
                            href = wg.get('href', '')
                            if '/work_type/MNG' in href:
                                is_manga = True
                                break
                        
                        if not is_manga:
                            continue # マンガでなければスキップ
                            
                        og_img = dsoup.select_one('meta[property="og:image"]')
                        if og_img:
                            img_src = og_img.get('content', '')
                        
                        # あらすじ
                        desc_tag = dsoup.select_one('meta[property="og:description"]')
                        if desc_tag:
                            desc = desc_tag.get('content', '')
                            
                        # 内容チェック
                        if _is_noise_content(title, desc):
                            continue
                            
                except:
                    continue # 取得失敗したらスキップ
                    
                aff_id = os.environ.get('DLSITE_AFFILIATE_ID', 'novelove')
                aff_url = f"{link}?affiliate_id={aff_id}" if "affiliate_id=" not in link else link
                
                items.append({
                    "title": title,
                    "url": aff_url,
                    "image_url": img_src,
                    "description": desc
                })
                
                if len(items) >= 5:
                    break # 5件揃ったら終了
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

    chat_open  = f'<div class="speech-bubble-left"><img src="/wp-content/uploads/icons/{reviewer["face_image"]}.png" alt="{reviewer["name"]}" /><div class="speech-text">'
    chat_close = '</div></div>'

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
  
  {chat_open}<strong>{reviewer["name"]}の推しポイント：</strong><br>（ここを{reviewer["name"]}のセリフ口調で30〜50字で記述）{chat_close}
  
  [REVIEW_LINK_{{rank}}]
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

def _post_ranking_article_to_wordpress(title, content, genre, site_name, top_image_url="", excerpt=""):
    """
    生成されたランキング記事をWordPressに投稿する
    top_image_url: 1位作品の画像URL（アイキャッチに設定）
    """
    # スラッグ生成: {site}-{genre}-ranking-{year}-{month}-w{week}
    now = datetime.now()
    year = now.strftime("%Y")
    month = now.strftime("%m")
    week = str((now.day - 1) // 7 + 1)  # 月内第何週（%Wは年の通算週番号なので誤り）
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
        # DBに記録を残す (将来の一括修正等のため)
        try:
            db_path = get_db_path(site_name)
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO novelove_posts (product_id, title, genre, site, status, post_type, wp_post_url, published_at)
                VALUES (?, ?, ?, ?, 'published', 'ranking', ?, datetime('now'))
            """, (slug, title, genre, site_name, wp_url))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"  ランキングDB記録エラー: {e}")
        return True
    return False

def get_ranking_slug(site, genre):
    from datetime import datetime
    now = datetime.now()
    year = now.strftime("%Y")
    month = now.strftime("%m")
    week = str((now.day - 1) // 7 + 1)
    return f"{site.lower()}-{genre.lower()}-ranking-{year}-{month}-w{week}"

def process_ranking_articles():
    """
    ランキング記事を一括で生成・投稿する処理メイン
    FANZA(BL, TL), DMM(BL, TL), DLsite(BL, TL) の計6記事
    サイト間15分ずらし ＋ サイト内BL/TL同時投稿版
    """
    logger.info("=" * 60)
    logger.info("ランキング記事自動生成モードを開始します")
    
    lock_file = os.path.join(SCRIPT_DIR, "ranking.lock")
    try:
        with open(lock_file, "w") as f:
            f.write(datetime.now().isoformat())
    except Exception as e:
        logger.error(f"ロックファイルの作成に失敗しました: {e}")

    try:
        sites = ["FANZA", "DLsite", "DMM"]
        medals = {1: "🥇 1位", 2: "🥈 2位", 3: "🥉 3位", 4: "4位", 5: "5位"}
        site_labels = {"FANZA": "FANZA", "DMM": "DMM.com", "DLsite": "DLsite"}
        
        for i, site in enumerate(sites):
            logger.info(f"--- ランキング処理: {site} (BL/TL 同期生成) ---")
            
            gen_data = {} # genre -> data
            
            for genre in ["BL", "TL"]:
                logger.info(f"  [{genre}] データの取得とAI生成...")
                items = []
                if site in ("FANZA", "DMM"):
                    items = fetch_ranking_dmm_fanza(site, genre)
                else:
                    items = fetch_ranking_dlsite(genre)
                    
                if len(items) < 5:
                    logger.warning(f"  -> ランキングデータが5件未満のためスキップ (取得数: {len(items)})")
                    continue
                    
                top_image_url = items[0].get("image_url", "")
                reviewer = _get_reviewer_for_genre(genre)
                
                prompt = format_ranking_prompt(site, genre, items, reviewer)
                messages = [
                    {"role": "system", "content": "あなたは優秀なアフィリエイトブロガーです。"},
                    {"role": "user", "content": prompt}
                ]
                
                generated_html, err = _call_deepseek_raw(messages, max_tokens=2500, temperature=0.7)
                if err != "ok":
                    logger.error(f"  -> AI生成失敗: {err}")
                    continue
                    
                content_html = generated_html
                
                # 内部リンク用のDB接続 (対象商品が既存レビューにあるか)
                db_path = get_db_path(site)
                conn = sqlite3.connect(db_path)
                c = conn.cursor()
                
                for idx, item in enumerate(items):
                    rank = idx + 1
                    badge = medals.get(rank, f"{rank}位")
                    content_html = content_html.replace(f"[RANK_BADGE_{rank}]", badge)
                    content_html = content_html.replace(f"[TITLE_{rank}]", item["title"])
                    
                    img_elem = f'<div style="text-align: center;"><a href="{item["url"]}" target="_blank" rel="noopener"><img src="{item["image_url"]}" alt="{item["title"]}" style="max-height: 400px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);" /></a></div>'
                    text_link_elem = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:10px; margin-bottom:15px;"><a href="{item["url"]}" target="_blank" rel="nofollow" style="text-decoration:none; color:#d81b60;">▶ 『{item["title"]}』の詳細をチェック！</a></p>'
                    content_html = content_html.replace(f"[IMAGE_{rank}]", f"{img_elem}{text_link_elem}")
                    
                    pid = ""
                    if "content_id" in item:
                        pid = item["content_id"]
                    else:
                        m = re.search(r"product_id/([^/?]+)", item["url"])
                        if m: pid = m.group(1)
                    
                    internal_link_html = ""
                    if pid:
                        row = c.execute("SELECT wp_post_url FROM novelove_posts WHERE product_id=? AND status='published'", (pid,)).fetchone()
                        if row and row[0]:
                            internal_link_html = f'<p style="text-align:center; font-size:0.9em; margin-top:-10px; margin-bottom:20px;"><a href="{row[0]}" style="color:#d81b60; text-decoration:none;">📝 詳しいレビューはこちら</a></p>'

                    content_html = content_html.replace(f"[REVIEW_LINK_{rank}]", internal_link_html)
                    
                conn.close()
                content_html = re.sub(r"^```html\n?", "", content_html, flags=re.MULTILINE)
                content_html = re.sub(r"^```\n?", "", content_html, flags=re.MULTILINE)
                
                _now = datetime.now()
                _week_of_month = (_now.day - 1) // 7 + 1
                title_date = f"{_now.year}年{_now.month}月第{_week_of_month}週"
                post_title = f"【{site_labels[site]}】今週の人気{genre}ランキング TOP5！（{title_date}）"
                meta_desc = f"【{site_labels[site]}】今週の人気{genre}ランキング TOP5を{reviewer['name']}が熱く紹介！最新のトレンドをチェックして、あなたの「沼」になる一冊を見つけてね。"
                
                gen_data[genre] = {
                    "content": content_html,
                    "top_image_url": top_image_url,
                    "title": post_title,
                    "excerpt": meta_desc
                }
                import time
                time.sleep(5) # APIレート制限対策

            # === 同期投稿 ===
            for genre, data in gen_data.items():
                final_content = data["content"]
                disp_site = site_labels.get(site, site)
                
                # 他ジャンルへの相互リンク挿入
                other_genre = "TL" if genre == "BL" else "BL"
                if other_genre in gen_data:
                    other_slug = get_ranking_slug(site, other_genre)
                    other_url = f"{WP_SITE_URL}/{other_slug}/"
                    _now2 = datetime.now()
                    _wk2 = (_now2.day - 1) // 7 + 1
                    other_title_date = f"{_now2.year}年{_now2.month}月第{_wk2}週"
                    cross_link = (
                        f'<div style="border:1px solid #f0c0c0; border-radius:8px; padding:15px; margin:20px 0; background:#fff8f8;">\n'
                        f'<p style="margin:0 0 8px; font-weight:bold; color:#c0607f;">📚 あわせて読みたい</p>\n'
                        f'<p><a href="{other_url}">【{disp_site}】{other_genre}ランキング（{other_title_date}）はこちら</a></p>\n'
                        f'</div>\n'
                    )
                    final_content += cross_link
                
                # クレジット
                if "FANZA" in disp_site:
                    ranking_credit = (
                        f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;">\n'
                        f'<a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/r18_135_17.gif" width="135" height="17" alt="WEB SERVICE BY FANZA" style="border:none;"></a>\n'
                        f'</div>\n'
                    )
                elif "DMM" in disp_site:
                    ranking_credit = (
                        f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;">\n'
                        f'<a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/com_135_17.gif" width="135" height="17" alt="WEB SERVICE BY DMM.com" style="border:none;"></a>\n'
                        f'</div>\n'
                    )
                else:
                    ranking_credit = f'<p style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee; font-size:0.8em; color:#bbb;">\nPRESENTED BY {disp_site} / Novelove Affiliate Program\n</p>\n'
                
                final_content += ranking_credit
                
                logger.info(f"  -> {genre} の同期投稿を実行中...")
                _post_ranking_article_to_wordpress(data["title"], final_content, genre, site, data["top_image_url"], excerpt=data["excerpt"])
                import time
                time.sleep(2)
            
            # 次のサイト処理まで15分待機 (最後のサイト以外)
            if i < len(sites) - 1:
                logger.info("通知負荷を軽減するため、次のサイト処理まで15分間（900秒）待機します...")
                import time
                time.sleep(900)

    finally:
        # ロック解除
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception as e:
                logger.error(f"ロックファイルの削除に失敗しました: {e}")
                
    logger.info("ランキング記事自動生成モードを終了しました")
    logger.info("=" * 60)

# ----------------------------------------------------------------------
# DigiKet 取得モジュール (統合版)
# ----------------------------------------------------------------------

def scrape_digiket_description(product_url):
    """
    DigiKet の商品詳細ページから「作品内容」の全文を抽出する
    """
    try:
        r = requests.get(product_url, headers=HEADERS, timeout=20)
        if r.status_code != 200: return ""

        # DigiKet サイトは基本 EUC-JP
        r.encoding = "euc-jp"
        soup = BeautifulSoup(r.text, "html.parser")
        desc_area = None

        # '作品説明' または '作品内容' というテキストを直接含む要素を探す
        for text_label in ["作品説明", "作品内容", "作品詳細"]:
            label_tag = soup.find(["h4", "h3", "th", "div", "span"], string=re.compile(text_label))
            if label_tag:
                desc_area = label_tag.find_next_sibling(["div", "p", "td"])
                if desc_area: break
                parent = label_tag.parent
                if parent:
                    desc_area = parent.find_next(["div", "p", "td"], class_=re.compile(r"description|explanation|body"))
                    if desc_area: break

        if not desc_area:
            selectors = [".work_explanation_body", ".works-description", "#work_explanation", ".main_explanation", ".description_area"]
            for sel in selectors:
                desc_area = soup.select_one(sel)
                if desc_area: break

        if desc_area:
            for trash in desc_area.select('.readmore, script, style, .work_review_btn'):
                trash.decompose()
            text = desc_area.get_text(separator="\n", strip=True)
            text = re.sub(r"^(作品説明|作品内容|作品詳細)\n?", "", text)
            return text.strip()
        return ""
    except Exception as e:
        logger.error(f"DigiKetスクレイピングエラー: {e}")
        return ""

def fetch_digiket_items():
    """
    DigiKet XML API から新着を取得し、DBにストックする
    """
    logger.info("DigiKet 新着取得開始")
    
    # 既存の DIGIKET_TARGETS 定義があれば使用、なければここで定義
    # fetch_and_stock_all() のグローバル定数 FETCH_TARGETS に DigiKet が含まれているが
    # 独自の RSS 取得が必要なため、個別にリストを定義する
    targets = [
        {"target": "8", "genre": "comic_bl",  "label": "DigiKet_商業BL"},
        {"target": "8", "genre": "comic_tl",  "label": "DigiKet_商業TL"},
        {"target": "2", "genre": "doujin_bl", "label": "DigiKet_同人BL"},
        {"target": "2", "genre": "doujin_tl", "label": "DigiKet_同人TL"},
    ]

    conn = sqlite3.connect(DB_FILE_DIGIKET)
    c = conn.cursor()

    for target_cfg in targets:
        target_id = target_cfg["target"]
        genre = target_cfg["genre"]
        label = target_cfg["label"]

        # DigiKet の RSS API はシンプルなターゲット指定のみで取得
        api_url = f"https://api.digiket.com/xml/api/getxml.php?target={target_id}&sort=new"

        try:
            logger.info(f"  - 取得先: {label} ({api_url})")
            r = requests.get(api_url, timeout=20)
            # DigiKet API は EUC-JP なので明示的にデコード
            r.encoding = 'euc-jp'
            xml_text = r.text
            
            # 修正ポイント: XMLとしてパースし、名前空間付きタグを正規表現で探す
            soup = BeautifulSoup(xml_text, "html.parser")
            items = soup.find_all("item")
            logger.info(f"  - 取得数: {len(items)}件")

            new_count = 0
            skip_count = 0
            for item in items:
                title = item.find("title").text if item.find("title") else ""
                
                # 修正ポイント: RSS 1.0 (RDF) では link タグが空で rdf:about に URL がある場合がある
                product_url = ""
                link_tag = item.find("link")
                if link_tag and link_tag.text.strip():
                    product_url = link_tag.text.strip()
                else:
                    # 属性から取得 (BeautifulSoupは名前空間付き属性をそのままか、接頭辞なしで保持することがある)
                    product_url = item.get("rdf:about") or item.get("about") or ""

                if not product_url:
                    logger.debug(f"    - スキップ: {title[:30]} (URLなし)")
                    skip_count += 1
                    continue

                # ID抽出 (ID=ITMXXXXXXX または ITMXXXXXXX 形式)
                m = re.search(r"ID=(ITM\d+)", product_url) or re.search(r"ITM\d+", product_url)
                if not m:
                    logger.debug(f"    - スキップ: {title[:30]} (ID抽出失敗)")
                    skip_count += 1
                    continue
                pid = m.group(1) if m.groups() else m.group(0)

                if c.execute("SELECT 1 FROM novelove_posts WHERE product_id=?", (pid,)).fetchone():
                    logger.debug(f"    - スキップ: {title[:30]} (DBに存在済み)")
                    skip_count += 1
                    continue

                # 名前空間を含むタグの取得 (dc:creator, dc:date, content:encoded)
                # re.I (ignore case) を指定して確実にヒットさせる
                creator_tag = item.find(re.compile(r"creator", re.I))
                author = creator_tag.text if creator_tag else ""
                
                date_tag = item.find(re.compile(r"date", re.I))
                date_str = date_tag.text if date_tag else datetime.now().strftime("%Y-%m-%d")

                content_tag = item.find(re.compile(r"encoded", re.I))
                content_encoded = content_tag.text if content_tag else ""
                img_match = re.search(r'src="(https://.*?\.jpg)"', content_encoded)
                image_url = img_match.group(1) if img_match else ""

                description_tag = item.find("description")
                description = description_tag.text if description_tag else ""

                affiliate_url = product_url
                if DIGIKET_AFFILIATE_ID:
                    if not affiliate_url.endswith("/"): affiliate_url += "/"
                    affiliate_url += f"AFID={DIGIKET_AFFILIATE_ID}/"

                c.execute("""INSERT INTO novelove_posts
                    (product_id, title, author, genre, site, status, release_date, description, affiliate_url, image_url, product_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (pid, title, author, genre, "DigiKet", "watching", date_str, description, affiliate_url, image_url, product_url))

                new_count += 1
                logger.info(f"    - 新規追加: {title[:30]} (PID: {pid})")

            conn.commit()
            logger.info(f"  - 完了: {label} (新規: {new_count}件, スキップ: {skip_count}件)")

        except Exception as e:
            logger.error(f"DigiKet取得エラー ({label}): {e}")

    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Novelove Auto Posting Tool")
    parser.add_argument("--ranking", action="store_true", help="Run the ranking generation workflow instead of normal posting")
    args = parser.parse_args()

    if args.ranking:
        process_ranking_articles()
    else:
        main()
