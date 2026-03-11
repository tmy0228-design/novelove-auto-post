import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()

WP_SITE_URL = "https://novelove.jp"
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
auth = (WP_USER, WP_APP_PASSWORD)

def inspect_menus():
    try:
        # Get all menus
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/menus", auth=auth, timeout=15)
        if r.status_code != 200:
            # Try wp/v2/nav-menus as fallback if the theme/plugin provides it
            r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/nav-menus", auth=auth, timeout=15)
        
        menus = r.json()
        print("--- Menus ---")
        for m in menus:
            # Check for name and id
            mid = m.get("id") or m.get("term_id")
            name = m.get("name")
            print(f"ID: {mid}, Name: {name}")
            
            # Get menu items for this menu
            r_items = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/menu-items", auth=auth, params={"menus": mid}, timeout=15)
            if r_items.status_code != 200:
                # Fallback for standard REST API nav menu items
                r_items = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/nav-menu-items", auth=auth, params={"nav_menu": mid}, timeout=15)
            
            items = r_items.json()
            print(f"  Items for Menu {mid}:")
            for item in items:
                title = item.get("title", {}).get("rendered", item.get("title", ""))
                obj_id = item.get("object_id")
                type_label = item.get("type_label") or item.get("object")
                print(f"    - [{type_label}] {title} (ID: {obj_id})")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_menus()
