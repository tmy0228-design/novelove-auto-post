import requests
import json
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

DMM_API_ID = os.environ.get("DMM_API_ID")
DMM_AFFILIATE_LINK_ID = os.environ.get("DMM_AFFILIATE_LINK_ID")
DMM_URL = "https://api.dmm.com/affiliate/v3/ItemList"

def test_dmm_fanza():
    print("--- Testing FANZA/DMM API (sort=rank) ---")
    params = {
        "api_id": DMM_API_ID,
        "affiliate_id": "novelove-990", # valid format
        "site": "FANZA",
        "service": "doujin",
        "floor": "digital_doujin",
        "hits": 5,
        "sort": "rank",
        "output": "json"
    }
    r = requests.get(DMM_URL, params=params)
    if r.status_code == 200:
        data = r.json()
        items = data.get("result", {}).get("items", [])
        print(f"FANZA doujin/digital_doujin rank found: {len(items)}")
        for idx, item in enumerate(items):
            print(f" {idx+1}. {item.get('title')}")
    else:
        print(f"Error fetching FANZA: {r.status_code}")
        print(r.text)

def scrape_dlsite_ranking(url_path):
    print(f"\n--- Testing DLsite Scraping ({url_path}) ---")
    url = f"https://www.dlsite.com/{url_path}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            items = soup.select('table#ranking_table .work_name a')
            images = soup.select('table#ranking_table .work_thumb img')
            
            print(f"Found {len(items)} items.")
            
            for idx in range(min(5, len(items))):
                title = items[idx].text.strip()
                link = items[idx].get('href')
                
                # Fetch detail page to get og:image
                img_src = ""
                try:
                    dr = requests.get(link, headers=headers)
                    dsoup = BeautifulSoup(dr.text, 'html.parser')
                    og_img = dsoup.select_one('meta[property="og:image"]')
                    if og_img:
                        img_src = og_img.get('content', '')
                except:
                    pass
                
                print(f"{idx+1}位:\n タイトル: {title}\n リンク: {link}\n 画像: {img_src}")
        else:
            print(f"Error fetching DLsite: {r.status_code}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    scrape_dlsite_ranking("girls/ranking/day")
    scrape_dlsite_ranking("bl/ranking/day")
