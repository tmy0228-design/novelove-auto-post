import sqlite3, time, sys, argparse
from novelove_fetcher import scrape_description
from novelove_core import DB_FILE_FANZA, db_connect, notify_discord, logger
from nexus_rewrite import run_rewrite

def get_targets(limit=None):
    conn = sqlite3.connect(DB_FILE_FANZA)
    c = conn.cursor()
    c.execute("SELECT product_id, title, site, product_url, LENGTH(description) FROM novelove_posts WHERE status='published' AND LENGTH(description) < 150 AND (description LIKE '%…%' OR description LIKE '%...%') ORDER BY published_at DESC")
    rows = c.fetchall()
    conn.close()
    return rows[:limit] if limit else rows

def re_fetch_and_save(pid, site, url, old_len):
    site_key = "Lovecal" if "Lovecal" in site else ("DLsite" if "DLsite" in site else ("DigiKet" if "DigiKet" in site else "FANZA"))
    new_desc = scrape_description(url, site=site_key, genre="")
    if not new_desc or new_desc == "__EXCLUDED_TYPE__" or len(new_desc) <= old_len + 10:
        return None
    conn = db_connect(DB_FILE_FANZA)
    conn.execute("UPDATE novelove_posts SET description=?, is_desc_updated=1 WHERE product_id=?", (new_desc, pid))
    conn.commit(); conn.close()
    return len(new_desc)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if not args.test and not args.all:
        print("--test or --all"); sys.exit(1)
    targets = get_targets(3 if args.test else None)
    ok=0; skip=0; fail=0
    for i,(pid,title,site,url,old_len) in enumerate(targets):
        logger.info(f"[{i+1}/{len(targets)}] {title[:25]}...")
        new_len = re_fetch_and_save(pid, site, url, old_len)
        if not new_len:
            logger.warning(f"  skip (no improvement)"); skip+=1; continue
        logger.info(f"  desc: {old_len} -> {new_len} chars")
        if run_rewrite(product_id=pid, execute=True):
            ok+=1
        else:
            fail+=1
        time.sleep(3)
    logger.info(f"DONE: ok={ok} skip={skip} fail={fail}")
    if args.all:
        notify_discord(f"一括修復完了: 成功{ok}件 スキップ{skip}件 失敗{fail}件", username="Nexus修復")

if __name__ == "__main__":
    main()
