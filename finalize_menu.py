import requests
import os
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
auth = (WP_USER, WP_APP_PASSWORD)

MENU_ID = 8

def update_menu_final():
    try:
        # 1. 現在のメニュー項目を取得
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/menu-items", auth=auth, params={"menus": MENU_ID}, timeout=15)
        items = r.json()
        
        # 不要な項目（削除済みカテゴリ ID: 28, 29, 25）をメニューから完全に除去
        deleted_cat_ids = ["28", "29", "25"]
        for item in items:
            obj_id = str(item.get("object_id"))
            if obj_id in deleted_cat_ids:
                requests.delete(f"{WP_SITE_URL}/wp-json/wp/v2/menu-items/{item['id']}", auth=auth, params={"force": True}, timeout=15)
                print(f"Removed broken menu item: {item['title'].get('rendered', '')} (Cat ID: {obj_id})")

        # 2. 並び替えの実行
        # 希望順序: ホーム(132), BL(23), TL(24), ノベラブについて(39)
        order_map = {
            "132": 1, # Home
            "23":  2, # BL
            "24":  3, # TL
            "39":  4, # About
        }
        
        # 最新のメニュー状態を取得
        r_final = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/menu-items", auth=auth, params={"menus": MENU_ID}, timeout=15)
        final_items = r_final.json()
        
        for item in final_items:
            obj_id = str(item.get("object_id"))
            if obj_id in order_map:
                new_order = order_map[obj_id]
                requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/menu-items/{item['id']}", auth=auth, json={"menu_order": new_order}, timeout=15)
                print(f"Set order for {item['title'].get('rendered', '')}: {new_order}")

    except Exception as e:
        print(f"Error finalizing menu: {e}")

if __name__ == "__main__":
    update_menu_final()
