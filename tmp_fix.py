
import os, json, sqlite3, subprocess

doc_root = '/home/kusanagi/myblog/DocumentRoot'
wp_path = '/opt/kusanagi/bin/wp'

DB_FILES = [
    '/home/kusanagi/scripts/novelove.db',
    '/home/kusanagi/scripts/novelove_dlsite.db',
    '/home/kusanagi/scripts/novelove_digiket.db'
]

def get_row(slug):
    for path in DB_FILES:
        if not os.path.exists(path): continue
        c = sqlite3.connect(path, timeout=30)
        c.row_factory = sqlite3.Row
        row = c.execute('SELECT title, genre FROM novelove_posts WHERE product_id=?', (slug,)).fetchone()
        c.close()
        if row: return row
    return None

res = subprocess.run(f'cd {doc_root} && {wp_path} post list --category=1 --format=json --fields=ID,post_name --allow-root', shell=True, capture_output=True, text=True)
if res.returncode != 0:
    print('Error listing posts:', res.stderr)
    exit(1)
    
posts = json.loads(res.stdout)
print(f'Found {len(posts)} UNCATEGORIZED POSTS', flush=True)

count = 0
for p in posts:
    post_id = p['ID']
    slug = p['post_name']
    row = get_row(slug)
    if not row:
        print(f'[-Skip-] {slug} not found in DB', flush=True)
        continue

    title, genre = row['title'], str(row['genre']).lower()
    
    is_novel = False
    if 'novel' in genre or '小説' in genre: is_novel = True
    elif any(x in genre for x in ('comic','manga','doujin','コミック','漫画')): is_novel = False

    is_ranking = 'ranking' in slug.lower() or 'ランキング' in title

    if is_ranking: cat_slug = 'ranking'
    else:
        is_bl = 'bl' in genre.lower() or 'BL' in title.upper()
        if is_novel: cat_slug = 'bl-novel' if is_bl else 'tl-novel'
        else: cat_slug = 'bl-manga' if is_bl else 'tl-manga'

    cmd = f'cd {doc_root} && {wp_path} post term set {post_id} category {cat_slug} --by=slug --allow-root'
    u = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if u.returncode == 0:
        count += 1
        print(f'[Fixed] {slug} -> {cat_slug}', flush=True)
    else:
        print(f'[Error] {post_id}: {u.stderr}', flush=True)

print(f'Done! Fixed {count} posts.', flush=True)
subprocess.run(f'cd {doc_root} && {wp_path} cache flush --allow-root && kusanagi bcache clear && kusanagi fcache clear', shell=True)
