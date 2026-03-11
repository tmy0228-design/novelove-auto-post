import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
auth = (WP_USER, WP_APP_PASSWORD)

MENU_ID = 8

def update_menu():
    try:
        # 1. 現在のメニュー項目を取得
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/menu-items", auth=auth, params={"menus": MENU_ID}, timeout=15)
        items = r.json()
        
        # 不要な項目（女性向け ID:25）を削除
        for item in items:
            if str(item.get("object_id")) == "25":
                requests.delete(f"{WP_SITE_URL}/wp-json/wp/v2/menu-items/{item['id']}", auth=auth, params={"force": True}, timeout=15)
                print(f"Deleted menu item: {item['title']['rendered']} (ID: {item['id']})")

        # 2. 新しい項目を追加するためのデータ定義
        # BL(23), BL R-18(28), TL(24), TL R-18(29), Home(132), About(39)
        # 並び順を考慮して一度全部整理したいが、追加のみ行う
        
        targets = [
            {"title": "BL R-18", "object_id": 28, "object": "category"},
            {"title": "TL R-18", "object_id": 29, "object": "category"},
        ]
        
        for t in targets:
            # 既に存在するかチェック
            exists = any(str(i.get("object_id")) == str(t["object_id"]) for i in items)
            if not exists:
                payload = {
                    "title": t["title"],
                    "menu_order": 99, # 暫定
                    "status": "publish",
                    "object": t["object"],
                    "object_id": t["object_id"],
                    "menus": MENU_ID,
                    "type": "taxonomy"
                }
                r_add = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/menu-items", auth=auth, json=payload, timeout=15)
                print(f"Added menu item: {t['title']} (Res: {r_add.status_code})")
            else:
                print(f"Menu item already exists: {t['title']}")

        # 3. 最後に並び替え（menu_order の更新）
        # 正しい順序: Home(132), BL(23), BL R-18(28), TL(24), TL R-18(29), About(39)
        order_map = {
            "132": 1, # Home
            "23":  2, # BL
            "28":  3, # BL R-18
            "24":  4, # TL
            "29":  5, # TL R-18
            "39":  6, # About
        }
        
        # 最新の全項目を取得
        r_final = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/menu-items", auth=auth, params={"menus": MENU_ID}, timeout=15)
        final_items = r_final.json()
        
        for item in final_items:
            obj_id = str(item.get("object_id"))
            if obj_id in order_map:
                new_order = order_map[obj_id]
                requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/menu-items/{item['id']}", auth=auth, json={"menu_order": new_order}, timeout=15)
                print(f"Updated order for {item['title'].get('rendered', '')}: {new_order}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    update_menu()
