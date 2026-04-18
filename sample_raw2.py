import requests
import xml.etree.ElementTree as ET
url = 'https://api.digiket.com/xml/api/getxml.php?target=6&sort=new'
r = requests.get(url)
try:
    root = ET.fromstring(r.content)
    for idx, item in enumerate(root.findall('.//item')[:2]):
        title = item.find('title').text
        desc = item.find('description').text
        print(f'-- Title: {title}')
        print(f'Desc: {repr(desc)}')
except Exception as e:
    print(f'Error: {e}')
