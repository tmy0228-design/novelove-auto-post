import requests
import os
import re
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")

auth = (WP_USER, WP_APP_PASSWORD)

def check_and_delete_trash():
    print("WordPressのゴミ箱（Trash）から記事を取得しています...")
    # status=trashで記事を取得
    params = {
        "status": "trash",
        "per_page": 100
    }
    
    r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, params=params)
    if r.status_code != 200:
        print(f"Error fetching trash: {r.status_code} - {r.text}")
        return
        
    posts = r.json()
    if not posts:
        print("ゴミ箱に記事はありません。")
        return
        
    print(f"ゴミ箱に {len(posts)} 件の記事が見つかりました。\n")
    
    # 削除対象のキーワード（コード内の filter や FOREIGN_LABELS から抜粋）
    ng_keywords = [
        "ボイス", "音声", "ASMR", "CV.", "CV:", "cv.", "cv:", "シチュエーションCD", "バイノーラル", "ドラマCD",
        "簡体中文", "繁体中文", "繁體中文", "English", "韓国語版", "中国語版", "中国語",
        "한국어", "中文字幕", "翻訳台詞",
        "ゲーム", "アニメ", "CG集", "ノベル", "小説", "実用"
    ]
    
    # ゴミ箱にあるものは基本的に最近削除されたノイズ（v8のcleanupなど）と思われますが、念のためタイトルを確認
    for p in posts:
        post_id = p["id"]
        title = p["title"]["rendered"]
        
        # 不要な記事かどうかを厳密に判定しなくても、ゴミ箱にある = 既に削除判定されたもの
        # ただ、念の為キーワードにマッチするか、あるいは最近のクリーンアップで消されたものかを確認
        is_noise = any(kw.lower() in title.lower() for kw in ng_keywords)
        
        print(f"ID: {post_id} | Title: {title} | ノイズ判定: {'True' if is_noise else 'False'}")
        
        # 完全に削除 (force=true)
        print(f"  -> 完全に削除中...")
        del_r = requests.delete(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}?force=true", auth=auth)
        if del_r.status_code == 200:
            print(f"  -> ✅ 削除完了")
        else:
            print(f"  -> ❌ 削除失敗: {del_r.status_code}")

if __name__ == "__main__":
    check_and_delete_trash()
