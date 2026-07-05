import sqlite3
from novelove_core import DB_FILE_UNIFIED

def migrate():
    print(f"Connecting to DB: {DB_FILE_UNIFIED}...")
    conn = sqlite3.connect(DB_FILE_UNIFIED)
    c = conn.cursor()
    
    columns_to_add = [
        ("author_detail", "TEXT"),
        ("cast_info", "TEXT"),
        ("series_name", "TEXT"),
        ("page_count", "INTEGER")
    ]
    
    # 既存のカラム一覧を取得
    c.execute("PRAGMA table_info(novelove_posts)")
    existing_columns = [row[1] for row in c.fetchall()]
    
    for col_name, col_type in columns_to_add:
        if col_name in existing_columns:
            print(f"Column '{col_name}' already exists. Skipping.")
        else:
            try:
                c.execute(f"ALTER TABLE novelove_posts ADD COLUMN {col_name} {col_type};")
                print(f"Added column '{col_name}' ({col_type}).")
            except Exception as e:
                print(f"Error adding column '{col_name}': {e}")
                
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
