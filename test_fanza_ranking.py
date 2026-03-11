import os
from dotenv import load_dotenv

load_dotenv()

DMM_API_ID = os.environ.get("DMM_API_ID")
DMM_AFFILIATE_API_ID = os.environ.get("DMM_AFFILIATE_API_ID")
DMM_AFFILIATE_LINK_ID = os.environ.get("DMM_AFFILIATE_LINK_ID")

import requests
from bs4 import BeautifulSoup
import re
import json

def _is_noise_content(title, desc=""):
    ng_words = [
        "簡体中文", "繁体中文", "繁體中文", "English", "韓国語版", "中国語",
        "简体中文", "翻訳台詞", "中文字幕", "korean", "한국어"
    ]
    target_text = f"{title}_{desc}".lower()
    for word in ng_words:
        if word.lower() in target_text:
            return True
    return False

def _make_fanza_session():
    session = requests.Session()
    for domain in [".dmm.co.jp", ".book.dmm.co.jp", "book.dmm.co.jp", ".dmm.co.jp"]:
        session.cookies.set("age_check_done", "1", domain=domain)
        session.cookies.set("ckcy", "1", domain=domain)
    return session

def scrape_description(product_url, site="FANZA"):
    print(f"  Scraping: {product_url}")
    session = _make_fanza_session()
    try:
        r = session.get(product_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, "html.parser")
        
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
                    
        if has_format_tag and not is_comic:
            print(f"    [FANZA] マンガ以外の形式のため除外 (is_comic=False): {product_url}")
            return "__EXCLUDED_TYPE__"
            
        return "Dummy Description"
    except Exception as e:
        print(f"Error scraping {product_url}: {e}")
        return ""

def test_fetch_ranking_dmm_fanza():
    site = "FANZA"
    genre = "BL"
    params = {
        "api_id": DMM_API_ID,
        "affiliate_id": DMM_AFFILIATE_API_ID,
        "hits": 10,
        "sort": "rank",
        "output": "json",
        "site": "FANZA",
        "service": "ebook",
        "floor": "bl"
    }
    
    items = []
    r = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=params, timeout=15)
    data = r.json()
    raw_items = data.get("result", {}).get("items", [])
    
    print("=== DMM/FANZA Ranking Fetch Test ===")
    for item in raw_items:
        title = item.get("title", "")
        print(f"\nChecking candidate: {title}")
        
        if _is_noise_content(title, ""):
            print(f"  -> Skipped by title noise check.")
            continue
            
        desc = scrape_description(item.get("URL", ""), site=site)
        print(f"  -> Scraped desc length: {len(desc)} | value: {desc[:20]}")
        
        if _is_noise_content(title, desc):
            print(f"  -> Skipped by desc noise check.")
            continue
            
        items.append({"title": title, "desc": desc})
        
        if len(items) >= 5:
            break

    print("\n=== Final Extracted Items ===")
    for i, it in enumerate(items):
        print(f"{i+1}: {it['title'][:40]} (desc: {it['desc'][:20]})")

if __name__ == "__main__":
    test_fetch_ranking_dmm_fanza()
