import sqlite3
import glob
import requests
import time
import os
import sys

sys.path.append('/home/kusanagi/scripts')
from novelove_core import WP_SITE_URL, WP_USER, WP_APP_PASSWORD, DMM_API_ID, DMM_AFFILIATE_API_ID, HEADERS
from auto_post import get_or_create_term

def resolve_wp_id_from_url(wp_post_url):
    if not wp_post_url: return None
    try:
        slug = [p for p in wp_post_url.rstrip('/').split('/') if p][-1]
        r = requests.get(f'{WP_SITE_URL}/wp-json/wp/v2/posts', auth=(WP_USER, WP_APP_PASSWORD), params={'slug': slug, '_fields': 'id,slug,link'}, timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()[0]['id']
    except Exception as e:
        print(f'Error resolving {wp_post_url}: {e}')
    return None

conn = sqlite3.connect('/home/kusanagi/scripts/novelove.db')
c = conn.cursor()

# STEP 1: Backfill missing wp_post_id
c.execute("SELECT product_id, wp_post_url FROM novelove_posts WHERE status='published' AND (wp_post_id IS NULL OR wp_post_id = '') AND wp_post_url IS NOT NULL")
rows = c.fetchall()
print(f'Backfilling {len(rows)} missing wp_post_ids...')
for i, (pid, url) in enumerate(rows):
    wp_id = resolve_wp_id_from_url(url)
    if wp_id:
        c.execute('UPDATE novelove_posts SET wp_post_id=? WHERE product_id=?', (wp_id, pid))
        conn.commit()
    time.sleep(0.3)

# STEP 2: Rebuild Exclusive logic
excl_tag_map = {'DMM.com': 'FANZA独占', 'FANZA': 'FANZA独占', 'Lovecal': 'らぶカル独占'}

c.execute("SELECT product_id, title, is_exclusive, wp_post_id, site, wp_tags FROM novelove_posts WHERE status='published' AND wp_post_id IS NOT NULL AND post_type='regular' AND (site LIKE '%DMM%' OR site LIKE '%FANZA%' OR site LIKE '%Lovecal%' OR site LIKE '%らぶカル%')")
rows2 = c.fetchall()
print(f'\nRebuilding exclusive tags for {len(rows2)} posts...')

for idx, (pid, title, cur_excl, wp_id, site, wp_tags_str) in enumerate(rows2):
    cur_excl = cur_excl or 0
    t_name = excl_tag_map.get(site, 'FANZA独占')
    if 'らぶカル' in (wp_tags_str or '') or 'lovecul' in site.lower(): t_name = 'らぶカル独占'
    
    item = None
    for try_site in ['FANZA', 'DMM.com']:
        try:
            r = requests.get('https://api.dmm.com/affiliate/v3/ItemList', headers=HEADERS, params={'api_id': DMM_API_ID, 'affiliate_id': DMM_AFFILIATE_API_ID, 'site': try_site, 'cid': pid, 'output': 'json'}, timeout=10)
            items = r.json().get('result', {}).get('items', [])
            if items:
                item = items[0]
                break
        except Exception: pass
        time.sleep(0.5)

    if not item: continue
    info = item.get('iteminfo', {})
    g_str = ' '.join([g.get('name', '') for g in info.get('genre', [])])
    l_str = ' '.join([l.get('name', '') for l in info.get('label', [])])

    true_excl = 0
    if '独占' in g_str or '専売' in g_str or '独占' in l_str or '専売' in l_str:
        if '先行' not in g_str and '先行' not in l_str:
            true_excl = 1
            
    c.execute('UPDATE novelove_posts SET is_exclusive=? WHERE product_id=?', (true_excl, pid))
    conn.commit()

    if cur_excl != true_excl:
        print(f'=> DB UPDATED {pid}: {cur_excl} -> {true_excl}')
        
        # update wp tags...
        t_id = get_or_create_term(t_name, 'tags')
        if not t_id: continue
        r = requests.get(f'{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_id}?_fields=tags', auth=(WP_USER, WP_APP_PASSWORD)).json()
        new_tags = r.get('tags', [])
        if true_excl == 0:
            if t_id in new_tags: new_tags.remove(t_id)
            alt_id = get_or_create_term('FANZA独占' if t_name == 'らぶカル独占' else 'らぶカル独占', 'tags')
            if alt_id and alt_id in new_tags: new_tags.remove(alt_id)
        else:
            if t_id not in new_tags: new_tags.append(t_id)
            
        requests.post(f'{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_id}', auth=(WP_USER, WP_APP_PASSWORD), json={'tags': new_tags})
        print(f'   WP TAGS UPDATED for {pid}')

conn.close()
print('ALL DONE.')
