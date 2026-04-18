import sqlite3
import glob

dbs = glob.glob('novelove*.db')
score_5_posts = []
score_4_posts = []

for db in dbs:
    try:
        conn = sqlite3.connect(db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='novelove_posts'")
        if not cursor.fetchone(): continue
        
        # Get Score 5
        cursor.execute("SELECT published_at, title, description, wp_post_url, original_tags, ai_tags FROM novelove_posts WHERE status='published' AND desc_score=5 ORDER BY published_at DESC LIMIT 3")
        for r in cursor.fetchall():
            score_5_posts.append({'date': r[0], 'title': r[1], 'desc': r[2], 'url': r[3], 'orig_tags': r[4], 'ai_tags': r[5], 'db': db})
            
        # Get Score 4
        cursor.execute("SELECT published_at, title, description, wp_post_url, original_tags, ai_tags FROM novelove_posts WHERE status='published' AND desc_score=4 ORDER BY published_at DESC LIMIT 10")
        for r in cursor.fetchall():
            score_4_posts.append({'date': r[0], 'title': r[1], 'desc': r[2], 'url': r[3], 'orig_tags': r[4], 'ai_tags': r[5], 'db': db})
        conn.close()
    except Exception as e:
        pass

score_5_posts.sort(key=lambda x: x['date'] if x['date'] else '', reverse=True)
score_4_posts.sort(key=lambda x: x['date'] if x['date'] else '', reverse=True)

report = "# 抜き打ち調査結果: スコア4 vs スコア5\n\n"

report += "## 本日周辺（直近）のスコア5の記事\n"
if not score_5_posts:
    report += "直近のスコア5記事は見つかりませんでした。\n"
else:
    for i, p in enumerate(score_5_posts[:3]):
        desc_len = len(p['desc']) if p['desc'] else 0
        report += f"### {i+1}. {p['title']} ({p['db']})\n"
        report += f"- **公開日時:** {p['date']}\n"
        report += f"- **WordPress URL:** {p['url']}\n"
        report += f"- **あらすじの文字数:** {desc_len}文字\n"
        report += f"- **公式属性タグ:** {p['orig_tags']}\n"
        report += f"- **AI生成タグ:** {p['ai_tags']}\n"
        report += "#### 【取得したあらすじ】\n"
        report += f"{p['desc'][:400]}... (以下略)\n\n"

report += "---\n\n"
report += "## 最近のスコア4の記事サンプル（特大記事へ格上げできるか？）\n"
for i, p in enumerate(score_4_posts[:6]):
    desc_len = len(p['desc']) if p['desc'] else 0
    report += f"### {i+1}. {p['title']} ({p['db']})\n"
    report += f"- **公開日時:** {p['date']}\n"
    report += f"- **WordPress URL:** {p['url']}\n"
    report += f"- **あらすじの文字数:** {desc_len}文字\n"
    report += f"- **公式属性タグ:** {p['orig_tags']}\n"
    report += f"- **AI生成タグ:** {p['ai_tags']}\n"
    report += "#### 【取得したあらすじ】\n"
    report += f"{p['desc'][:400]}... (以下略)\n\n"

with open("score_4_analysis_report.md", "w", encoding="utf-8") as f:
    f.write(report)
