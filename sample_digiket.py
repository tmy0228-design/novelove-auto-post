import requests
from bs4 import BeautifulSoup
import re

def sample_target(target_id, target_name):
    print(f'\\n=== Sampling {target_name} (target={target_id}) ===')
    url = f'https://api.digiket.com/xml/api/getxml.php?target={target_id}&sort=new'
    r = requests.get(url)
    soup = BeautifulSoup(r.content.decode('cp932', errors='replace'), 'html.parser')
    items = soup.find_all('item')
    print(f'Fetched {len(items)} items. Showing first 5:')
    for item in items[:5]:
        title = item.find('title').text if item.find('title') else ''
        desc = item.find('description')
        desc_text = desc.text if desc else ''
        m = re.search(r'ジャンル[：:]\\\\s*</strong>(.*?)(?:</td>|</div>|</p>|</li>|</span>)', desc_text, re.S)
        tags = []
        if m:
            tags = re.findall(r'<a[^>]*>([^<]+)</a>', m.group(1))
        print(f'  - Title: {title}')
        print(f'    Tags : {tags}')

sample_target('6', '商業 女性コミック')
sample_target('8', '商業 BL/TL')
