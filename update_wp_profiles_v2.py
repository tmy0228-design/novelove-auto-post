import os
import subprocess
import re
import sys

WP_DIR = "/home/kusanagi/myblog/DocumentRoot"
WP_CLI = "wp"

# == 1. SEO メタデータ定義 ==
SITE_SEO = {
    "blogname": "ノベラブ(Novelove) ｜BL・TL特化の「神解釈」おすすめ作品紹介サイト",
    "blogdescription": "【BL/TL特化】年齢も好みも違う5名の公式レコメンダーが、商業コミックから同人誌・小説まで「本当に面白い神作品」だけを厳選布教！あなたと「解釈一致」する紹介者が必ず見つかる、オタクのための作品紹介サイトです。"
}

PAGE_SEO = {
    "39": {
        "title": "ノベラブ(Novelove)とは？5人のレコメンダーが語るBL/TL特化紹介サイト",
        "desc": "ノベラブ(Novelove)の5人の公式レコメンダー（紫苑・茉莉花・葵・桃香・蓮）のプロフィールをご紹介。それぞれの得意ジャンルやこだわりの性癖、メンバー同士の意外な関係性相関図を大公開します。"
    },
    "1965": { # 紫苑
        "title": "紫苑（BL担当）｜執着・年下攻めの神解釈おすすめ作品一覧 | Novelove",
        "desc": "Novelove公式BLレコメンダー紫苑の紹介ページ。執着攻め・年下攻めなどの「関係性の重さ」を愛するクールなOLが、緻密なキャラクター分析と「完全な解釈一致」で厳選したBLコミック・小説のおすすめ一覧です。"
    },
    "1966": { # 茉莉花
        "title": "茉莉花（TL担当）｜激甘・溺愛の胸きゅんおすすめ作品一覧 | Novelove",
        "desc": "Novelove公式TLレコメンダー茉莉花の紹介ページ。ピュアで甘々な溺愛展開をこよなく愛するカフェ店員が、「全人類ハッピーエンド」を合言葉に厳選したTLコミック・小説のおすすめ一覧です。"
    },
    "1967": { # 葵
        "title": "葵（BL担当）｜同人誌・マイナーカプの限界オタクおすすめ作品一覧 | Novelove",
        "desc": "Novelove公式BLレコメンダー葵の紹介ページ。同人推し活に命を懸ける限界オタク女子大生が、ほとばしる情熱と圧倒的語彙力（？）で全力プレゼンするBLコミック・同人誌のおすすめ一覧です。"
    },
    "1968": { # 桃香
        "title": "桃香（TL担当）｜身分差・契約・大人向けディープおすすめ作品一覧 | Novelove",
        "desc": "Novelove公式TLレコメンダー桃香の紹介ページ。ピュアな恋愛では物足りなくなった大人の女性が、ドロドロの執着や契約関係など「ディープな性癖」に刺さるTLコミック・小説のおすすめ一覧です。"
    },
    "1969": { # 蓮
        "title": "蓮（BL担当）｜心理描写・伏線考察のインテリおすすめ作品一覧 | Novelove",
        "desc": "Novelove公式BLレコメンダー蓮の紹介ページ。作中のジェンダー表象や関係性の美しさを「学術的」に分析・考察するインテリ大学院生（実は隠れ熱血派）が厳選した、読み応え抜群のBLコミック・小説のおすすめ一覧です。"
    }
}

def run_wp(args):
    cmd = [WP_CLI] + args + [f"--path={WP_DIR}", "--allow-root"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error executing {' '.join(args)}: {res.stderr}")
    return res.stdout.strip()

def update_seo():
    print("✨ トップページのSEOメタデータを更新中(WordPress標準オプション)...")
    run_wp(["option", "update", "blogname", SITE_SEO["blogname"]])
    run_wp(["option", "update", "blogdescription", SITE_SEO["blogdescription"]])
    
    print("✨ 固定ページのSEOメタデータを更新中(Cocoon仕様)...")
    for pid, meta in PAGE_SEO.items():
        run_wp(["post", "meta", "update", pid, "the_page_seo_title", meta["title"]])
        run_wp(["post", "meta", "update", pid, "the_page_meta_description", meta["desc"]])
        print(f"  - ページ {pid} 更新完了")

def update_character_html(pid, name):
    print(f"✨ 担当者ページ({name}: {pid}) の見出しを日本語に更新中...")
    content = run_wp(["post", "get", pid, "--field=post_content"])
    if not content: return
    
    # 英語見出しの置換 (正規表現)
    content = re.sub(r'<h2 class="rev-section-title">PROFILE<br />.*?</h2>', '<h2 class="rev-section-title">📝 基本プロフィール</h2>', content, flags=re.IGNORECASE|re.DOTALL)
    content = re.sub(r'<h2 class="rev-section-title">EPISODE<br />.*?</h2>', '<h2 class="rev-section-title">☕ 日常エピソード</h2>', content, flags=re.IGNORECASE|re.DOTALL)
    content = re.sub(r'<h2 class="rev-section-title">RELATIONSHIPS<br />.*?</h2>', '<h2 class="rev-section-title">🤝 他の紹介者との関係性</h2>', content, flags=re.IGNORECASE|re.DOTALL)
    
    # ファイルに書き出してWP-CLIで読み込ませる(引数長オーバー対策)
    tmp_file = f"/tmp/post_{pid}.html"
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(content)
    run_wp(["post", "update", pid, tmp_file])
    os.remove(tmp_file)

def update_about_html():
    print("✨ Aboutページ(39) の 結成ストーリーと相関図を更新中...")
    # novelove_soul.py から関係性マトリクスを取得
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    try:
        from novelove_soul import RELATIONSHIPS
    except ImportError:
        print("novelove_soul.py のインポートに失敗しました。")
        return
        
    content = run_wp(["post", "get", "39", "--field=post_content"])
    
    # イントロダクションの置換
    intro_html = """<div class="about-intro">
<p style="font-weight:bold; font-size:1.2em; color:#d81b60; margin-bottom:15px;">「解釈一致」の限界オタクたちが集う場所、Novelove</p>
<p>ここは、年齢も職業もバラバラな5人の女性たちが、「BL・TLへの異常な熱量」だけで繋がっている秘密の場所です。</p>
<p>発端は、茉莉花（TL担当）と葵（BL担当）の幼馴染コンビ。中学時代に茉莉花が葵を同人沼に引きずり込んで以来、二人は息をするように作品を語り合ってきました。<br>
そこへ、夜な夜なSNSで「神解釈」の長文を投下していたOL・紫苑、ピュアな恋愛では物足りなくなった大人の女性・桃香、そして「これは学術的研究資料です」と言い張りながらBLを読み漁る大学院生・蓮が合流。</p>
<p>趣味も属性もバラバラな5人ですが、「この尊さを誰かに布教したい！」という情熱は完全に一致。現在ではNoveloveの公式レコメンダー（作品紹介者）として、日々発掘した「神作品」の魅力を全力でお届けしています。</p>
</div>"""
    content = re.sub(r'<div class="about-intro">.*?</div>', intro_html, content, flags=re.IGNORECASE|re.DOTALL)
    
    # 相関図（グリッド）の構築
    rel_html = """<h2 class="about-section-title" style="margin-top:60px; text-align:center; font-size:1.6em; color:#2c3e50; border-bottom:2px solid #ffcfdf; padding-bottom:10px;">🤝 レコメンダー相関関係</h2>
<div class="relationship-grid" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:20px; margin-top:30px; margin-bottom:40px;">\n"""
    
    def get_name(id_str):
        mapping = { 'shion': '紫苑', 'marika': '茉莉花', 'aoi': '葵', 'momoka': '桃香', 'ren': '蓮' }
        return mapping.get(id_str, id_str)

    for pair_key, desc in RELATIONSHIPS.items():
        id1, id2 = pair_key.split('-')
        name1 = get_name(id1)
        name2 = get_name(id2)
        rel_html += f"""    <div style="background:#fff; border-radius:12px; padding:20px; box-shadow:0 4px 10px rgba(0,0,0,0.05); border:1px solid #eee;">
        <div style="display:flex; justify-content:center; align-items:center; margin-bottom:15px;">
            <img src="/wp-content/uploads/icons/{name1}.png" style="width:50px; height:50px; border-radius:50%; object-fit:cover; margin-right:10px;">
            <span style="font-weight:bold; color:#d81b60; margin:0 10px;">×</span>
            <img src="/wp-content/uploads/icons/{name2}.png" style="width:50px; height:50px; border-radius:50%; object-fit:cover; margin-left:10px;">
        </div>
        <p style="font-size:0.9em; line-height:1.6; color:#444; margin:0;">{desc}</p>
    </div>\n"""
    rel_html += "</div>\n\n"
    
    # Aboutページ末尾に相関図を追加（既存の相関図があれば置換、なければ追加）
    if "🤝 レコメンダー相関関係" in content:
        content = re.sub(r'<h2 class="about-section-title".*?🤝 レコメンダー相関関係.*?</h2\s*>\n<div class="relationship-grid.*?</div\s*>\n', rel_html, content, flags=re.IGNORECASE|re.DOTALL)
    else:
        # .reviewer-grid の </div> の後ろに挿入
        # 確実に挿入するため、最後の </div><!-- .reviewer-grid --> の前か後ろに入れる
        content = re.sub(r'(</div>\s*</p>\s*</div>\s*</div>\s*</div>)', lambda m: '</div>\n</p>\n</div>\n' + rel_html + '</div>\n</div>', content)
        # 上の正規表現が当たらなかった場合の安全策
        if "レコメンダー相関関係" not in content:
            content += "\n" + rel_html
            
    tmp_file = "/tmp/post_39.html"
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(content)
    run_wp(["post", "update", "39", tmp_file])
    os.remove(tmp_file)

if __name__ == "__main__":
    update_seo()
    update_character_html("1965", "紫苑")
    update_character_html("1966", "茉莉花")
    update_character_html("1967", "葵")
    update_character_html("1968", "桃香")
    update_character_html("1969", "蓮")
    update_about_html()
    print("✅ 全ての更新処理が完了しました！")
