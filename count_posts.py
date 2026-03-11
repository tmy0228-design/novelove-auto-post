import sqlite3
import os

def count_published(db_path):
    if not os.path.exists(db_path):
        return 0
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM novelove_posts WHERE status='published'").fetchone()[0]
    conn.close()
    return count

fanza_count = count_published("novelove.db")
dlsite_count = count_published("novelove_dlsite.db")

print(f"FANZA published posts: {fanza_count}")
print(f"DLsite published posts: {dlsite_count}")
print(f"Total target posts: {fanza_count + dlsite_count}")
