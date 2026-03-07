#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
Novelove 自動投稿エンジン v7.1
【pending判定改善・Geminiあらすじチェック・画像Cookie修正版】
==========================================================
【変更点 v6.0 → v7.0】
 - fetch_and_stock_all(): 画像チェック+Geminiあらすじ判定でpending/watching振り分け
    promote_watching()
 - promote_watching(): watching作品を毎回再チェックしてpendingに昇格
   （画像なし→watching継続、画像あり→Geminiあらすじ判定→OK:pending/NG:watching）
 - post_to_wordpress(): 画像取得にFANZA用Cookieセッションを適用（NOW PRINTING対策）
==========================================================
【戦略】
 - モデル: 品質重視順（3-flash → 3.1-flash-lite → 2.5-flash → 2.5-flash-lite → 1.5-flash-latest）
 - 入力フィルター: 3段階（なし→軽め→ガチガチ）で順次試行
 - エラー分類: 429（レート制限）は3回まで再挑戦、内容NGは同一実行で3段階フィルター
 - ジャンル: FANZA（BL/TL小説・BL/乙女同人・ボイス）+ DMMブックス（一般BL/TL・女性向けコミック）
 - R-18タグ: ジャンルデータから自動判定（サイト名ではなくジャンル内容で判断）
 - PR表記: 短縮・汎用化（DMM・FANZA両対応）
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
from google import genai
from datetime import datetime

# --- Discord通知機能を内包 ---
def notify_discord(message, username="ノベラブ通知くん", avatar_url=None):
    """Discordに通知を送信（外部レポジトリ依存を解消）"""
    webhook_url = "https://discord.com/api/webhooks/1479116788343242833/CCuc9YCVfq38-bwlq2Ku2w8_5ru5W90Ezo-UrNvLri5QHR_t288EIvATRVBcXZlPRRMo"
    if not webhook_url: return False
    payload = {"content": message, "username": username}
    if avatar_url: payload["avatar_url"] = avatar_url
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        return r.status_code in (200, 204)
    except: return False
from datetime import datetime

# === 設定欄 ===
GEMINI_API_KEY        = "AIzaSyDwdQFxohNN3ZfdBZDLx4x1BJmCvEdRszE"
WP_SITE_URL           = "https://novelove.jp"
WP_USER               = "tomomin"
WP_APP_PASSWORD       = "FDn0z9epvJDer5v5inalAFPj"
DMM_API_ID            = "utU2Uz7rK9kSaVGD2NDU"
DMM_AFFILIATE_API_ID  = "novelove-990"
DMM_AFFILIATE_LINK_ID = "novelove-001"

# === 取得対象ジャンル定義 ===
FETCH_TARGETS = [
    # FANZA電子書籍（大人向け）
    {"site": "FANZA",   "service": "ebook",  "floor": "bl",             "genre": "BL",           "label": "BL小説",         "keyword": None},
    {"site": "FANZA",   "service": "ebook",  "floor": "tl",             "genre": "TL",           "label": "TL小説",         "keyword": None},
    # FANZA同人（大人向け）
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin", "genre": "doujin_bl",    "label": "BL同人",         "keyword": "ボーイズラブ"},
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin", "genre": "doujin_tl",    "label": "乙女同人",       "keyword": "乙女向け"},
    {"site": "FANZA",   "service": "doujin", "floor": "digital_doujin", "genre": "doujin_voice", "label": "ボイス",         "keyword": "ボイス 女性向け"},
    # FANZAゲーム
    {"site": "FANZA",   "service": "pcgame", "floor": "pcgame",         "genre": "pcgame",       "label": "PCゲーム",       "keyword": None},
    # DMMブックス（一般向け・腐女子刺さり系）
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_bl",     "label": "一般BL",         "keyword": "ボーイズラブ"},
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_tl",     "label": "一般TL",         "keyword": "ティーンズラブ"},
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_women",  "label": "女性向けコミック", "keyword": "女性向け"},
    # DLsite（乙女・BL・同人）
    {"site": "DLsite",  "service": None,     "floor": "girls",          "genre": "doujin_tl",    "label": "DLsite乙女",     "keyword": None},
    {"site": "DLsite",  "service": None,     "floor": "bl",             "genre": "doujin_bl",    "label": "DLsiteBL",      "keyword": None},
]

GENRE_TAGS = {
    "BL":           ["BL", "BL小説", "FANZA"],
    "TL":           ["TL", "TL小説", "FANZA"],
    "doujin_bl":    ["BL", "BL同人", "同人", "FANZA"],
    "doujin_tl":    ["乙女向け", "同人", "FANZA"],
    "doujin_voice": ["同人", "FANZA"],
    "comic_bl":     ["BL", "BLコミック", "一般"],
    "comic_tl":     ["TL", "TLコミック", "一般"],
    "comic_women":  ["女性向け", "一般"],
    "pcgame":       ["ゲーム", "女性向け", "FANZA"],
}

# === カテゴリ定義（ジャンル別） ===
GENRE_CATEGORIES = {
    "BL": 23, "doujin_bl": 23, "comic_bl": 23,     # BL作品
    "TL": 24, "doujin_tl": 24, "comic_tl": 24,     # TL作品
    "comic_women": 25,                             # 女性向け
    "doujin_voice": 25,                            # ボイスは一旦女性向けに
    "pcgame": 25                                   # ゲーム
}

# === 入力フィルター（3段階マスクマップ） ===
# DLsite版の開発を経て洗練された「最強辞書」を同期
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
    """
    入力テキストを指定レベルでマスキングする。
    level=0: マスクなし（生）
    level=1: 軽めマスク（MASK_LIGHT_MAPのみ）
    level=2: ガチガチマスク（LIGHT + EXTRA）
    """
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
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE_FANZA = os.path.join(SCRIPT_DIR, "novelove.db")
DB_FILE_DLSITE = os.path.join(SCRIPT_DIR, "novelove_dlsite.db")
LOG_FILE      = os.path.join(SCRIPT_DIR, "novelove.log")

# モデルリスト（品質重視順）
PRO_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-flash-latest",
]

CHECK_MODELS = [
    "gemini-2.0-flash-lite",
    "gemini-flash-lite-latest",
]

logger = logging.getLogger("novelove")
logger.setLevel(logging.INFO)
logger.propagate = False
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
    """ジャンルコードに対応できるREVIEWERSからランダムに1人選ぶ"""
    candidates = [r for r in REVIEWERS if genre in r["genres"]]
    if not candidates:
        candidates = REVIEWERS
    return random.choice(candidates)

def _genre_label(genre):
    """ジャンルコードを日本語ラベルに変換"""
    labels = {
        "BL": "BL小説",
        "TL": "TL小説",
        "doujin_bl": "BL同人",
        "doujin_tl": "乙女向け同人",
        "doujin_voice": "女性向けボイス作品",
        "comic_bl": "BLコミック",
        "comic_tl": "TLコミック",
        "comic_women": "女性向けコミック",
    }
    return labels.get(genre, "作品")

# === データベース管理（分離・不整合防止設計） ===
def get_db_path(site_raw):
    """サイト情報から適切なDBパスを返す"""
    if site_raw and "DLsite" in str(site_raw):
        return DB_FILE_DLSITE
    return DB_FILE_FANZA

def init_db():
    """DBの初期化・スキーマ同期"""
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
        # カラム追加のマイグレーション
        for col, definition in [
            ("retry_count", "INTEGER DEFAULT 0"),
            ("last_error",  "TEXT DEFAULT ''"),
            ("last_checked_at", "TEXT DEFAULT ''"),
            ("site", "TEXT DEFAULT ''"),
        ]:
            try:
                c.execute(f"ALTER TABLE novelove_posts ADD COLUMN {col} {definition}")
            except Exception: pass
        conn.commit()
        conn.close()

def reset_dlsite_failures():
    """DLsiteの失敗作品をリセットして新エンジンで再起させる"""
    if not os.path.exists(DB_FILE_DLSITE): return
    try:
        conn = sqlite3.connect(DB_FILE_DLSITE, timeout=30)
        c = conn.cursor()
        c.execute("UPDATE novelove_posts SET status='watching', retry_count=0 WHERE status!='published'")
        conn.commit()
        conn.close()
        logger.info("DLsiteリセット完了: 未投稿作品を再審査(watching)に設定しました")
    except Exception as e:
        logger.error(f"DLsiteリセット失敗: {e}")

# === 以前のDB定義を置換 ===

def _make_fanza_session():
    """年齢確認クッキーを持ったセッションを作成"""
    session = requests.Session()
    for domain in [".dmm.co.jp", ".book.dmm.co.jp", "book.dmm.co.jp", ".dmm.co.jp"]:
        session.cookies.set("age_check_done", "1", domain=domain)
        session.cookies.set("ckcy", "1", domain=domain)
    return session

HEADERS = {"User-Agent": "Mozilla/5.0"} # Added for the new scrape_dlsite_description function

def scrape_dlsite_description(url):
    """DLsiteのあらすじを確実かつクリーンに抽出する (v7.5 超硬化版)"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200: return ""
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # 不要な要素を事前に削除
        for trash in soup.select('.work_outline, .work_parts_area.outline, .work_parts_area.chobit, .work_edition'):
            trash.decompose()

        # 1. 作品画像 (OGPから確実に取得)
        image_url = ""
        og_img = soup.select_one('meta[property="og:image"]')
        if og_img:
            image_url = og_img.get("content", "")
        
        if not image_url:
            # 代替
            main_img = soup.select_one('.product_image_main img')
            if main_img: image_url = main_img.get('src')
        
        if image_url and image_url.startswith("//"):
            image_url = "https:" + image_url
        if "sam.jpg" in image_url:
            image_url = image_url.replace("sam.jpg", "main.jpg")

        # 2. あらすじ
        container = soup.select_one('.work_parts_container')
        if container:
            text = container.get_text(separator="\n", strip=True)
            # スペック情報を念のためさらに除去（"販売日"などが含まれていたらそれ以降を取る）
            if "作品内容" in text:
                text = text.split("作品内容")[-1]
            if len(text) > 100: return text.strip()

        # 2. 次点：見出し「作品内容」の直後の要素を探す
        for h3 in soup.find_all(['h3', 'div'], text=re.compile(r'作品内容')):
            next_div = h3.find_next_sibling('div')
            if next_div:
                text = next_div.get_text(separator="\n", strip=True)
                if len(text) > 50: return text.strip()

        # 3. 最終手段：og:description
        meta_desc = soup.select_one('meta[property="og:description"]')
        if meta_desc and meta_desc.get('content'):
            return meta_desc.get('content').strip()
            
        return ""
    except Exception as e:
        logger.error(f"DLsiteスクレイピングエラー: {e}")
        return ""

def _fetch_dlsite_items(target):
    """DLsiteの新着スクレイピング（詳細ページから確実な情報を取得）"""
    floor = target.get("floor", "girls")
    url = f"https://www.dlsite.com/{floor}/new/=/work_type/TOW" # とりあえず小説(TOW)
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
            
            # 詳細ページからOGP等を取得して精度を高める
            image_url = ""
            try:
                dr = requests.get(detail_url, headers=headers, timeout=10)
                dsoup = BeautifulSoup(dr.text, "html.parser")
                og_img = dsoup.select_one('meta[property="og:image"]')
                if og_img:
                    image_url = og_img.get("content", "")
            except: pass

            if not image_url:
                # 予備：サムネイル
                img_tag = work.select_one("img")
                if img_tag:
                    image_url = img_tag.get("src") or img_tag.get("data-src") or ""
            
            if image_url.startswith("//"):
                image_url = "https:" + image_url
            
            # 高画質化
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
            time.sleep(1) # 負荷軽減
    except Exception as e:
        logger.error(f"DLsite取得エラー: {e}")
    return items

def scrape_description(product_url, site="FANZA"):
    """商品ページからあらすじをスクレイピング"""
    if "dlsite" in str(product_url).lower():
        return scrape_dlsite_description(product_url)
    
    if not product_url:
        return ""
    session = _make_fanza_session()
    try:
        r = session.get(
            product_url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://book.dmm.co.jp/"},
            timeout=20
        )
        soup = BeautifulSoup(r.text, "html.parser")
        next_tag = soup.find("script", id="__NEXT_DATA__")
        if next_tag:
            try:
                ndata = json.loads(next_tag.string)
                desc = ndata.get("props", {}).get("pageProps", {}).get("product", {}).get("description", "")
                if len(desc.strip()) > 10:
                    return desc.strip()
            except Exception:
                pass
        og = soup.find("meta", property="og:description")
        if og and len(og.get("content", "")) > 10:
            return og.get("content").strip()
    except Exception as e:
        logger.warning(f"スクレイピング失敗: {e}")
    return ""

def _is_r18_item(item, site=None):
    """APIレスポンスのタイトルとジャンル情報からR-18判定（3段階）"""
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
    """APIレスポンスから作者名を抽出する"""
    # itemlist APIの作者情報はarticleフィールドに入っていることが多い
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
    """全ジャンルの新着作品を取得して適切なDBに蓄積"""
    from datetime import datetime
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
            # アフィリエイトURL生成
            if site == "DLsite":
                 aff_url = f"{item.get('URL')}?affiliate_id=novelove-001"
            else:
                 aff_url = (item.get("affiliateURL") or "").replace(DMM_AFFILIATE_API_ID, DMM_AFFILIATE_LINK_ID)
            
            is_r18 = 1 if _is_r18_item(item, site=site) else 0
            author = _extract_author(item)
            status = "watching"

            c.execute(
                """INSERT INTO novelove_posts
                    (product_id, title, author, genre, site, status, description,
                    affiliate_url, image_url, product_url, release_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, item.get("title"), author, target["genre"],
                    f"{site}:r18={is_r18}", status, desc, aff_url,
                    image_url, item.get("URL", ""), item.get("date", ""))
            )
            logger.info(f"[{site}] [確保] {item.get('title','')[:40]} ({target['label']})")
            added += 1
        conn.commit()
        conn.close()
        if added > 0: logger.info(f"{site}/{target['label']}: {added}件蓄積")

def _check_image_ok(image_url):
    """画像URLが実在するか、およびプレースホルダでないか確認"""
    if not image_url or not isinstance(image_url, str):
        return False
    
    # プレースホルダ文字列のチェック
    low_url = image_url.lower()
    placeholders = ["now_printing", "no_image", "noimage", "comingsoon", "dummy", "common/img"]
    if any(p in low_url for p in placeholders):
        return False

    try:
        # FANZAの302（画像なし）を検出しやすくするためセッションを使用
        r = _make_fanza_session().head(
            image_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
            allow_redirects=False
        )
        # 302リダイレクトは画像未準備
        if r.status_code == 302:
            return False
        # 200かつある程度のファイルサイズがあるか等を見る（可能なら）
        return r.status_code == 200
    except Exception:
        return False

def _check_desc_ok(title, desc, release_date_str=None):
    """Geminiにあらすじが記事執筆に十分か判定させる（モデルローテーション対応）"""
    if not desc or len(desc.strip()) < 5:
        return False

    # 未来フィルター：発売日が今日から7日より先の場合は判定をスキップ（後回し）
    if release_date_str:
        try:
            from datetime import datetime, timedelta
            today = datetime.now()
            release_date = datetime.strptime(release_date_str[:10], "%Y-%m-%d")
            if release_date > today + timedelta(days=7):
                # logger.info(f"  [スキップ] 発売まで7日以上: {title[:30]}")
                return False
        except Exception:
            pass

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""以下の作品のあらすじを見て、レビュー記事を書くのに十分な情報があるか判定してください。
「はい」か「いいえ」だけ答えてください。

作品タイトル: {title}
あらすじ: {desc}"""
    
    # グローバルなカウンタを使用してモデルを順次切り替える（ローテーション）
    if not hasattr(_check_desc_ok, "counter"):
        _check_desc_ok.counter = 0
    
    # 3つのモデルをローテーションして試す
    for _ in range(len(CHECK_MODELS)):
        idx = _check_desc_ok.counter % len(CHECK_MODELS)
        model_name = CHECK_MODELS[idx]
        _check_desc_ok.counter += 1
        
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            text = resp.text.strip() if hasattr(resp, "text") and resp.text else ""
            if text:
                # 判定ごとに2秒待機（負荷分散・429回避）
                time.sleep(2)
                return "はい" in text
        except Exception as e:
            logger.warning(f"Geminiあらすじ判定エラー ({model_name}): {e}")
            time.sleep(2)
            
    return False

def _check_stock_status(image_url, desc, title, release_date=""):
    """
    画像チェック + Geminiあらすじ判定でpending/watchingを返す
    画像なし → watching
    画像あり → Geminiで判定 → OK:pending / NG:watching
    """
    if not _check_image_ok(image_url):
        return "watching"
    if _check_desc_ok(title, desc, release_date):
        return "pending"
    return "watching"

def promote_watching():
    """watching作品の昇格（DB分離対応・FANZA救済基準）"""
    from datetime import datetime
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    now_date = now.date()

    # 両方のDBを巡回
    # --- 昇格フェーズ ---
    dbs_to_post = [DB_FILE_FANZA, DB_FILE_DLSITE] # Re-enable DLsite in dbs_to_post
    
    for db_path in dbs_to_post:
        # サイト名（ログ用）
        site_tag = "FANZA/DMM" if db_path == DB_FILE_FANZA else "DLsite"
        
        try:
            conn = sqlite3.connect(db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # 救済対象を抽出（発売済み・未発売問わず、前回チェックから24時間経過したもの）
            rows_to_process = c.execute(
                """SELECT product_id, title, image_url, description, release_date, status, retry_count
                   FROM novelove_posts
                   WHERE (status='watching' OR (status='failed_stock' AND retry_count < 3))
                   AND (last_checked_at IS NULL OR last_checked_at < datetime('now', '-24 hours'))
                   ORDER BY release_date ASC LIMIT 20"""
            ).fetchall()

            promoted_count = 0
            for r_item in rows_to_process:
                p_id, title, img, desc, r_date = r_item["product_id"], r_item["title"], r_item["image_url"], r_item["description"], r_item["release_date"]
                
                # チェック開始（日時更新）
                c.execute("UPDATE novelove_posts SET last_checked_at=datetime('now') WHERE product_id=?", (p_id,))
                
                status = _check_stock_status(img, desc, title, r_date)
                if status == "pending":
                    c.execute("UPDATE novelove_posts SET status='pending' WHERE product_id=?", (p_id,))
                    logger.info(f"[{site_tag}] [昇格] {title[:40]}")
                    promoted_count += 1
                else:
                    # 昇格失敗時：もし発売日を過ぎていたら、これ以上追わずに「お蔵入り(failed_stock)」へ
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
    """
    内部リンク取得（優先度: ①同じ作者 → ②同じジャンル）
    リンク切れの場合は最大5件まで後続を探す
    戻り値: {"title": ..., "url": ...} or None
    """
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    candidates = []

    # ①同じ作者の公開済み記事を上位5件取得
    if author and author.strip():
        candidates += c.execute(
            """SELECT title, wp_post_url FROM novelove_posts
               WHERE status='published' AND author=? AND product_id!=?
               AND wp_post_url != '' ORDER BY published_at DESC LIMIT 5""",
            (author.strip(), product_id)
        ).fetchall()

    # ②同じジャンルの公開済み記事を上位5件取得（候補が足りない場合）
    if len(candidates) < 5:
        candidates += c.execute(
            """SELECT title, wp_post_url FROM novelove_posts
               WHERE status='published' AND genre=? AND product_id!=?
               AND wp_post_url != '' ORDER BY published_at DESC LIMIT 5""",
            (genre, product_id)
        ).fetchall()

    conn.close()

    # 候補を順番にチェック
    seen_urls = set()
    for title, url in candidates:
        if url in seen_urls: continue
        seen_urls.add(url)
        
        if _check_wp_post_exists(url):
            return {"title": title, "url": url}
        else:
            # リンク切れ記事はDB更新（バックグラウンドで行うか、ここではログのみ）
            logger.warning(f"[内部リンク] リンク切れスキップ: {url}")
            # 本来はここでDBを failed_ai 等に更新すべきだが、副作用を避けるため一旦ログのみ
    
    return None

def _check_wp_post_exists(url):
    """WP記事が実在するか確認（404ならFalse）"""
    try:
        r = requests.head(url, timeout=10, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False

# === AI執筆 ===
def build_prompt(target, reviewer, mask_level=0, internal_link=None):
    """
    Cocoon吹き出し形式の記事生成プロンプトを構築（v6.0刷新版）
    構成: 冒頭コメント → 本文①作品紹介 → コメント② → 本文②刺さりポイント
          → コメント③ → 本文③こんな人におすすめ → コメント④総評+内部リンク
          → アフィリエイトリンク
    """
    safe_title = mask_input(target["title"], mask_level)
    safe_desc  = mask_input(target["description"], mask_level)
    label      = _genre_label(target["genre"])

    chat_open  = f'<div class="speech-bubble-left"><img src="/wp-content/uploads/icons/{reviewer["face_image"]}.png" alt="{reviewer["name"]}" />\n<div class="speech-text">'
    chat_close = '</div>\n</div>'

    voice_hint = ""
    if target["genre"] == "doujin_voice":
        voice_hint = "\n【ボイス作品紹介のコツ】声優の演技・音質・耳への心地よさに言及すること。「耳が溶ける」「ヘッドホン必須」「通勤中に聴けない」などのリアクションを使ってもOK。"

    # 内部リンクブロックHTMLを生成（コメント④の外に独立配置）
    if internal_link:
        internal_link_html = f'''<div style="border:1px solid #f0c0c0; border-radius:8px; padding:15px; margin:20px 0; background:#fff8f8;">
<p style="margin:0 0 8px; font-weight:bold; color:#c0607f;">📚 あわせて読みたい</p>
<a href="{internal_link["url"]}">{internal_link["title"]}</a>
</div>'''
        internal_link_instruction = "（今回は内部リンクなし。コメント④は総評のみでOK）"
    else:
        internal_link_html = ""
        internal_link_instruction = "（今回は内部リンクなし。コメント④は総評のみでOK）"

    return f"""あなたは人気ファンブログ「Novelove」のライター「{reviewer["name"]}」です。

【キャラクター設定】
名前: {reviewer["name"]}
性格: {reviewer["personality"]}
文体・口調: {reviewer["tone"]}

【執筆ルール】
1. キャラクターコメント（吹き出し）と記事本文（HTMLタグ部分）を完全に書き分けること。
2. 記事本文（<h2>, <p>, <ul>, <li>タグの中身）は、いかなる場合も**「標準的で丁寧な日本語（ですます調）」**で、客観的な紹介文として執筆すること。担当ライターの口調や一人称（私、僕など）を混ぜないこと。
3. 直接的な性的単語（性器の名称・行為の直接名称）は絶対に使用禁止。代わりに官能的な比喩を使うこと。
4. キャラクターコメント（吹き出し）の中身のみ、{reviewer["name"]}の個性を全開にした口調で、主観的な熱い感想や叫びを執筆すること。
5. 紹介対象は「{label}」です。小説・漫画オタク的な表現（神作、沼、尊いなど）は吹き出しコメントの中でのみ使用すること。
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

<p>（標準語で執筆）あらすじ・世界観。300〜400字程度。適宜改行を入れる。</p>

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

def call_gemini(prompt):
    """
    Gemini API呼び出し（BLOCK_NONE・フォールバック付き）
    戻り値: (text, error_type, model_name)
    """
    client = genai.Client(api_key=GEMINI_API_KEY)
    safety_settings = [
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    last_error_type = "content_block"
    for model_name in PRO_MODELS:
        try:
            logger.info(f"  [{model_name}] 執筆依頼...")
            t_start = time.time()
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"safety_settings": safety_settings}
            )
            t_end = time.time()
            proc_time = round(t_end - t_start, 1)
            # テキスト抽出
            text = None
            try:
                if hasattr(resp, "text") and resp.text:
                    text = resp.text
            except Exception:
                pass
            if not text:
                try:
                    text = resp.candidates[0].content.parts[0].text
                except Exception:
                    pass
            if text and len(text.strip()) > 50:
                logger.info(f"  [{model_name}] 執筆完了（{len(text)}文字 / {proc_time}秒）")
                return text.strip(), "ok", model_name, proc_time
            logger.warning(f"  [{model_name}] 空答（コンテンツ拒絶の可能性）")
            last_error_type = "content_block"
        except Exception as e:
            err_str = str(e)
            logger.warning(f"  [{model_name}] エラー: {err_str[:100]}")
            if "429" in err_str:
                last_error_type = "rate_limit"
                time.sleep(5)
            else:
                last_error_type = "content_block"
    return "", last_error_type, "None", 0.0

def make_excerpt(description, title, genre):
    """あらすじからSEO用のexcerptを生成する"""
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
    戻り値: (wp_title, content, excerpt, seo_title, is_r18, error_type, model_name, filter_level)
    """
    reviewer = _get_reviewer_for_genre(target["genre"])

    # 内部リンクを取得（①同じ作者 → ②同じジャンル）
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
        logger.info(f"  [内部リンク] 該当なし（今回はなし）")

    final_error = "content_block"
    final_model = "None"
    final_proc_time = 0.0
    for mask_level in [0, 1, 2]:
        level_name = ["フィルターなし", "軽めフィルター", "ガチガチフィルター"][mask_level]
        logger.info(f"  [{level_name}] で執筆試行中...")
        prompt  = build_prompt(target, reviewer, mask_level, internal_link)
        content, error_type, model_name, proc_time = call_gemini(prompt)
        final_error = error_type
        final_model = model_name
        final_proc_time = proc_time

        if content:
            # 【最終画像チェック】投稿直前にもう一度確認
            if not _check_image_ok(target["image_url"]):
                logger.warning(f"  [画像NG] 投稿直前のチェックで画像が無効と判定されました: {target['image_url']}")
                return None, None, None, None, False, "image_missing", model_name, level_name, proc_time, 0

            img_html   = f'<p style="text-align:center;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow"><img src="{target["image_url"]}" alt="{target["title"]}" style="max-width:300px;border-radius:8px;box-shadow:0 4px 15px rgba(0,0,0,0.15);" /></a></p>\n'
            # サイト表示名の正規化
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

            # アフィリンクとテキストリンク
            text_link  = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:5px; margin-bottom:15px;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow" style="text-decoration:none; color:#d81b60;">▶ 『{target["title"]}』の詳細をチェック！</a></p>\n'

            # PR表記
            service_name = site_display
            credit_html = f'<p style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee; font-size:0.8em; color:#bbb;">\nPRESENTED BY {service_name} / Novelove Affiliate Program\n</p>\n'

            # 発売日表示
            release_display = ""
            if target.get("release_date"):
                try:
                    rd = target["release_date"][:10].replace("-", "/")
                    release_display = f'<p style="text-align:center; color:#666; font-size:0.9em; margin-bottom:10px;">発売日：{rd}</p>\n'
                except: pass

            excerpt    = make_excerpt(target["description"], target["title"], target["genre"])
            label      = _genre_label(target["genre"])
            seo_title  = f"{target['title']}を{reviewer['name']}が紹介！「{label}」{reviewer['name']}の本音 | Novelove"
            if len(seo_title) > 60:
                seo_title = f"{target['title'][:30]}…を{reviewer['name']}が紹介 | Novelove"
            wp_title   = target["title"]

            full_content = badge_html + img_html + release_display + text_link + content + credit_html
            word_count = len(content)
            # site情報を正規化してis_r18判定
            site_raw = target.get("site", "FANZA")
            is_r18_val = ":r18=1" in str(site_raw)
            return wp_title, full_content, excerpt, seo_title, is_r18_val, "ok", model_name, level_name, proc_time, word_count

        if error_type == "rate_limit":
            logger.warning(f"  レート制限エラー → フィルター試行を中断")
            break

        logger.warning(f"  [{level_name}] 失敗 → 次のフィルターレベルへ")

    return None, None, None, None, False, final_error, final_model, "None", final_proc_time, 0

# === WordPress投稿（REST API）===
def post_to_wordpress(title, content, genre, image_url, excerpt="", seo_title="", slug="", is_r18=False):
    """WordPress REST API + アプリケーションパスワードで投稿"""
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

    def get_or_create_term(name, taxonomy):
        try:
            r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/{taxonomy}", auth=auth, params={"search": name}, timeout=15)
            hits = r.json()
            if hits:
                return hits[0]["id"]
            r2 = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/{taxonomy}", auth=auth, json={"name": name}, timeout=15)
            return r2.json().get("id")
        except Exception:
            return None

    cat_id = GENRE_CATEGORIES.get(genre, 25) # デフォルトは女性向け(25)かニュース(4)だが、安全に女性向けへ
    tag_names = list(GENRE_TAGS.get(genre, ["その他"]))

    tl_kws = {"TL", "ティーンズラブ", "乙女", "花嫁", "娘", "お嬢", "令嬢", "女性向け"}
    bl_kws = {"BL", "ボーイズラブ"}
    target_text_for_tag = title + genre
    has_tl = any(k in target_text_for_tag for k in tl_kws)
    has_bl = any(k in title for k in bl_kws)

    if "BL" in tag_names:
        if has_tl and not has_bl:
            tag_names = [t for t in tag_names if "BL" not in t]
            logger.info(f"   [BL除外] TL判定のためタグを削除: {title}")

    if is_r18:
        if "R-18" not in tag_names:
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
    logger.info("Novelove エンジン v7.3 【関連記事強化・口調分離・バランス調整版】 起動")
    init_db()
    reset_dlsite_failures() # DLsiteの失敗分をリセット
    fetch_and_stock_all()
    promote_watching()

    # 投稿ループ（ラウンドロビン）
    posted    = False
    max_tries = 10
    tries     = 0
    
    while not posted and tries < max_tries:
        tries += 1
        if not hasattr(main, "genre_index"):
            main.genre_index = 0
        current_genre_info = FETCH_TARGETS[main.genre_index % len(FETCH_TARGETS)]
        main.genre_index += 1
        
        site_for_db = current_genre_info.get("site", "FANZA")
        db_path = get_db_path(site_for_db)
        genre = current_genre_info["genre"]

        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        
        # 指定ジャンルのpendingを探す
        row = c.execute(
            "SELECT * FROM novelove_posts WHERE status='pending' AND genre=? LIMIT 1", (genre,)
        ).fetchone()

        if not row:
            # 該当ジャンルがなければ、同じDB内の別ジャンルをランダムに
            row = c.execute(
                "SELECT * FROM novelove_posts WHERE status='pending' ORDER BY RANDOM() LIMIT 1"
            ).fetchone()

        if not row:
            conn.close()
            continue

        # DBのカラム位置に合わせてマッピング (Rowオブジェクトでないため)
        # 0:product_id, 1:title, 2:author, 3:genre, 4:site, 5:status, 6:release_date, 7:description, 8:affiliate_url, 9:image_url, 10:product_url, 11:wp_post_url, 12:retry_count, 13:published_at, 14:last_checked_at, 15:last_error
        target = {
            "product_id": row[0], "title": row[1], "author": row[2] or "",
            "genre": row[3], "site": row[4], "description": row[7],
            "affiliate_url": row[8], "image_url": row[9],
            "release_date": row[6] or "", "is_r18": ":r18=1" in str(row[4])
        }
        retry_count = row[12] if len(row) > 12 else 0 # retry_count

        logger.info(f"【ターゲット決定】 {target['title']} (DB: {os.path.basename(db_path)})")

        res_data = generate_article(target)
        if not res_data:
             conn.close()
             continue
        
        wp_title, content, excerpt, seo_title, is_r18_val, error_type, model_name, filter_level, proc_time, word_count = res_data

        if content:
            url = post_to_wordpress(
                wp_title, content, target["genre"], target["image_url"],
                excerpt, seo_title, slug=target["product_id"], is_r18=is_r18_val
            )
            if url:
                c.execute(
                    "UPDATE novelove_posts SET status='published', wp_post_url=?, published_at=datetime('now') WHERE product_id=?",
                    (url, target["product_id"])
                )
                conn.commit()
                
                # 統計情報の取得
                daily_count = c.execute("SELECT COUNT(*) FROM novelove_posts WHERE status='published' AND published_at >= date('now', 'localtime')").fetchone()[0]
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
                conn.close() # Close connection on WP post failure
                break # Break the loop if WP posting fails
        else:
             # 生成失敗時のリトライ・NG処理
             new_retry = retry_count + 1
             if error_type == "rate_limit":
                 c.execute("UPDATE novelove_posts SET retry_count=? WHERE product_id=?", (new_retry, target["product_id"]))
             else:
                 c.execute("UPDATE novelove_posts SET status='failed_ai' WHERE product_id=?", (target["product_id"],))
             conn.commit()
        
        conn.close()

    logger.info("=" * 60)

if __name__ == "__main__":
    main()
