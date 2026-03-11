import json
import logging
from auto_post import fetch_ranking_dmm_fanza, fetch_ranking_dlsite

# ログを出力してフィルタリングの様子を見る
logging.basicConfig(level=logging.INFO)

print("=== FANZA BL Ranking Test ===")
fanza_bl = fetch_ranking_dmm_fanza("FANZA", "BL")
print("\n[Result FANZA BL]")
for i, item in enumerate(fanza_bl):
    print(f"{i+1}: {item['title'][:50]} (desc length: {len(item.get('description', ''))})")
    
print("\n=== DLsite BL Ranking Test ===")
dlsite_bl = fetch_ranking_dlsite("BL")
print("\n[Result DLsite BL]")
for i, item in enumerate(dlsite_bl):
    print(f"{i+1}: {item['title'][:50]} (desc length: {len(item.get('description', ''))})")
