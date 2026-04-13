import sqlite3
import re
from textwrap import shorten

# Connect to local DB (which mirrors published WP posts)
conn = sqlite3.connect('C:/Users/PC/.gemini/antigravity/playground/novelove-github/novelove.db')
c = conn.cursor()

# 1. NO IMAGE issue (FIFU checked directly via DB if available, or just check recent posts)
print("=== RECENT POSTS (Checking for FIFU / Hero Image issues) ===")
# We only have product_id, title, product_url... in novelove_posts
# Let's see recent 10 posts
rows = c.execute("SELECT product_id, title, site, affiliate_url FROM novelove_posts ORDER BY rowid DESC LIMIT 10").fetchall()
for r in rows:
    print(f"RECENT: {r[0]} | {r[2]} | {shorten(r[1], width=30)}")

print("\n=== DUPLICATE IMAGE ISSUE (Legacy Posts) ===")
# Since we can't easily query WP post_content locally, we use a WP query via SSH wrapper.
