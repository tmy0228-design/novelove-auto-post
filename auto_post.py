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
from bs4 import BeautifulSoup
from google import genai

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
    # DMMブックス（一般向け・腐女子刺さり系）
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_bl",     "label": "一般BL",         "keyword": "ボーイズラブ"},
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_tl",     "label": "一般TL",         "keyword": "ティーンズラブ"},
    {"site": "DMM.com", "service": "ebook",  "floor": "comic",          "genre": "comic_women",  "label": "女性向けコミック", "keyword": "女性向け"},
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
}

# === カテゴリ定義（ジャンル別） ===
GENRE_CATEGORIES = {
    "BL": 23, "doujin_bl": 23, "comic_bl": 23,     # BL作品
    "TL": 24, "doujin_tl": 24, "comic_tl": 24,     # TL作品
    "comic_women": 25,                             # 女性向け
    "doujin_voice": 25                             # ボイスは一旦女性向けに
}

# === 入力フィルター（3段階マスクマップ） ===
MASK_LIGHT_MAP = {
    "セックス": "熱く溶け合う",
    "SEX": "熱く溶け合う",
    "sex": "熱く溶け合う",
    "強姦": "無理やり関係を迫る",
    "レイプ": "無理やり関係を迫る",
    "輪姦": "複数人との強制的な関係",
    "複数人で押さえつけ": "複数人に囲まれ",
    "陵辱": "辱め",
    "生ハメ": "無防備な行為",
    "やりまくり": "溺れるように求め合い",
    "ナカに入れ": "深く求め",
    "クリトリス": "最も敏感な秘密の場所",
    "膣内": "最奥",
    "肉棒": "熱い塊",
    "蜜壺": "蜜の泉",
    "乳首": "敏感な突起",
    "性器": "秘めた部分",
    "精液": "愛の証",
    "孕ませ": "子を宿させ",
    "種付け": "命を宿させ",
}

MASK_EXTRA_MAP = {
    "SMクラブ": "背徳の社交場",
    "M奴隷": "快楽に身を委ねた存在",
    "ご主人様": "支配者",
    "拷問": "激しい責め",
    "調教": "快楽に染めていく",
    "ナカ": "最奥",
    "クリ": "最も敏感な場所",
    "イく": "限界を超える",
    "イっ": "限界を超え",
    "イき": "限界を超えた",
    "クリイキ": "限界を超えた",
    "絶頂": "理性が溶ける瞬間",
    "オナニー": "自己愛撫",
    "性感ほぐし": "体の奥のほぐし",
    "アダルトグッズ": "大人向けグッズ",
    "催淫": "抗えない甘い誘惑",
    "淫ら": "官能的",
    "薬物": "謎めいた物質",
    "ドラッグ": "禁断の誘惑",
    "LSD": "幻想的な体験",
    "アヤワスカ": "神秘の儀式",
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
DB_FILE    = os.path.join(SCRIPT_DIR, "novelove.db")
LOG_FILE   = os.path.join(SCRIPT_DIR, "novelove.log")

# モデルリスト（品質重視順・無料枠確定モデルのみ）
# v6.0変更: 3.1-pro（有料）・2.0-flash（廃止）を削除、新モデルを追加
PRO_MODELS = [
    "gemini-3-flash-preview",         # 最高品質・無料枠あり（最優先）
    "gemini-3.1-flash-lite-preview",  # 高速・軽量・新モデル
    "gemini-2.5-flash",               # 安定版・無料枠確定
    "gemini-2.5-flash-lite",          # 軽量・無料枠確定
    "gemini-1.5-flash-latest",        # 最終手段
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

# === データベース ===
def init_db():
    """DBの初期化・スキーマのマイグレーション"""
    conn = sqlite3.connect(DB_FILE)
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
        published_at TIMESTAMP
    )''')
    for col, definition in [
        ("retry_count", "INTEGER DEFAULT 0"),
        ("last_error",  "TEXT DEFAULT ''"),
    ]:
        try:
            c.execute(f"ALTER TABLE novelove_posts ADD COLUMN {col} {definition}")
        except Exception:
            pass
    conn.commit()
    conn.close()

def _make_fanza_session():
    """年齢確認クッキーを持ったセッションを作成"""
    session = requests.Session()
    for domain in [".dmm.co.jp", ".book.dmm.co.jp", "book.dmm.co.jp", ".dmm.co.jp"]:
        session.cookies.set("age_check_done", "1", domain=domain)
        session.cookies.set("ckcy", "1", domain=domain)
    return session

def scrape_description(product_url):
    """商品ページからあらすじをスクレイピング（年齢確認Cookie対応）"""
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
    """DMM/FANZA APIから全ジャンルの新着作品を取得してDBに蓄積"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    added = 0

    for target in FETCH_TARGETS:
        site = target.get("site", "FANZA")
        params = {
            "api_id": DMM_API_ID,
            "affiliate_id": DMM_AFFILIATE_API_ID,
            "site": site,
            "service": target["service"],
            "floor": target["floor"],
            "hits": 100,  # v6.0: 20→100に増加（ストック枯渇防止）
            "sort": "date",
            "output": "json",
        }
        if target.get("keyword"):
            params["keyword"] = target["keyword"]

        try:
            res = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=params, timeout=15).json()
            for item in res.get("result", {}).get("items", []):
                pid = item.get("content_id")
                if not pid:
                    continue
                if c.execute("SELECT 1 FROM novelove_posts WHERE product_id=?", (pid,)).fetchone():
                    continue
                desc = scrape_description(item.get("URL", ""))
                time.sleep(1.0)
                image_url = item.get("imageURL", {}).get("large", "")
                aff_url = (item.get("affiliateURL") or "").replace(DMM_AFFILIATE_API_ID, DMM_AFFILIATE_LINK_ID)
                is_r18 = 1 if _is_r18_item(item, site=site) else 0
                author = _extract_author(item)

                # v7.0: 画像チェック+Geminiあらすじ判定でpending/watching振り分け
                status = _check_stock_status(image_url, desc, item.get("title", ""))

                c.execute(
                    """INSERT INTO novelove_posts
                       (product_id, title, author, genre, site, status, description,
                        affiliate_url, image_url, product_url, release_date)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, item.get("title"), author, target["genre"],
                     f"{site}:r18={is_r18}", status, desc, aff_url,
                     image_url, item.get("URL", ""), item.get("date", ""))
                )
                logger.info(f"[確保] {item.get('title','')[:40]} ({target['label']}/{status}/r18={is_r18}/author={author[:10]})")
                added += 1
        except Exception as e:
            logger.error(f"API エラー ({site}/{target['label']}): {e}")
            continue

    conn.commit()
    conn.close()
    logger.info(f"ストック完了: {added}件追加")

def _check_image_ok(image_url):
    """画像URLが実在するか確認（302リダイレクト=NG）"""
    if not image_url:
        return False
    try:
        r = _make_fanza_session().head(
            image_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
            allow_redirects=False
        )
        return r.status_code == 200
    except Exception:
        return False

def _check_desc_ok(title, desc):
    """Geminiにあらすじが記事執筆に十分か判定させる"""
    if not desc or len(desc.strip()) < 5:
        return False
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""以下の作品のあらすじを見て、レビュー記事を書くのに十分な情報があるか判定してください。
「はい」か「いいえ」だけ答えてください。

作品タイトル: {title}
あらすじ: {desc}"""
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
        )
        text = resp.text.strip() if hasattr(resp, "text") and resp.text else ""
        return "はい" in text
    except Exception as e:
        logger.warning(f"Geminiあらすじ判定エラー: {e}")
        return False

def _check_stock_status(image_url, desc, title):
    """
    画像チェック + Geminiあらすじ判定でpending/watchingを返す
    画像なし → watching
    画像あり → Geminiで判定 → OK:pending / NG:watching
    """
    if not _check_image_ok(image_url):
        return "watching"
    if _check_desc_ok(title, desc):
        return "pending"
    return "watching"

def promote_watching():
    """
    watching状態の作品を再チェックしてpendingに昇格させる（v7.0）
    - 発売日が今日以前のwatchingのみ対象
    - OK → pending昇格
    - NG → failed_stock（売る気なし商品としてお蔵入り）
    毎回main()実行時に呼び出す
    """
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    rows = c.execute(
        """SELECT product_id, title, image_url, description FROM novelove_posts
           WHERE status='watching' AND release_date <= ?""",
        (today + " 23:59:59",)
    ).fetchall()

    promoted = 0
    failed = 0
    for product_id, title, image_url, desc in rows:
        status = _check_stock_status(image_url, desc, title)
        if status == "pending":
            c.execute(
                "UPDATE novelove_posts SET status='pending' WHERE product_id=?",
                (product_id,)
            )
            logger.info(f"[昇格] watching→pending: {title[:40]}")
            promoted += 1
        else:
            c.execute(
                "UPDATE novelove_posts SET status='failed_stock', last_error='発売日当日でも画像/あらすじ不備' WHERE product_id=?",
                (product_id,)
            )
            logger.info(f"[お蔵入り] watching→failed_stock: {title[:40]}")
            failed += 1

    conn.commit()
    conn.close()
    logger.info(f"watching再チェック完了: {promoted}件昇格 / {failed}件お蔵入り")

def get_internal_link(product_id, author, genre):
    """
    内部リンク取得（優先度: ①同じ作者 → ②同じジャンル）
    リンク切れの場合は最大5件まで後続を探す
    戻り値: {"title": ..., "url": ...} or None
    """
    conn = sqlite3.connect(DB_FILE)
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
    戻り値: (text, error_type)
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
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"safety_settings": safety_settings}
            )
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
                logger.info(f"  [{model_name}] 執筆完了（{len(text)}文字）")
                return text.strip(), "ok"
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
    return "", last_error_type

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
    戻り値: (wp_title, content, excerpt, seo_title, is_r18, error_type)
    """
    reviewer = _get_reviewer_for_genre(target["genre"])

    # 内部リンクを取得（①同じ作者 → ②同じジャンル）
    internal_link = get_internal_link(
        target["product_id"],
        target.get("author", ""),
        target["genre"]
    )
    if internal_link:
        logger.info(f"  [内部リンク] 取得成功: {internal_link['title'][:30]}")
    else:
        logger.info(f"  [内部リンク] 該当なし（今回はなし）")

    final_error = "content_block"
    for mask_level in [0, 1, 2]:
        level_name = ["フィルターなし", "軽めフィルター", "ガチガチフィルター"][mask_level]
        logger.info(f"  [{level_name}] で執筆試行中...")
        prompt  = build_prompt(target, reviewer, mask_level, internal_link)
        content, error_type = call_gemini(prompt)
        final_error = error_type

        if content:
            img_html   = f'<p style="text-align:center;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow"><img src="{target["image_url"]}" alt="{target["title"]}" style="max-width:300px;border-radius:8px;box-shadow:0 4px 15px rgba(0,0,0,0.15);" /></a></p>\n'
            text_link  = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:5px; margin-bottom:15px;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow">▶ 『{target["title"]}』の詳細をチェック！</a></p>\n'

            is_r18 = target.get("is_r18", False)
            if is_r18:
                credit_html = '<p style="text-align:center; margin-top:30px; padding-top:10px; border-top:1px solid #eee;">\n<a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.co.jp/p/affiliate/web_service/r18_135_17.gif" width="135" height="17" alt="WEB SERVICE BY FANZA" /></a>\n</p>\n'
            else:
                credit_html = '<p style="text-align:center; margin-top:30px; padding-top:10px; border-top:1px solid #eee;">\n<a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/com_135_17.gif" width="135" height="17" alt="WEB SERVICE BY DMM.com" /></a>\n</p>\n'

            excerpt    = make_excerpt(target["description"], target["title"], target["genre"])
            label      = _genre_label(target["genre"])
            seo_title  = f"{target['title']}を{reviewer['name']}が紹介！「{label}」{reviewer['name']}の本音 | Novelove"
            if len(seo_title) > 60:
                seo_title = f"{target['title'][:30]}…を{reviewer['name']}が紹介 | Novelove"
            wp_title   = target["title"]

            full_content = img_html + text_link + content + credit_html
            return wp_title, full_content, excerpt, seo_title, is_r18, "ok"

        if error_type == "rate_limit":
            logger.warning(f"  レート制限エラー → フィルター試行を中断")
            break

        logger.warning(f"  [{level_name}] 失敗 → 次のフィルターレベルへ")

    return None, None, None, None, False, final_error

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
    fetch_and_stock_all()
    promote_watching()

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    posted    = False
    max_tries = 10
    tries     = 0

    while not posted and tries < max_tries:
        tries += 1

        row = c.execute(
            """SELECT product_id, title, author, genre, description, affiliate_url,
                      image_url, retry_count, site
               FROM novelove_posts
               WHERE status='pending' AND retry_count < 3
               ORDER BY release_date DESC LIMIT 1"""
        ).fetchone()

        if not row:
            logger.info("【待機中】 pending状態の作品がありません。")
            break

        site_raw = row[8] or ""
        is_r18 = ":r18=1" in site_raw
        target = {
            "product_id":   row[0],
            "title":        row[1],
            "author":       row[2] or "",
            "genre":        row[3],
            "description":  row[4],
            "affiliate_url": row[5],
            "image_url":    row[6],
            "is_r18":       is_r18,
        }
        retry_count = row[7]
        logger.info(f"【ターゲット決定】 {target['title']} (retry:{retry_count}/author:{target['author'][:15]})")

        wp_title, content, excerpt, seo_title, is_r18, error_type = generate_article(target)

        if content:
            url = post_to_wordpress(
                wp_title, content, target["genre"], target["image_url"],
                excerpt, seo_title, slug=target["product_id"], is_r18=is_r18
            )
            if url:
                c.execute(
                    "UPDATE novelove_posts SET status='published', wp_post_url=?, published_at=datetime('now') WHERE product_id=?",
                    (url, target["product_id"])
                )
                conn.commit()
                logger.info(f"✅ 投稿成功！ URL: {url}")
                posted = True
            else:
                logger.error("❌ WordPress投稿に失敗しました。")
                break
        else:
            new_retry = retry_count + 1
            if error_type == "rate_limit":
                logger.warning(f"⏳ レート制限エラー → retry_count={new_retry}。次回再挑戦します。")
                c.execute(
                    "UPDATE novelove_posts SET retry_count=?, last_error='rate_limit' WHERE product_id=?",
                    (new_retry, target["product_id"])
                )
                conn.commit()
            else:
                logger.error(f"❌ 3段階フィルター全滅 → failed_ai に変更")
                c.execute(
                    "UPDATE novelove_posts SET status='failed_ai', retry_count=?, last_error='content_block' WHERE product_id=?",
                    (new_retry, target["product_id"])
                )
                conn.commit()
            c.execute(
                "UPDATE novelove_posts SET status='failed_ai' WHERE status='pending' AND retry_count >= 3"
            )
            conn.commit()

    conn.close()
    logger.info("=" * 60)

if __name__ == "__main__":
    main()
