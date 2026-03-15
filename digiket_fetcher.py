#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Novelove DigiKet Fetcher Module
独立して DigiKet の商品を RSS から検知し、詳細ページから補完するモジュール
"""

import requests
import sqlite3
import os
import re
import logging
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv

# --- 設定 ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()

DIGIKET_AFFILIATE_ID = os.environ.get("DIGIKET_AFFILIATE_ID", "novelove")
DB_FILE_DIGIKET = os.path.join(SCRIPT_DIR, "novelove_digiket.db")
LOG_FILE = os.path.join(SCRIPT_DIR, "novelove.log")

# ロガー設定 (既存の novelove ロガーに便乗するか独立させる)
logger = logging.getLogger("novelove.digiket")
logger.setLevel(logging.INFO)

# --- 取得対象設定 ---
# target=8: 商業BL/TL新着, target=2: 女性向同人新着
DIGIKET_TARGETS = [
    {"target": "8", "genre": "comic_bl",  "label": "DigiKet_商業BL"},
    {"target": "8", "genre": "comic_tl",  "label": "DigiKet_商業TL"},
    {"target": "2", "genre": "doujin_bl", "label": "DigiKet_同人BL"},
    {"target": "2", "genre": "doujin_tl", "label": "DigiKet_同人TL"},
]

def init_digiket_db():
    conn = sqlite3.connect(DB_FILE_DIGIKET)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS novelove_posts (
        product_id TEXT PRIMARY KEY,
        title TEXT,
        author TEXT DEFAULT '',
        genre TEXT,
        site TEXT DEFAULT 'DigiKet',
        status TEXT DEFAULT 'watching',
        release_date TEXT DEFAULT '',
        description TEXT DEFAULT '',
        affiliate_url TEXT DEFAULT '',
        image_url TEXT DEFAULT '',
        product_url TEXT DEFAULT '',
        wp_post_url TEXT DEFAULT '',
        retry_count INTEGER DEFAULT 0,
        last_error TEXT DEFAULT '',
        desc_score INTEGER DEFAULT 0,
        inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        published_at TIMESTAMP,
        post_type TEXT DEFAULT 'regular'
    )''')
    conn.commit()
    conn.close()

def scrape_digiket_description(product_url):
    """
    DigiKet の商品詳細ページから「作品内容」の全文を抽出する
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(product_url, headers=headers, timeout=20)
        if r.status_code != 200: return ""
        
        soup = BeautifulSoup(r.text, "html.parser")
        
        # 1. 見出しやテキストから領域を特定
        desc_area = None
        
        # '作品説明' または '作品内容' というテキストを直接含む要素を探す
        for text_label in ["作品説明", "作品内容", "作品詳細"]:
            label_tag = soup.find(["h4", "h3", "th", "div", "span"], string=re.compile(text_label))
            if label_tag:
                # 見出しの隣または親の親などにある本文を探す
                # パターンA: 見出しの次の要素
                desc_area = label_tag.find_next_sibling(["div", "p", "td"])
                if desc_area: break
                # パターンB: 親要素の中にある別の要素
                parent = label_tag.parent
                if parent:
                    desc_area = parent.find_next(["div", "p", "td"], class_=re.compile(r"description|explanation|body"))
                    if desc_area: break
        
        if not desc_area:
            # 2. セレクタでのフォールバック
            selectors = [
                ".work_explanation_body", 
                ".works-description", 
                "#work_explanation",
                ".main_explanation",
                ".description_area"
            ]
            for sel in selectors:
                desc_area = soup.select_one(sel)
                if desc_area: break
            
        if desc_area:
            # 「続きを読む」リンクや宣伝バナーなどを除外
            for trash in desc_area.select('.readmore, script, style, .work_review_btn'):
                trash.decompose()
            text = desc_area.get_text(separator="\n", strip=True)
            
            # 不要なヘッダー（「作品説明」など）が混入している場合は削る
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
    init_digiket_db()
    
    conn = sqlite3.connect(DB_FILE_DIGIKET)
    c = conn.cursor()
    
    for target_cfg in DIGIKET_TARGETS:
        target_id = target_cfg["target"]
        genre = target_cfg["genre"]
        label = target_cfg["label"]
        
        # API URL (アフィリエイトIDを付与)
        api_url = f"https://api.digiket.com/xml/api/getxml.php?target={target_id}&sort=new"
        if DIGIKET_AFFILIATE_ID:
            api_url += f"&affiliate_id={DIGIKET_AFFILIATE_ID}"
            
        try:
            r = requests.get(api_url, timeout=20)
            soup = BeautifulSoup(r.text, "html.parser") # html.parser で代替 (lxml/xml 不在対策)
            items = soup.find_all("item")
            
            new_count = 0
            for item in items:
                title = item.find("title").text if item.find("title") else ""
                product_url = item.find("link").text if item.find("link") else ""
                
                # ジャンルフィルタ (キーワード判定からカテゴリ信頼へ緩和)
                # DigiKetのtarget=8, target=2 は既にカテゴリ分けされているため、原則全件取得
                is_match = True
                
                # if not is_match: continue
                
                # ID抽出 (ID=ITMXXXXXXX)
                m = re.search(r"ID=(ITM\d+)", product_url)
                if not m: continue
                pid = m.group(1)
                
                # 重複チェック
                c.execute("SELECT 1 FROM novelove_posts WHERE product_id=?", (pid,))
                if c.fetchone(): continue
                
                author = item.find("dc:creator").text if item.find("dc:creator") else ""
                date_str = item.find("dc:date").text if item.find("dc:date") else datetime.now().strftime("%Y-%m-%d")
                
                # 画像URLの抽出 (content:encoded 内に <img> が含まれる)
                content_encoded = item.find("content:encoded").text if item.find("content:encoded") else ""
                img_match = re.search(r'src="(https://.*?\.jpg)"', content_encoded)
                image_url = img_match.group(1) if img_match else ""
                
                # あらすじ取得 (RSS版)
                description = item.find("description").text if item.find("description") else ""
                
                # アフィリエイトURL (APIから返ってくるlinkにIDが含まれているはずだが、念のため再構築)
                affiliate_url = product_url
                if DIGIKET_AFFILIATE_ID:
                    # DigiKetのアフィリエイトURL形式: /AFID=xxxxx/
                    if not affiliate_url.endswith("/"):
                        affiliate_url += "/"
                    affiliate_url += f"AFID={DIGIKET_AFFILIATE_ID}/"
                
                c.execute("""INSERT INTO novelove_posts 
                    (product_id, title, author, genre, site, status, release_date, description, affiliate_url, image_url, product_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (pid, title, author, genre, "DigiKet", "watching", date_str, description, affiliate_url, image_url, product_url))
                
                new_count += 1
                
            conn.commit()
            logger.info(f"  -> {label}: {new_count}件 の新規作品をストックしました")
            
        except Exception as e:
            logger.error(f"DigiKet取得エラー ({label}): {e}")
            
    conn.close()

if __name__ == "__main__":
    # 単体実行テスト用
    logging.basicConfig(level=logging.INFO)
    fetch_digiket_items()
