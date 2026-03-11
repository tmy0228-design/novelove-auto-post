import requests
from bs4 import BeautifulSoup
import json
import os

def _make_fanza_session():
    session = requests.Session()
    # 年齢確認Cookie
    session.cookies.set("age_check_done", "1", domain=".dmm.co.jp", path="/")
    session.cookies.set("age_check_done", "1", domain=".dmm.com", path="/")
    # 既存のロジックに合わせる
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }
    session.headers.update(headers)
    return session

def test_scrape(url):
    print(f"\n--- Testing URL: {url} ---")
    session = _make_fanza_session()
    try:
        r = session.get(url, timeout=20)
        r.encoding = r.apparent_encoding
        
        if r.status_code != 200:
            print(f"Status Code: {r.status_code}")
            return

        soup = BeautifulSoup(r.text, "html.parser")
        
        # デバッグ: タイトル確認
        page_title = soup.find("title")
        print(f"Page Title: {page_title.text.strip() if page_title else 'N/A'}")

        results = {}
        
        # 1. __NEXT_DATA__
        next_tag = soup.find("script", id="__NEXT_DATA__")
        if next_tag:
            try:
                ndata = json.loads(next_tag.string)
                desc = ndata.get("props", {}).get("pageProps", {}).get("product", {}).get("description", "")
                if not desc:
                    # Alternative path in JSON
                    desc = ndata.get("props", {}).get("pageProps", {}).get("data", {}).get("description", "")
                if desc:
                    results["__NEXT_DATA__"] = desc[:100] + "..."
            except: pass
            
        # 2. .summary__txt (Doujin / Voice)
        summary_txt = soup.select_one(".summary__txt")
        if summary_txt:
            results[".summary__txt"] = summary_txt.text.strip()[:100] + "..."
            
        # 3. .mg-b20 (Mono / PC Game)
        mg_b20 = soup.select_one(".mg-b20")
        if mg_b20:
            results[".mg-b20"] = mg_b20.text.strip()[:100] + "..."

        # 4. .product-description__text (Books fallback?)
        p_desc = soup.select_one(".product-description__text")
        if p_desc:
            results[".product-description__text"] = p_desc.text.strip()[:100] + "..."
            
        # 5. og:description
        og = soup.find("meta", property="og:description")
        if og:
            results["og:description"] = og.get("content", "")[:100] + "..."
            
        if results:
            for k, v in results.items():
                print(f"[{k}]: {v}")
        else:
            print("No description found. Soup snippet:")
            # 構造確認のために少しだけ出力
            main_content = soup.find("div", id="main-content") or soup.find("div", class_="main")
            if main_content:
                 print(main_content.text.strip()[:300])
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    urls = [
        "https://www.dmm.co.jp/dc/doujin/-/detail/=/cid=d_733738/",  # 同人
        "https://www.dmm.co.jp/mono/pcgame/-/detail/=/cid=d_123456/", # PCゲーム (ダミーID、構造確認)
        "https://book.dmm.co.jp/product/6250057/s645asmmi00995/",     # 電子書籍 (既存)
    ]
    for url in urls:
        test_scrape(url)
