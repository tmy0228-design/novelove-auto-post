#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
Novelove 既存記事スペック情報 遡及的修復スクリプト (v2.0.0)
【パターン2：DB取得とWP同期の完全分離設計】
==========================================================
"""
import os
import sys
import time
import random
import sqlite3
import subprocess
import re
import json
import logging
import requests
from bs4 import BeautifulSoup

# パス追加
sys.path.insert(0, '/home/kusanagi/scripts')

from novelove_core import (
    DB_FILE_UNIFIED, WP_CLI_PATH, WP_DOC_ROOT,
    DMM_API_ID, DMM_AFFILIATE_API_ID
)
from novelove_fetcher import (
    scrape_dlsite_description, scrape_description, format_author_detail,
    _make_dmm_session
)

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('/home/kusanagi/scripts/repair_progress.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("repair")

def get_db_conn():
    return sqlite3.connect(DB_FILE_UNIFIED)

def fetch_dmm_api_meta(cid):
    """DMM APIから商業作品のメタデータを取得"""
    url = "https://api.dmm.com/affiliate/v3/ItemList"
    params = {
        "api_id": DMM_API_ID,
        "affiliate_id": DMM_AFFILIATE_API_ID,
        "site": "DMM.com",
        "cid": cid,
        "output": "json"
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        result = data.get("result", {})
        items = result.get("items", [])
        if items:
            return items[0].get("iteminfo", {})
    except Exception as e:
        logger.warning(f"DMM API 取得失敗 ({cid}): {e}")
    return {}

def build_specs_html(release_date, author_detail, cast_info, page_count, fallback_author=None, is_dlsite=False):
    specs = []
    if release_date and isinstance(release_date, str) and len(release_date) >= 4:
        formatted_date = release_date[:10].replace("-", "/")
        specs.append(f"発売日: {formatted_date}")
        
    def clean_txt(t):
        if not t: return ""
        return t.replace("\r", "").replace("\n", "").replace("\xa0", " ").strip()

    if author_detail:
        author_detail = clean_txt(author_detail)
        author_detail = format_author_detail(author_detail)
        if ":" in author_detail:
            parts = author_detail.split(",")
            role_to_names = {}
            for part in parts:
                if ":" in part:
                    r, n = part.split(":", 1)
                    r = r.strip()
                    n = n.strip()
                    if not n:
                        continue
                    if ":" in n:
                        r2, n2 = n.split(":", 1)
                        r = r2.strip()
                        n = n2.strip()
                    
                    if r not in role_to_names:
                        role_to_names[r] = []
                    if n not in role_to_names[r]:
                        role_to_names[r].append(n)
            
            for r, names in role_to_names.items():
                names_str = " / ".join(names)
                specs.append(f"{r}: {names_str}")
        else:
            specs.append(f"著者: {author_detail}")
    elif fallback_author:
        fallback_author = clean_txt(fallback_author)
        if is_dlsite and "/" in fallback_author:
            sub_parts = [p.strip() for p in fallback_author.split("/") if p.strip()]
            if len(sub_parts) >= 2:
                specs.append(f"レーベル: {sub_parts[0]}")
                specs.append(f"著者: {sub_parts[1]}")
            else:
                specs.append(f"サークル: {fallback_author}")
        else:
            specs.append(f"サークル: {fallback_author}" if is_dlsite else f"著者: {fallback_author}")
            
    if cast_info:
        specs.append(f"声優(CV): {cast_info}")
    if page_count:
        try:
            pg_val = int(page_count)
            if pg_val > 0:
                specs.append(f"{pg_val}P")
        except:
            pass
            
    if not specs:
        return ""
    specs_text = " ｜ ".join(specs)
    
    html = f"""<!-- NOVELOVE_SPECS_START -->
<div class="novelove-specs" style="background:#fafafa; border-top:1px solid #eee; border-bottom:1px solid #eee; padding:6px 10px; margin:12px 0; font-size:0.85em; color:#666; text-align:center; line-height:1.5;">
  {specs_text}
</div>
<!-- NOVELOVE_SPECS_END -->\n"""
    return html

def update_wp_post_content(wp_id, spec_html):
    """WP-CLIを使用して本番WordPress上の投稿を更新"""
    try:
        # 現在の本文を取得
        cmd_get = [WP_CLI_PATH, "--path=" + WP_DOC_ROOT, "post", "get", str(wp_id), "--field=post_content"]
        res = subprocess.run(cmd_get, capture_output=True, text=True, check=True)
        content = res.stdout
        
        # 古いスペック表を置換、なければ挿入
        start_marker = "<!-- NOVELOVE_SPECS_START -->"
        end_marker = "<!-- NOVELOVE_SPECS_END -->"
        
        if start_marker in content and end_marker in content:
            pattern = re.compile(r"<!-- NOVELOVE_SPECS_START -->.*?<!-- NOVELOVE_SPECS_END -->\s*", re.DOTALL)
            new_content = pattern.sub(spec_html, content)
        else:
            if "<h2>" in content:
                new_content = content.replace("<h2>", spec_html + "<h2>", 1)
            else:
                new_content = spec_html + content
                
        # 更新実行
        cmd_update = [WP_CLI_PATH, "--path=" + WP_DOC_ROOT, "post", "update", str(wp_id), "-"]
        subprocess.run(cmd_update, input=new_content, capture_output=True, text=True, check=True)
        return True
    except Exception as e:
        logger.error(f"WP更新失敗 (WP_ID: {wp_id}): {e}")
    return False

def check_is_insufficient(pid, raw_auth, site, genre, p_url):
    """メタデータが不足しているかどうかを厳格に判定"""
    pid_lower = str(pid).lower()
    
    is_dlsite = "dlsite" in site.lower() or "dlsite" in p_url.lower()
    is_lovecal = "lovecul.dmm.co.jp" in p_url.lower() or "lovecal" in site.lower()
    is_dmm_com = not is_dlsite and not is_lovecal
    
    details = {}
    if raw_auth:
        for part in raw_auth.split(","):
            if ":" in part:
                parts = part.split(":", 1)
                details[parts[0].strip()] = parts[1].strip()
                
    # 1. DLsite商業 (BJ)
    if is_dlsite and "bj" in pid_lower:
        if "出版社" not in details or "レーベル" not in details:
            return True
            
    # 2. DLsite同人 (RJ)
    elif is_dlsite and "rj" in pid_lower:
        is_voice = "voice" in genre.lower() or "sou" in genre.lower()
        if is_voice:
            if "声優(CV)" not in details or "シナリオ" not in details:
                return True
        else:
            if len(details) <= 1 and "サークル" in details:
                return True
                
    # 3. らぶカル同人
    elif is_lovecal:
        if len(details) <= 1 and "サークル" in details:
            return True
            
    # 4. DMM商業
    elif is_dmm_com:
        if "出版社" not in details or "レーベル" not in details:
            return True
            
    return False

def fetch_and_update_db_only(row, dry_run=False):
    """【フェーズ1】作品詳細を外部から取得し、DBのみを更新（WP更新は行わない）"""
    pid = row["product_id"]
    title = row["title"]
    site = row["site"] or ""
    genre = row["genre"] or ""
    p_url = row["product_url"] or ""
    
    is_dlsite = "dlsite" in site.lower() or "dlsite" in p_url.lower()
    is_lovecal = "lovecul.dmm.co.jp" in p_url.lower() or "lovecal" in site.lower()
    is_dmm_com = not is_dlsite and not is_lovecal
    
    new_auth = ""
    new_cast = ""
    new_series = ""
    new_pages = 0
    
    logger.info(f"Re-fetching data for [{pid}] {title[:30]}...")
    
    if is_dlsite:
        desc, dl_tags, dl_excl, dl_auth, dl_cast, dl_series, dl_pages = scrape_dlsite_description(p_url)
        new_auth = dl_auth
        new_cast = dl_cast
        new_series = dl_series
        new_pages = dl_pages
    elif is_lovecal:
        desc, love_auth = scrape_description(p_url, site="DMM.com", genre=genre)
        new_auth = love_auth
    elif is_dmm_com:
        iteminfo = fetch_dmm_api_meta(pid)
        authors = []
        seen = set()
        
        for field in ["author", "writer", "artist"]:
            vals = iteminfo.get(field, []) or []
            for v in vals:
                name = v.get("name", "") if isinstance(v, dict) else str(v)
                role = {"author": "著者", "writer": "著者", "artist": "イラスト"}.get(field, "著者")
                pair = f"{role}:{name}"
                if name and pair not in seen:
                    authors.append(pair)
                    seen.add(pair)
                    
        makers = iteminfo.get("maker", []) or []
        for m in makers:
            name = m.get("name", "") if isinstance(m, dict) else str(m)
            pair = f"出版社:{name}"
            if name and pair not in seen:
                authors.append(pair)
                seen.add(pair)
                
        labels = iteminfo.get("label", []) or []
        for l in labels:
            name = l.get("name", "") if isinstance(l, dict) else str(l)
            pair = f"レーベル:{name}"
            if name and pair not in seen:
                authors.append(pair)
                seen.add(pair)
                
        new_auth = ",".join(authors)
        
        actresses = iteminfo.get("actress", []) or []
        cast_list = []
        for a in actresses:
            name = a.get("name", "") if isinstance(a, dict) else str(a)
            if name and name not in cast_list:
                cast_list.append(name)
        new_cast = ",".join(cast_list)
        
    if not new_auth:
        logger.warning(f"再取得データが空です: [{pid}]")
        return False
        
    logger.info(f"  [新スペック] {new_auth}")
    
    if dry_run:
        logger.info(f"  [Dry Run] DBアップデートをスキップします。")
        return True
        
    # DBアップデートのみ
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE novelove_posts 
        SET author_detail=?, cast_info=COALESCE(NULLIF(?, ''), cast_info), 
            series_name=COALESCE(NULLIF(?, ''), series_name), 
            page_count=CASE WHEN ? > 0 THEN ? ELSE page_count END
        WHERE product_id=?
    """, (new_auth, new_cast, new_series, new_pages, new_pages, pid))
    conn.commit()
    conn.close()
    logger.info("  [DB] アップデート完了")
    return True

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, required=True, choices=["fetch-db", "update-wp"], 
                        help="実行モード: 'fetch-db' (DB再取得) または 'update-wp' (本番WPへの反映)")
    parser.add_argument("--dry-run", action="store_true", help="DBやWPを書き換えずにシミュレーション実行")
    parser.add_argument("--limit", type=int, default=10, help="処理する件数の上限")
    parser.add_argument("--pid", type=str, help="特定の作品IDのみをピンポイントで処理")
    args = parser.parse_args()
    
    logger.info(f"=== 遡及的スペック修復バッチ開始 Mode: {args.mode} (Limit: {args.limit}, DryRun: {args.dry_run}) ===")
    
    conn = get_db_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    if args.pid:
        c.execute("SELECT * FROM novelove_posts WHERE product_id=?", (args.pid,))
        rows = c.fetchall()
    else:
        if args.mode == "fetch-db":
            # 対象：公開済み（published）または投稿待ち（watching, pending）の全通常記事
            c.execute("""
                SELECT * FROM novelove_posts 
                WHERE (status='published' OR status='watching' OR status='pending') 
                  AND post_type='regular'
            """)
        else:  # update-wp
            # 対象：WordPressに既に投稿されている通常記事（wp_post_idが存在するもの）
            c.execute("""
                SELECT * FROM novelove_posts 
                WHERE status='published' 
                  AND post_type='regular'
                  AND wp_post_id IS NOT NULL AND wp_post_id != ''
            """)
        all_rows = c.fetchall()
        
        # ランキング等を除外
        rows = []
        for r in all_rows:
            pid = str(r["product_id"]).lower()
            title = str(r["title"]).lower()
            if "ranking" in pid or "pickup" in pid or "w1" in pid or "w2" in pid or "w3" in pid or "w4" in pid or "w5" in pid or "ランキング" in title or "ピックアップ" in title:
                continue
            
            if args.mode == "fetch-db":
                # 不足分だけでなく、対象の通常記事すべてを無条件で再取得する
                rows.append(r)
            else:
                # WP一括反映時も同様に対象をすべて更新
                rows.append(r)
                
    conn.close()
    
    total = len(rows)
    logger.info(f"処理対象の全通常作品数: {total} 件")
    
    if total == 0:
        logger.info("対象となる作品はありません。")
        return
        
    processed = 0
    success = 0
    
    for row in rows[:args.limit]:
        processed += 1
        pid = row["product_id"]
        wp_id = row["wp_post_id"]
        site = row["site"] or ""
        p_url = row["product_url"] or ""
        auth_det = row["author_detail"] or ""
        cast = row["cast_info"] or ""
        pages = row["page_count"] or 0
        rel_date = row["release_date"]
        
        logger.info(f"\n--- Progress: [{processed}/{min(total, args.limit)}] PID: {pid} ---")
        
        if args.mode == "fetch-db":
            # 【フェーズ1: DB取得】
            ok = fetch_and_update_db_only(row, dry_run=args.dry_run)
            if ok:
                success += 1
            # 外部アクセス負荷軽減のためにランダムスリープ
            time.sleep(random.uniform(1.5, 2.5))
        else:
            # 【フェーズ2: WP反映】
            if wp_id:
                is_dlsite = "dlsite" in site.lower() or "dlsite" in p_url.lower()
                spec_html = build_specs_html(rel_date, auth_det, cast, pages, fallback_author=row["author"], is_dlsite=is_dlsite)
                if spec_html:
                    if args.dry_run:
                        logger.info(f"    [Dry Run] WP ID {wp_id} のスペック表示アップデートをシミュレート")
                        logger.info(f"    [擬似HTML] {spec_html.strip()}")
                        success += 1
                    else:
                        ok = update_wp_post_content(wp_id, spec_html)
                        if ok:
                            logger.info(f"    [WP] 記事ID {wp_id} のスペック表表示を整形更新しました")
                            success += 1
                        else:
                            logger.warning(f"    [WP] 記事ID {wp_id} の更新に失敗しました")
                else:
                    success += 1
            else:
                success += 1
            # 外部サイトへのアクセスがないため、スリープは最小限
            time.sleep(0.05)
        
    logger.info(f"\n=== バッチ完了 (成功: {success}/{processed}) ===")

if __name__ == "__main__":
    main()
