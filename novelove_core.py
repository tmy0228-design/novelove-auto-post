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
    article_pattern: str          = ""    # v16.0.0: 使用されたHTML骨格パターン (A/B/C/D/R)


# === .env 読み込み（全モジュール共通、ここで1回だけ行う） ===
_env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(_env_path):
    load_dotenv(_env_path)
else:
    load_dotenv()

# === システム設定定数 ===
import time
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
DB_FILE_UNIFIED = os.path.join(SCRIPT_DIR, "novelove_unified.db")  # v18.0.0: 統合DB
LOG_FILE        = os.path.join(SCRIPT_DIR, "novelove.log")
MAIN_LOCK_FILE  = os.path.join(SCRIPT_DIR, "main.lock")
RANK_LOCK_FILE  = os.path.join(SCRIPT_DIR, "ranking.lock")
INDEX_FILE      = os.path.join(SCRIPT_DIR, "genre_index.txt")
EMERGENCY_LOCK_FILE = os.path.join(SCRIPT_DIR, "emergency_stop.lock")
WP_SITE_URL     = os.environ.get("WP_SITE_URL", "https://novelove.jp")  # C-2: テスト環境切り替えを.envで制御可能に

# === 環境変数（一元管理） ===
DEEPSEEK_API_KEY      = os.environ.get("DEEPSEEK_API_KEY", "")
# v17.0.0: Grokハイブリッド移行。OPENROUTER_API_KEYがあればそれを使い、 lazyなフォールバック
OPENROUTER_API_KEY    = os.environ.get("OPENROUTER_API_KEY") or DEEPSEEK_API_KEY
WP_USER               = os.environ.get("WP_USER", "")
WP_APP_PASSWORD       = os.environ.get("WP_APP_PASSWORD", "")
DMM_API_ID            = os.environ.get("DMM_API_ID", "")
DMM_AFFILIATE_API_ID  = os.environ.get("DMM_AFFILIATE_API_ID", "")
DMM_AFFILIATE_LINK_ID = os.environ.get("DMM_AFFILIATE_LINK_ID", "")
DLSITE_AFFILIATE_ID   = os.environ.get("DLSITE_AFFILIATE_ID", "novelove")

# ※ セキュリティ上、デフォルト値なし。未設定時は呼び出し元がエラー終了する。
SSH_PASS              = os.environ.get("SSH_PASS", "")
# === WP-CLI / サーバー環境依存パス ===
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

# === 原子ロック制御関数 (v21.3.0 追加) ===
def acquire_lock(lock_path, stale_timeout=7200):
    """
    原子的にロックファイルを作成する。
    - 成功: True
    - 失敗: False (期限切れ時は強制解除して再試行)
    """
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()}\n{datetime.datetime.now().isoformat()}\n".encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            mtime = os.path.getmtime(lock_path)
            if time.time() - mtime > stale_timeout:
                logger.warning(f"🚨 ロック {lock_path} が {stale_timeout//3600} 時間を超えています。強制解除します。")
                try:
                    os.remove(lock_path)
                except Exception:
                    pass
                # 再試行
                try:
                    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, f"{os.getpid()}\n{datetime.datetime.now().isoformat()}\n".encode())
                    os.close(fd)
                    return True
                except FileExistsError:
                    return False
            else:
                return False
        except OSError:
            return False

def release_lock(lock_path):
    """ロックファイルを安全に削除する"""
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception as e:
        logger.error(f"🚨 ロック解除失敗 ({lock_path}): {e}")

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
    # v19.0.0: ボイスジャンル対応
    if "voice" in g_lower:
        is_bl = "bl" in g_lower or "BL" in str(genre)
        return "BLボイス" if is_bl else "TL/乙女ボイス"
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
        f'<a href="{url}" target="_blank" rel="nofollow noopener" style="{AFFILIATE_BUTTON_STYLE}">'
        f'{label}</a></div>'
    )

# === アフィリエイトURL生成（一元管理）===
# ASP側の仕様変更はここ一箇所を修正するだけで全サイトに反映される。
def generate_affiliate_url(site: str, product_url: str, **kwargs) -> str:
    """
    サイト別にアフィリエイトURLを生成して返す共通関数。
    
    Args:
        site:        "FANZA" | "DMM.com" | "DLsite"
        product_url: 商品ページURL（FANZA/DMM）または空文字（DLsite）
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



        # FANZA / DMM.com
        af_id      = DMM_AFFILIATE_LINK_ID or "novelove-001"
        ch_params  = "&ch=toolbar&ch_id=text"
        encoded    = urllib.parse.quote(product_url, safe="")
        
        # 🌟 Lovecal (らぶカル) のURLはFANZA仕様のアフィリンクだと死ぬためDMM用に強制置換
        if "lovecul.dmm.co.jp" in product_url:
            return f"https://al.dmm.com/?lurl={encoded}&af_id={af_id}{ch_params}"
            
        if site in ("FANZA", "Lovecal"):
            return f"https://al.fanza.co.jp/?lurl={encoded}&af_id={af_id}{ch_params}"
        # DMM.com
        return f"https://al.dmm.com/?lurl={encoded}&af_id={af_id}{ch_params}"

    except Exception:
        return product_url  # フォールバック: 元のURLをそのまま返す


def get_db_path(site_raw=None):
    """後方互換のため残す。v18.0.0以降は常に統合DBを返す。"""
    return DB_FILE_UNIFIED

def get_source_db(site_raw):
    """site文字列からsource_dbグループ文字列を返す。
    戻り値: 'dmm' / 'lovecal' / 'dlsite'
    """
    s = str(site_raw)
    if "DLsite"  in s: return "dlsite"
    if "Lovecal" in s or "lovecal" in s: return "lovecal"
    return "dmm"  # ※らぶカル以外のDMM一般・成人商業を 'dmm' に統合

def normalize_title(title):
    """タイトルから装飾（括弧とその中身）とスペースを除去し、スッピン文字列を返す。"""
    t = str(title)
    # 波ダッシュ・ダッシュ類の表記ゆれを統一（〜 vs ‾ 等で類似度が割れるのを防ぐ）
    t = t.translate(str.maketrans({
        "～": "〜",  # fullwidth tilde
        "‾": "〜",  # overline (DLsite等で波線代わりに使われる)
        "∼": "〜",
        "〰": "〜",
        "－": "ー",
        "―": "ー",
        "─": "ー",
        "–": "ー",
        "—": "ー",
        "-": "ー",
    }))
    # 全体が括弧で囲まれている場合、括弧のみを除去して中身を保護する
    brackets = [('「', '」'), ('『', '』'), ('【', '】'), ('（', '）'), ('(', ')'), ('[', ']')]
    for start, end in brackets:
        if t.startswith(start) and t.endswith(end):
            t = t[1:-1]
            break

    # 数字を含む話数・巻数の括弧を一時的に保護する
    # 例: (1), (15), （2）, [3], #4, vol.5, Vol.6, ①, ②, (3話) など
    temp_placeholders = []
    def replace_digit_bracket(match):
        placeholder = f"__DIGIT_BRACKET_{len(temp_placeholders)}__"
        temp_placeholders.append((placeholder, match.group(0)))
        return placeholder

    # 数字のみ、またはvol/Vol付きの数字、巻・話などの漢字を含む括弧を一時置換
    t = re.sub(
        r'([\[\(（【〈《「『](?:\d+|[0-9]+|vol\.\d+|Vol\.\d+|[①-⑨]|[a-zA-Z0-9]+話|[a-zA-Z0-9]+巻)[\]\)）】〉》」』])',
        replace_digit_bracket,
        t
    )

    # 通常の装飾用の括弧とその中身を除去
    t = re.sub(r'[\[\(（【〈《「『].*?[\]\)）】〉》」』]', '', t)

    # 一時退避させていた話数括弧を戻す
    for placeholder, original in temp_placeholders:
        t = t.replace(placeholder, original)

    # スペース除去
    t = re.sub(r'[\s　]+', '', t)
    return t.strip()


_TITLE_DASH_MAP = str.maketrans({
    "～": "〜", "‾": "〜", "∼": "〜", "〰": "〜",
    "－": "ー", "―": "ー", "─": "ー", "–": "ー", "—": "ー", "-": "ー",
})

_CIRCLED_EP = {
    "①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5, "⑥": 6, "⑦": 7, "⑧": 8, "⑨": 9, "⑩": 10,
    "⑪": 11, "⑫": 12, "⑬": 13, "⑭": 14, "⑮": 15, "⑯": 16, "⑰": 17, "⑱": 18, "⑲": 19, "⑳": 20,
}

# 複数話パッケージ（個別話数付きタイトルとは別SKU）
_PACKAGE_EDITIONS = frozenset({"tateyomi", "gappei", "tankobon", "comics"})

_EDITION_RULES = [
    (re.compile(r"【[^】]*タテヨミ[^】]*】"), "tateyomi"),
    (re.compile(r"【[^】]*合本[^】]*】"), "gappei"),
    (re.compile(r"【[^】]*単行本[^】]*】"), "tankobon"),
    (re.compile(r"【[^】]*コミックス版?[^】]*】"), "comics"),
    (re.compile(r"【[^】]*電子単行本[^】]*】"), "tankobon"),
    (re.compile(r"【[^】]*全年齢[^】]*】"), "allages"),
    (re.compile(r"[（(]\s*単話\s*[）)]"), "single"),
]


def _to_int_digits(num_str):
    num_str = "".join(chr(ord(c) - 0xFEE0) if "０" <= c <= "９" else c for c in str(num_str))
    try:
        return int(num_str)
    except ValueError:
        return None


def parse_title_parts(title):
    """
    タイトルを本体・話数・売り方ラベルに分解する（v21.5.13）。
    returns dict: base, episode, editions, is_package, is_single_sale
    """
    raw = str(title or "")
    t = raw.translate(_TITLE_DASH_MAP)
    t = t.replace("！", "!").replace("？", "?")
    t = re.sub(r"[○●＊*✕×]", "○", t)

    editions = set()
    for cre, label in _EDITION_RULES:
        if cre.search(t):
            editions.add(label)
            t = cre.sub("", t)
    # 括弧除去後に残る「単話」「単話版」
    if re.search(r"単話版?$", t) or "単話" in t:
        # タイトル末尾寄りのみ（作品名に単話が含まれる稀例は許容）
        if re.search(r"単話版?\s*$", t):
            editions.add("single")
            t = re.sub(r"単話版?\s*$", "", t)

    episode = None
    # 丸数字
    if t and t[-1] in _CIRCLED_EP:
        episode = _CIRCLED_EP[t[-1]]
        t = t[:-1]
    else:
        ep_res = [
            re.compile(r"[\[\(（【〈《「『]\s*([0-9０-９]+)\s*[話巻]?\s*[\]\)）】〉》」』]\s*$"),
            re.compile(r"第\s*([0-9０-９]+)\s*[話巻]\s*$"),
            # 記号区切りの末尾数字のみ（プロジェクション20 のような密着数字は話数にしない）
            re.compile(r"[〜ー・]\s*([0-9０-９]+)\s*[話巻]?\s*[!?\.…]*$"),
            re.compile(r"([0-9０-９]+)\s*[話巻]\s*$"),
        ]
        for cre in ep_res:
            m = cre.search(t)
            if m:
                episode = _to_int_digits(m.group(1))
                t = t[: m.start()]
                break

    # 残りの装飾括弧を除去
    t = re.sub(r"[\[\(（【〈《「『].*?[\]\)）】〉》」』]", "", t)
    t = re.sub(r"[\s　]+", "", t)
    base = re.sub(r"[^\w]", "", t)

    return {
        "base": base,
        "episode": episode,
        "editions": editions,
        "is_package": bool(editions & _PACKAGE_EDITIONS),
        "is_single_sale": "single" in editions,
    }


def title_core_for_fuzzy(title):
    """Fuzzy比較用の本体文字列（parse_title_parts の base）。"""
    return parse_title_parts(title)["base"]


def author_token_set(author=None, author_detail=None):
    """作者・詳細を正規化し、比較用トークン集合にする。"""
    text = " ".join([str(author or ""), str(author_detail or "")]).strip()
    if not text:
        return set()
    text = text.translate(_TITLE_DASH_MAP)
    text = re.sub(
        r"(著者|作者|イラスト|原作|作画|シナリオ|漫画|ネーム|サークル|出版社|レーベル)\s*[:：]?",
        " ",
        text,
    )
    parts = re.split(r"[,，/／、&＆・\|｜\s　]+", text)
    tokens = set()
    for p in parts:
        p = re.sub(r"[\s　]+", "", p)
        p = re.sub(r"[^\w]", "", p)
        if len(p) >= 2:
            tokens.add(p)
    return tokens


# === 声優(CV)名の共通パーサ (v21.6.0) ===
# cast_info / author_detail の声優欄を唯一のルールで分割・正規化する。
# WPタグ生成・DBバックフィル・fetcher保存のすべてがこの関数を通ること（表記ゆれ防止）。

_CAST_SPLIT_RE = re.compile(r"[,，、/／;；\|｜\n]+")
_CAST_NOISE_TOKENS = {"他", "ほか", "他数名", "その他", "未定", "非公開", "秘密", "？", "?", "-", "―", "なし"}
_CAST_PREFIX_RE = re.compile(r"^(?:CV|ＣＶ|声優|キャスト|出演)[.．:：\s]*", re.IGNORECASE)


def parse_cast_names(raw):
    """声優(CV)文字列を人名リストへ分割・正規化する。

    - 区切り: カンマ・読点・スラッシュ・セミコロン・パイプ・改行
      （「・」は人名内で使われるため区切りとして扱わない）
    - 各名前: NFKC正規化 → CV等の接頭辞除去 → 前後空白除去
    - 「他」「ほか」等のノイズトークンは破棄（不完全リストをタグ化しない）
    - 順序保持で重複除去
    """
    import unicodedata
    if not raw:
        return []
    text = unicodedata.normalize("NFKC", str(raw))
    names = []
    seen = set()
    for part in _CAST_SPLIT_RE.split(text):
        name = _CAST_PREFIX_RE.sub("", part.strip())
        name = re.sub(r"[\s　]+", " ", name).strip()
        # 括弧だけの注記 (例: "(仮)") や役名注記 "名前(役名)" の括弧部を除去
        name = re.sub(r"[（(][^（()）]*[)）]$", "", name).strip()
        # 末尾の「〇〇 他」「〇〇ほか」を除去（不完全リスト表記の掃除）
        name = re.sub(r"[\s　]+(?:他|ほか)$", "", name).strip()
        if not name or name in _CAST_NOISE_TOKENS:
            continue
        if len(name) > 25:  # 人名としてありえない長さは説明文等の混入とみなす
            continue
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def extract_cast_from_author_detail(author_detail):
    """author_detail（"役割:名前" カンマ区切り）から声優(CV)の人名リストを抽出する。

    らぶカル/DMM系は声優が cast_info でなく author_detail に
    「声優(CV):A/B/C」形式で入っているため、そこから回収する。
    値内にカンマが含まれる場合（"声優(CV):A,B"）も、後続の
    「役割:」を持たないパートを継続として取り込む。
    """
    if not author_detail:
        return []
    parts = str(author_detail).split(",")
    buf = []
    in_cast = False
    for p in parts:
        s = p.strip()
        if re.match(r"^声優\s*\(?(?:CV|ＣＶ)\)?\s*[:：]", s):
            in_cast = True
            buf.append(re.sub(r"^声優\s*\(?(?:CV|ＣＶ)\)?\s*[:：]", "", s))
        elif in_cast and ":" not in s and "：" not in s:
            buf.append(s)  # CV値の継続（カンマ区切りの複数人）
        else:
            in_cast = False
    return parse_cast_names(",".join(buf))


def base_digit_suffix_conflict(a, b):
    """一方が他方＋密着数字だけのとき（プロジェクション20 vs プロジェクション）は別作品。"""
    if not a or not b or a == b:
        return False
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    if longer.startswith(shorter):
        rest = longer[len(shorter) :]
        if rest and re.fullmatch(r"[0-9０-９]+", rest):
            return True
    return False


def super_normalize_title(title):
    """normalize_titleを適用後、さらに記号類をすべて排除した純粋な文字列を返す。"""
    t = normalize_title(title)
    return re.sub(r'[^\w]', '', t)


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
    conn.create_function("normalize_title", 1, normalize_title)
    conn.create_function("super_normalize_title", 1, super_normalize_title)
    return conn

def calculate_local_priority(title: str, desc: str, tags: str = "", original_tags: str = "", release_date_raw: str = "", is_exclusive: bool = False) -> int:
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
                score += 30   # 当日発売
            elif days_ago == 1:
                score += 25   # 昨日
            elif days_ago == 2:
                score += 20   # 2日前
            elif days_ago == 3:
                score += 15   # 3日前
            elif 4 <= days_ago <= 7:
                score += 10   # 4〜7日前
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
    power_words = ["溺愛", "ヤンデレ", "スパダリ", "オメガバース", "執着", "独占欲", "幼なじみ", "NTR", "身分差", "再会", "契約結婚", "一途", "初恋",
                   "ざまぁ", "悪役令嬢", "婚約破棄", "異世界", "健気", "身代わり"]  # v21.5.12: 冷遇削除（他タグと被りやすく付与が薄い）
    for pw in power_words:
        if pw in full_text:
            score += 2

    # 4. ノイズワード減点
    noise_words = ["セール", "まとめ買い", "大幅値下げ", "体験版", "値下げ", "半額", "期間限定"]
    for nw in noise_words:
        if nw in full_text:
            score -= 5
            
    # 5. 専売ボーナス (v14.7.0)
    # 最優先でAI審査に回すため、当日の新着(+50)を上回る+100の強力なボーナスを付与
    if is_exclusive:
        score += 100

    return score

def init_db():
    """v18.0.0: 統合DB (novelove_unified.db) に対して1回だけ実行。"""
    conn = db_connect(DB_FILE_UNIFIED)
    c = conn.cursor()
    # WALモードを先に設定（同時書き込み性能の確保）
    c.execute("PRAGMA journal_mode=WAL;")
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
        wp_post_id    INTEGER DEFAULT NULL,
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
        # === WP投稿ID（マイグレーション互換） ===
        ("wp_post_id",        "INTEGER DEFAULT NULL"),    # WP投稿記事ID
        # === HTML骨格パターン記録 (v16.0.0) ===
        ("article_pattern",   "TEXT DEFAULT ''"),      # 使用されたHTML骨格パターン (A/B/C/D/R)
        # === DB統合 (v18.0.0) ===
        ("source_db",         "TEXT DEFAULT ''"),      # DB所属: lovecal / dmm / dlsite
        # === 死に記事自動パージ・永久保護 (v18.6.0) ===
        ("is_protected",      "INTEGER DEFAULT 0"),    # 殿堂入り保護フラグ（1=永久保護、自動削除対象外）
        # === まとめ出演作品ID (v21.5.6) ===
        # curation 行に、そのまとめに出した通常作品の product_id をカンマ区切りで保存する。
        # 殿堂入り(is_protected)と分離し、まとめ再選定時の「未出演優先」判定に使う。
        ("curation_work_ids", "TEXT DEFAULT ''"),
    ]:
        try:
            c.execute(f"ALTER TABLE novelove_posts ADD COLUMN {col} {definition}")
        except Exception:
            pass
    # 頻繁に使われる組み合わせのインデックスを作成（v18.0.0）
    c.execute("CREATE INDEX IF NOT EXISTS idx_status_source ON novelove_posts (status, source_db);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_status_genre  ON novelove_posts (status, genre);")
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
