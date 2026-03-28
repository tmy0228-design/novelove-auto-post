import os
import time
import requests
import sqlite3
import argparse
import re
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import logging

# novelove_core から必要な設定・関数を読み込む
from novelove_core import (
    DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET,
    db_connect,
)

WP_SITE_URL = os.environ.get("WP_SITE_URL", "https://novelove.jp")
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("NexusPurge")
WP_USER = os.environ.get("WP_USER", "novelove-admin")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"



def _call_deepseek_score_only(title, description, genre):
    """
    タイトルとあらすじだけをDeepSeekに送り、1〜5の「SCORE」のみを算出させる
    """
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY が設定されていません")
        return 0

    system_prompt = (
        "あなたは作品の魅力を辛口かつ客観的に評価するプロのキュレーターです。"
        "以下の作品タイトルとあらすじを読み、読者に胸を張っておすすめできるか（魅力的なフックがあるか、設定や展開が面白そうか）を5段階で評価し、"
        "その点数（1〜5の数値）のみを必ず以下のフォーマットで出力してください。\n"
        "SCORE: [1〜5の整数]\n"
        "※理由などのテキストは一切出力しないでください。"
    )
    user_prompt = f"ジャンル: {genre}\nタイトル: {title}\nあらすじ: {description}\n"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 10,
        "temperature": 0.1,
        "stream": False,
    }
    try:
        r = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30)
        if r.status_code != 200:
            logger.warning(f"DeepSeek APIエラー: {r.status_code}")
            return 0
        ans = r.json()["choices"][0]["message"]["content"]
        match = re.search(r'SCORE:\s*(\d)', ans, re.IGNORECASE)
        if match:
            return int(match.group(1))
        
        # プレーンな数字だけの場合も拾う
        match_fallback = re.search(r'\d', ans)
        if match_fallback:
            return int(match_fallback.group())
            
        return 0
    except Exception as e:
        logger.error(f"  [AI呼び出し失敗] {e}")
        return 0

def purge_wordpress_post(slug):
    """
    WordPressの記事をslug (product_id) を元に検索し、完全削除(force=true)する
    """
    auth = (WP_USER, WP_APP_PASSWORD)
    try:
        # 1. slugからIDを検索
        search_req = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", params={"slug": slug, "_fields": "id,title"}, auth=auth, timeout=15)
        if search_req.status_code != 200:
            logger.warning(f"  -> WP検索エラー: {search_req.text}")
            return False
            
        posts = search_req.json()
        if not posts:
            logger.warning(f"  -> WP上に記事が存在しません (slug={slug})。削除チェックをスキップします。")
            return True # 既に無いなら成功扱い
            
        wp_post_id = posts[0]['id']
        wp_title = posts[0]['title']['rendered']
        
        # 2. 記事の完全削除(Trashではなく即時削除)
        # ※ ゴミ箱に送りたい場合は params={} （force不要）にする
        del_req = requests.delete(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}", auth=auth, params={"force": "true"}, timeout=20)
        if del_req.status_code in (200, 201):
            logger.info(f"  -> 🗑️ WP記事を削除完了: ID={wp_post_id} ({wp_title})")
            return True
        else:
            logger.error(f"  -> WP記事削除エラー: {del_req.status_code} {del_req.text}")
            return False
            
    except Exception as e:
        logger.error(f"  -> WP通信エラー: {e}")
        return False

def run_purge(dry_run=False):
    dbs = [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]
    
    total_processed = 0
    total_purged = 0
    total_passed = 0
    
    logger.info(f"=== Nexus Purge (再審査＆削除) 開始 ===")
    if dry_run:
        logger.info("※ DRY-RUNモード: 実際の削除やDB書き換えは行いません")
        
    for db_path in dbs:
        if not os.path.exists(db_path):
            continue
            
        site_name = os.path.basename(db_path).replace('.db', '').replace('novelove_', '')
        conn = db_connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # published で desc_score = 0 の記事を探す (AI審査が抜けていた期間の投稿データ)
        # ランキング記事（ranking）は対象外とし、通常記事（regular）のみをパージ対象とする
        rows = c.execute("SELECT product_id, title, description, genre, published_at FROM novelove_posts WHERE status='published' AND post_type='regular' AND desc_score=0 ORDER BY published_at DESC").fetchall()
        
        if not rows:
            conn.close()
            continue
            
        logger.info(f"[{site_name}] スコア未付与の公開記事: {len(rows)}件 発見")
        
        for row in rows:
            pid = row['product_id']
            title = row['title']
            desc = row['description']
            genre = row['genre']
            
            logger.info(f"審査中: {title[:25]}...")
            score = _call_deepseek_score_only(title, desc, genre)
            
            if score == 0:
                logger.warning("  -> [エラー] スコア取得失敗のためスキップ")
                time.sleep(2)
                continue
                
            if score < 4:
                logger.warning(f"  -> [不合格] スコア: {score} 点 -> 削除対象です 🗑️")
                if not dry_run:
                    # WordPressから削除
                    if purge_wordpress_post(pid):
                        # DBをexcludedに更新
                        c.execute("UPDATE novelove_posts SET status='excluded', last_error=?, desc_score=0 WHERE product_id=?", (f"purged_low_score: {score}", pid))
                        conn.commit()
                        total_purged += 1
            else:
                logger.info(f"  -> [合格] スコア: {score} 点 -> 維持します ✨")
                if not dry_run:
                    # DBのスコアを更新
                    c.execute("UPDATE novelove_posts SET desc_score=? WHERE product_id=?", (score, pid))
                    conn.commit()
                    total_passed += 1
                    
            total_processed += 1
            time.sleep(1) # API制限用スリープ
            
        conn.close()

    logger.info(f"=== Nexus Purge 完了 ===")
    logger.info(f"確認件数: {total_processed} 件")
    logger.info(f"合格維持: {total_passed} 件")
    logger.info(f"削除除外: {total_purged} 件")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="審査だけ行い、実際の削除・更新を行わない")
    args = parser.parse_args()
    
    run_purge(dry_run=args.dry_run)
