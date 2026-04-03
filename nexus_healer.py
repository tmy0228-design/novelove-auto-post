import os
import requests
import sqlite3
import re
import argparse
from dotenv import load_dotenv

load_dotenv()

# novelove_core から必要な設定をインポート
from novelove_core import (
    DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET,
    db_connect,
)

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("NexusHealer")

WP_SITE_URL = os.environ.get("WP_SITE_URL", "https://novelove.jp")
WP_USER = os.environ.get("WP_USER", "novelove-admin")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")

def get_purged_urls():
    """DBから「purgeされて除外済」となった記事の wp_post_url を抽出する"""
    purged = []
    for db_path in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
        if not os.path.exists(db_path): continue
        conn = db_connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # last_error に purged_low_score が入っている記事を探す
        rows = c.execute("SELECT product_id, title, wp_post_url, last_error FROM novelove_posts WHERE status='excluded' AND last_error LIKE 'purged_low_score%'").fetchall()
        for r in rows:
            if r["wp_post_url"]:
                purged.append(r)
        conn.close()
    return purged

def get_new_internal_link(genre, current_pid):
    """オートヒーリングのための新しい関連記事リンクをランダムに取得する"""
    dbs = {
        "novel_bl": DB_FILE_FANZA, "novel_tl": DB_FILE_FANZA, "comic_bl": DB_FILE_FANZA, "comic_tl": DB_FILE_FANZA,
        "doujin_bl": DB_FILE_DLSITE, "doujin_tl": DB_FILE_DLSITE,
        "voice_bl": DB_FILE_DLSITE, "voice_tl": DB_FILE_DLSITE
    }
    db_target = dbs.get(genre, DB_FILE_FANZA)
    if not os.path.exists(db_target):
        return None
        
    conn = db_connect(db_target)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 既存の公開済み＆高スコアの中からランダムに1件
    row = c.execute(
        "SELECT title, wp_post_url FROM novelove_posts WHERE status='published' AND product_id != ? AND genre = ? AND desc_score >= 4 ORDER BY RANDOM() LIMIT 1",
        (current_pid, genre)
    ).fetchone()
    
    conn.close()
    
    if row and row["wp_post_url"]:
        return {"title": row["title"], "url": row["wp_post_url"]}
    return None

def heal_wordpress_post(post_id, post_content, bad_url, db_genre, current_pid, dry_run=False):
    """
    該当の WP 記事のコンテンツ内にある壞れたリンクを修復する
    """
    auth = (WP_USER, WP_APP_PASSWORD)
    
    # 新しいリンク先を取得
    new_link = get_new_internal_link(db_genre, current_pid)
    if not new_link:
        logger.warning(f"  -> 代わりの関連記事が見つかりませんでした (ジャンル:{db_genre})。修復をスキップします。")
        return False
        
    # WPコンテンツ内の該当URLとタイトルを置換する
    # <a href="{bad_url}">...</a> のようなパターンを探す
    pattern = r'<a href="' + re.escape(bad_url) + r'".*?>(.*?)</a>'
    
    def replacer(match):
        new_tag = f'<a href="{new_link["url"]}">{new_link["title"]}</a>'
        logger.info(f"    [置換] '{match.group(1)}' -> '{new_link['title']}'")
        return new_tag

    new_content = re.sub(pattern, replacer, post_content)
    
    if new_content == post_content:
        logger.warning("  -> コンテンツ内に置換対象が見つかりませんでした。")
        return False
        
    if dry_run:
        logger.info(f"  -> DRY-RUN: 記事ID {post_id} のコンテンツ修正をシミュレートしました。")
        return True
        
    # WP更新実行
    update_data = {"content": new_content}
    try:
        r = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}", auth=auth, json=update_data, timeout=20)
        if r.status_code in (200, 201):
            logger.info(f"  -> 🏥 記事ID {post_id} のリンクを修復しました！")
            return True
        else:
            logger.error(f"  -> WP更新エラー: {r.status_code} {r.text}")
            return False
    except Exception as e:
        logger.error(f"  -> WP通信エラー: {e}")
        return False


def run_healer(dry_run=False):
    logger.info("=== Nexus Healer (リンク自動修復) 開始 ===")
    if dry_run:
        logger.info("※ DRY-RUNモード: 実際のWP更新は行いません")
        
    purged_items = get_purged_urls()
    logger.info(f"削除済みの元記事URL数: {len(purged_items)}件 をスキャンします")
    
    auth = (WP_USER, WP_APP_PASSWORD)
    
    total_healed = 0
    total_scanned = 0
    
    for item in purged_items:
        bad_url = item["wp_post_url"]
        logger.info(f"スキャン開始: [リンク切れ] {bad_url}")
        
        # WP内でこのURLを含む記事を検索する
        try:
            # 検索クエリでURLを探す（WP REST APIの search は本文にもヒットする）
            search_url = f"{WP_SITE_URL}/wp-json/wp/v2/posts"
            # プレーンなURLとして検索に乗らない可能性もあるので、URLの最後のスラッシュやドメインを工夫して検索
            search_term = bad_url.replace("https://novelove.jp/", "") 
            r = requests.get(search_url, params={"search": search_term, "_fields": "id,title,content"}, auth=auth, timeout=30)
            
            if r.status_code != 200:
                logger.warning(f"  サーチエラー: {r.status_code}")
                continue
                
            posts = r.json()
            hits = [p for p in posts if bad_url in p["content"]["rendered"]]
            
            if not hits:
                continue
                
            logger.info(f"  -> {len(hits)}件の被害記事（リンク切れを抱えた記事）を発見しました！")
            
            # DBから代替ジャンルを引くための情報
            for p in hits:
                post_id = p["id"]
                content = p["content"]["rendered"]
                
                # 自分自身のジャンルを元に代替を探したいが、ここでは一律FANZAのnovel_tl/blなどから探す。
                # より精緻にするなら p のタグを調べて引くが、今回は簡易的に novel_tl 固定にするか、消えた記事のジャンルにあわせる。
                # 消えた記事(item)のレコードがないので少し工夫する。とりあえずDBからitem["product_id"]のジャンルを引く。
                # get_purged_urls で抽出した情報を使う。
                
                # 代替用ジャンルの特定
                db_genre = "novel_tl" # default fallback
                for d in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
                    if os.path.exists(d):
                        _conn = db_connect(d)
                        _c = _conn.execute("SELECT genre FROM novelove_posts WHERE product_id=?", (item["product_id"],)).fetchone()
                        _conn.close()
                        if _c:
                            db_genre = _c[0]
                            break
                
                total_scanned += 1
                success = heal_wordpress_post(post_id, content, bad_url, db_genre, item["product_id"], dry_run)
                if success:
                    total_healed += 1
                    
        except Exception as e:
            logger.error(f"  エラー: {e}")

    logger.info("=== Nexus Healer 完了 ===")
    logger.info(f"修復を試みたリンク切れ記事数: {total_scanned}件")
    logger.info(f"修復成功件数: {total_healed}件")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_healer(dry_run=args.dry_run)
