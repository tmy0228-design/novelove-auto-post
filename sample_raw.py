import requests
import xml.etree.ElementTree as ET
url = 'https://api.digiket.com/xml/api/getxml.php?target=6&sort=new'
r = requests.get(url)
# Let requests guess encoding or fallback to utf-8
r.encoding = r.apparent_encoding
root = ET.fromstring(r.text)
for idx, item in enumerate(root.findall('.//item')[:3]):
    title = item.find('title').text
    desc = item.find('description').text
    print(f'-- Item {idx+1}: {title}')
    print(f'Desc: {repr(desc)}')
