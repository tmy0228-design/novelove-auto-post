import sqlite3
import requests
import time
import re
from bs4 import BeautifulSoup
from novelove_core import DB_FILE_UNIFIED, HEADERS, DMM_API_ID, DMM_AFFILIATE_API_ID

def get_db_conn():
    return sqlite3.connect(DB_FILE_UNIFIED)

# === DMM / FANZA / Lovecal バックフィル (API一括) ===
def backfill_dmm():
    conn = get_db_conn()
    c = conn.cursor()
    
    # 未取得のDMM系通常記事を取得
    c.execute("""
        SELECT product_id, site 
        FROM novelove_posts 
        WHERE status='published' 
          AND post_type='regular' 
          AND (site LIKE 'DMM.com%' OR site LIKE 'FANZA%' OR site LIKE 'Lovecal%')
          AND author_detail IS NULL
    """)
    rows = c.fetchall()
    
    if not rows:
        print("No pending DMM posts to backfill.")
        conn.close()
        return
        
    print(f"Found {len(rows)} DMM posts to backfill.")
    
    total = len(rows)
    for idx, (pid, site_val) in enumerate(rows, 1):
        print(f"[{idx}/{total}] Fetching DMM API: {pid}...")
        
        api_site = "FANZA"
        if site_val and "DMM.com" in site_val:
            api_site = "DMM.com"
            
        api_url = "https://api.dmm.com/affiliate/v3/ItemList"
        params = {
            "api_id": DMM_API_ID,
            "affiliate_id": DMM_AFFILIATE_API_ID,
            "site": api_site,
            "cid": pid,
            "output": "json"
        }
        
        try:
            r = requests.get(api_url, params=params, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                print(f"  API Error (status={r.status_code}) for {pid}")
                # 一時的なエラーの場合もあるため、DBは更新せずにスキップ
                continue
            
            data = r.json()
            items = data.get("result", {}).get("items", [])
            
            if not items:
                # 見つからない場合は空で更新して次回スキップ
                c.execute("""
                    UPDATE novelove_posts 
                    SET author_detail='', cast_info='', series_name='', page_count=0 
                    WHERE product_id=?
                """, (pid,))
                conn.commit()
                continue
                
            item = items[0]
            iteminfo = item.get("iteminfo", {}) or {}
            
            # 作者
            authors = []
            for field in ["author", "writer", "artist"]:
                vals = iteminfo.get(field, []) or []
                for v in vals:
                    name = v.get("name", "") if isinstance(v, dict) else str(v)
                    if name and name not in authors:
                        authors.append(name)
            author_str = ",".join(authors) if authors else ""
            
            # 声優
            actresses = iteminfo.get("actress", []) or []
            casts = [act.get("name", "") for act in actresses if act.get("name")]
            cast_str = ",".join(casts) if casts else ""
            
            # シリーズ
            series_list = iteminfo.get("series", []) or []
            series_name = series_list[0].get("name", "") if series_list else ""
            
            # ページ数
            volume = item.get("volume", "")
            page_count = 0
            if volume:
                m = re.search(r"(\d+)", str(volume))
                if m:
                    page_count = int(m.group(1))
            
            # DB更新
            c.execute("""
                UPDATE novelove_posts 
                SET author_detail=?, cast_info=?, series_name=?, page_count=? 
                WHERE product_id=?
            """, (author_str, cast_str, series_name, page_count, pid))
            conn.commit()
            
            time.sleep(0.15)  # 負荷軽減のための安全ウェイト
            
        except Exception as e:
            print(f"  Error processing {pid}: {e}")
            
    conn.close()
    print("DMM backfill complete.")

# === DLsite バックフィル (個別スクレイピング) ===
def backfill_dlsite():
    conn = get_db_conn()
    c = conn.cursor()
    
    # 未取得のDLsite通常記事を取得
    c.execute("""
        SELECT product_id, product_url 
        FROM novelove_posts 
        WHERE status='published' 
          AND post_type='regular' 
          AND site LIKE 'DLsite%'
          AND author_detail IS NULL
    """)
    rows = c.fetchall()
    
    if not rows:
        print("No pending DLsite posts to backfill.")
        conn.close()
        return
        
    print(f"Found {len(rows)} DLsite posts to backfill.")
    
    total = len(rows)
    for idx, (pid, url) in enumerate(rows, 1):
        print(f"[{idx}/{total}] Scraping DLsite: {pid} ({url})...")
        
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                print(f"  Failed to fetch page: status={r.status_code}")
                # 取得失敗時は空を設定して次回スキップするようにする（エラーの無限ループ防止）
                c.execute("""
                    UPDATE novelove_posts 
                    SET author_detail='', cast_info='', series_name='', page_count=0 
                    WHERE product_id=?
                """, (pid,))
                conn.commit()
                continue
                
            soup = BeautifulSoup(r.text, "html.parser")
            
            author_detail = ""
            cast_info = ""
            series_name = ""
            page_count = 0
            
            # #work_outline テーブルから情報をパース
            outline_table = soup.select_one("#work_outline")
            if outline_table:
                # 著者、イラスト、シナリオなどを集約
                authors = []
                for tr in outline_table.select("tr"):
                    th = tr.select_one("th")
                    td = tr.select_one("td")
                    if not th or not td:
                        continue
                        
                    th_text = th.get_text(strip=True)
                    td_text = td.get_text(strip=True)
                    
                    if "著者" in th_text or "シナリオ" in th_text or "イラスト" in th_text or "原画" in th_text:
                        # リンクテキストがあれば抽出
                        links = [a.get_text(strip=True) for a in td.find_all("a") if a.get_text(strip=True)]
                        val = ",".join(links) if links else td_text
                        authors.append(f"{th_text}:{val}")
                        
                    elif "声優" in th_text or "キャスト" in th_text:
                        links = [a.get_text(strip=True) for a in td.find_all("a") if a.get_text(strip=True)]
                        cast_info = ",".join(links) if links else td_text
                        
                    elif "シリーズ名" in th_text:
                        links = [a.get_text(strip=True) for a in td.find_all("a") if a.get_text(strip=True)]
                        series_name = ",".join(links) if links else td_text
                        
                    elif "ページ数" in th_text:
                        m = re.search(r"(\d+)", td_text)
                        if m:
                            page_count = int(m.group(1))
                            
                author_detail = ",".join(authors)
                
            # DB更新
            c.execute("""
                UPDATE novelove_posts 
                SET author_detail=?, cast_info=?, series_name=?, page_count=? 
                WHERE product_id=?
            """, (author_detail, cast_info, series_name, page_count, pid))
            conn.commit()
            
            # 負荷軽減
            time.sleep(1.5)
            
        except Exception as e:
            print(f"  Error scraping {pid}: {e}")
            
    conn.close()
    print("DLsite backfill complete.")

if __name__ == "__main__":
    backfill_dmm()
    backfill_dlsite()
