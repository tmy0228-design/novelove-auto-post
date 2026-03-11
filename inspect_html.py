import requests
import re

url = "https://novelove.jp/dlsite-bl-ranking-2026-03-w10-5/"
r = requests.get(url)
html = r.text

# ボタンが含まれる部分を抽出
# 前回のHTMLで custom-button-container を使っているのでそれを探す
matches = re.findall(r'<div class="custom-button-container".*?</div>', html, re.DOTALL)
for i, m in enumerate(matches):
    print(f"--- Button {i+1} RAW ---")
    print(repr(m)) # Use repr to see newlines and other chars
    print("\n")

# ついでに適用されていそうなベースCSSのクラスやIDを確認
# 記事本文のコンテナクラスなどを探す
content_match = re.search(r'<article.*?>', html)
if content_match:
    print(f"Article Tag: {content_match.group(0)}")
