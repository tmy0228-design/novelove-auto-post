#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
novelove_curator.py — テーマ別まとめ記事自動生成バッチ
==========================================================
【役割】
  露出の低いタグや作品を優先的にまとめ、新設した「まとめ」カテゴリに
  内部リンクを集約することで、サイト全体のSEO戦闘力を最大化するバッチです。
==========================================================
"""

import os
import sys
import datetime
import random
import argparse
import re
import collections
import time as _time
import subprocess

# プロジェクトルートパスを通す
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

CURATION_LOCK_FILE = os.path.join(SCRIPT_DIR, "curation.lock")

from novelove_core import (
    logger, DB_FILE_UNIFIED, db_connect, WP_SITE_URL,
    get_affiliate_button_html, notify_discord,
    is_emergency_stop, MAIN_LOCK_FILE, RANK_LOCK_FILE,
    acquire_lock, release_lock,
)
from novelove_soul import REVIEWERS, FACT_GUARD, NG_PHRASES, MOOD_PATTERNS, AI_TAG_WHITELIST
from novelove_writer import _call_deepseek_raw
from novelove_fetcher import mask_input

# === クールダウン処理 ===
def get_cooldown_tags(conn, days=90):
    """過去N日間に使用されたタグを取得して除外対象とする"""
    c = conn.cursor()
    limit_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("SELECT wp_tags FROM novelove_posts WHERE post_type = 'curation' AND published_at >= ?", (limit_date,))
    rows = c.fetchall()
    
    cooldown = set()
    for (tag_str,) in rows:
        if not tag_str:
            continue
        # クロスタグ等の複数タグ結合も考慮し、カンマおよび&でスプリット
        for t in tag_str.replace("&", ",").split(","):
            t_clean = t.strip()
            if t_clean:
                cooldown.add(t_clean)
    return cooldown


def _ensure_curation_work_ids_column(conn):
    """まとめ出演IDカラムが無ければ追加する（旧DB互換）"""
    c = conn.cursor()
    cols = {row[1] for row in c.execute("PRAGMA table_info(novelove_posts)").fetchall()}
    if "curation_work_ids" not in cols:
        c.execute("ALTER TABLE novelove_posts ADD COLUMN curation_work_ids TEXT DEFAULT ''")
        conn.commit()
        logger.info("[Curator] Added column curation_work_ids")


def get_curation_featured_ids(conn):
    """過去のまとめに出演済みの通常作品 product_id 集合を返す。"""
    _ensure_curation_work_ids_column(conn)
    c = conn.cursor()
    c.execute(
        "SELECT curation_work_ids FROM novelove_posts "
        "WHERE post_type = 'curation' AND curation_work_ids IS NOT NULL AND curation_work_ids != ''"
    )
    featured = set()
    for (ids_str,) in c.fetchall():
        for pid in str(ids_str).split(","):
            pid = pid.strip()
            if pid:
                featured.add(pid)
    return featured


def _select_five_unused_works(works, featured_ids):
    """
    まとめ未出演作品をクリック昇順で最大5本選ぶ。
    未出演が5本未満なら None（そのタグ/ペアはスキップ対象）。
    スキップしてもタグの90日クールダウンには入れない。
    """
    unused = [w for w in works if w["product_id"] not in featured_ids]
    unused.sort(key=lambda x: x["clicks"])
    if len(unused) < 5:
        return None
    return unused[:5]

# === 週番号とジャンル選定 ===
def _get_week_number():
    """今日の日付から週番号（1〜4）を算出する。29日以降は第4週とする。"""
    day = datetime.datetime.now().day
    return min(4, (day - 1) // 7 + 1)

def _determine_genre_for_week(week, conn):
    """週番号に応じたジャンルグループを返す"""
    if week == 1:
        return "BL"
    elif week == 2:
        return "TL"
    elif week == 3:
        # 前回と逆のジャンルにする
        c = conn.cursor()
        c.execute("SELECT genre FROM novelove_posts WHERE post_type = 'curation' ORDER BY published_at DESC LIMIT 1")
        row = c.fetchone()
        if row:
            last = row[0]
            if last == "bl-curation":
                return "TL"
            elif last == "tl-curation":
                return "BL"
        return "BL"  # 履歴なし時のデフォルト
    elif week == 4:
        return "cross"
    return "BL"

# === タグと作品の選定ロジック ===
def select_theme_and_works(conn, week, forced_tag=None, forced_genre=None):
    """テーマ（タグ）と5作品を選定する。

    v21.5.6:
      - まとめ未出演作品を優先し、未出演が5本未満のタグ/ペアはその回スキップして次候補へ。
      - スキップしても90日クールダウンには入れない（新規が増えたら翌週以降また候補になる）。
      - 全候補が尽きて1本も書けない場合は (None, [], genre) を返す → 呼び出し側でDiscord通知。
    """
    cooldown_tags = get_cooldown_tags(conn)
    featured_ids = get_curation_featured_ids(conn)
    logger.info(f"[Curator] Cooldown tags: {cooldown_tags}")
    logger.info(f"[Curator] Already featured in curation: {len(featured_ids)} works")
    
    # 1. 公開中の記事データをロード (wp_tags からロードし、post_type = 'regular' に限定)
    c = conn.cursor()
    c.execute("""
        SELECT product_id, title, genre, wp_tags, gsc_clicks, affiliate_url, image_url, site, release_date, description
        FROM novelove_posts
        WHERE status = 'published' AND wp_tags != '' AND post_type = 'regular'
    """)
    rows = c.fetchall()
    
    works = []
    for r in rows:
        pid, title, genre, wp_tags_str, clicks, aff_url, img_url, site, r_date, desc = r
        tags = [t.strip() for t in wp_tags_str.split(",") if t.strip()]
        works.append({
            "product_id": pid,
            "title": title,
            "genre": genre,
            "tags": tags,
            "clicks": clicks or 0,
            "affiliate_url": aff_url,
            "image_url": img_url,
            "site": site,
            "release_date": r_date,
            "description": desc
        })
        
    logger.info(f"[Curator] Loaded {len(works)} published works with tags.")
    
    # BL/TLの分類
    bl_works = [w for w in works if 'bl' in w['genre'].lower()]
    tl_works = [w for w in works if 'tl' in w['genre'].lower()]
    
    # ジャンルの判定
    target_genre = None
    if forced_genre:
        target_genre = forced_genre
    else:
        target_genre = _determine_genre_for_week(week, conn)
        
    logger.info(f"[Curator] Target genre mode: {target_genre}")
    
    # 各タグがどの作品に紐付いているかをマッピング
    tag_to_bl_works = collections.defaultdict(list)
    tag_to_tl_works = collections.defaultdict(list)
    
    for w in bl_works:
        for t in w['tags']:
            tag_to_bl_works[t].append(w)
            
    for w in tl_works:
        for t in w['tags']:
            tag_to_tl_works[t].append(w)
            
    selected_tag = None
    selected_works = []
    genre_group = target_genre
    
    if forced_tag:
        # タグが強制指定されている場合
        selected_tag = forced_tag
        forced_tags = [t.strip() for t in forced_tag.split(",") if t.strip()]
        all_matching = [w for w in works if all(ft in w['tags'] for ft in forced_tags)]
        picked = _select_five_unused_works(all_matching, featured_ids)
        if not picked:
            logger.warning(
                f"[Curator] Forced tag '{selected_tag}' has only "
                f"{sum(1 for w in all_matching if w['product_id'] not in featured_ids)} unused works (<5). Abort."
            )
            return None, [], genre_group
        selected_works = picked
        bl_count = sum(1 for w in all_matching if 'bl' in w['genre'].lower())
        tl_count = sum(1 for w in all_matching if 'tl' in w['genre'].lower())
        genre_group = "BL" if bl_count >= tl_count else "TL"
        logger.info(f"[Curator] Forced tag '{selected_tag}' matched {len(all_matching)} works. Selecting {len(selected_works)} unused.")
        
    elif target_genre in ("BL", "TL"):
        tag_map = tag_to_bl_works if target_genre == "BL" else tag_to_tl_works
        
        # 候補タグの分析
        candidates = []
        for tag, tag_w_list in tag_map.items():
            if tag not in AI_TAG_WHITELIST:  # ★ ホワイトリストガード
                continue
            if tag in cooldown_tags:
                continue
            if len(tag_w_list) < 10:  # 通常タグは公開記事10件以上
                continue
            total_clicks = sum(w['clicks'] for w in tag_w_list)
            candidates.append({
                "tag": tag,
                "total_clicks": total_clicks,
                "work_count": len(tag_w_list),
                "works": tag_w_list
            })
            
        # クリック数の昇順（低い＝埋もれている）、同数なら作品数が多い方を優先
        candidates.sort(key=lambda x: (x['total_clicks'], -x['work_count']))
        
        skipped = 0
        for cand in candidates:
            picked = _select_five_unused_works(cand["works"], featured_ids)
            if not picked:
                unused_n = sum(1 for w in cand["works"] if w["product_id"] not in featured_ids)
                logger.info(
                    f"[Curator] Skip tag '{cand['tag']}' (unused={unused_n}<5, "
                    f"total={cand['work_count']}) → try next"
                )
                skipped += 1
                continue
            selected_tag = cand["tag"]
            selected_works = picked
            logger.info(
                f"[Curator] Selected tag: '{selected_tag}' "
                f"(Clicks: {cand['total_clicks']}, Works: {cand['work_count']}, "
                f"unused_picked=5, skipped_tags={skipped})"
            )
            break
        else:
            logger.warning(
                f"[Curator] No candidate tags with 5+ unused works for {target_genre} "
                f"(candidates={len(candidates)}, skipped_insufficient={skipped})."
            )
            
    elif target_genre == "cross":
        # クロスタグ（BLまたはTL内で、共通作品が5件以上ある2タグのペア）
        cross_candidates = []
        
        for g_name, tag_map, genre_w_list in [("BL", tag_to_bl_works, bl_works), ("TL", tag_to_tl_works, tl_works)]:
            # 5件以上の作品があるタグを抽出 (cooldown対象外 & ホワイトリスト内限定)
            valid_tags = [tag for tag, tag_w in tag_map.items() if tag in AI_TAG_WHITELIST and len(tag_w) >= 5 and tag not in cooldown_tags]
            
            for i in range(len(valid_tags)):
                for j in range(i+1, len(valid_tags)):
                    t1, t2 = valid_tags[i], valid_tags[j]
                    common = [w for w in genre_w_list if t1 in w['tags'] and t2 in w['tags']]
                    if len(common) >= 5:
                        total_clicks = sum(w['clicks'] for w in common)
                        cross_candidates.append({
                            "tags": (t1, t2),
                            "genre": g_name,
                            "total_clicks": total_clicks,
                            "work_count": len(common),
                            "works": common
                        })
                        
        cross_candidates.sort(key=lambda x: (x['total_clicks'], -x['work_count']))
        
        skipped = 0
        for cand in cross_candidates:
            picked = _select_five_unused_works(cand["works"], featured_ids)
            if not picked:
                unused_n = sum(1 for w in cand["works"] if w["product_id"] not in featured_ids)
                logger.info(
                    f"[Curator] Skip cross '{cand['tags'][0]}+{cand['tags'][1]}' "
                    f"(unused={unused_n}<5) → try next"
                )
                skipped += 1
                continue
            t1, t2 = cand["tags"]
            sorted_tags = sorted([t1, t2])
            selected_tag = f"{sorted_tags[0]},{sorted_tags[1]}"
            genre_group = f"cross-{cand['genre'].lower()}"
            selected_works = picked
            logger.info(
                f"[Curator] Selected cross tags: '{selected_tag}' ({genre_group}) "
                f"(Clicks: {cand['total_clicks']}, Works: {cand['work_count']}, skipped_pairs={skipped})"
            )
            break
        else:
            logger.warning(
                f"[Curator] No cross tag pairs with 5+ unused works "
                f"(pairs={len(cross_candidates)}, skipped={skipped})."
            )
            
    return selected_tag, selected_works, genre_group

# === AI執筆：導入コラム生成 ===
def generate_intro_column(reviewer, tag_name, genre_group):
    """まとめコラムの導入部分をAIで生成する（プレーンテキストのみで出力させる）"""
    mood = random.choice(MOOD_PATTERNS)
    
    # クロスタグ用の表記整形
    display_tag = tag_name.replace(",", "と")
    
    prompt = f"""あなたは「Novelove」のライター「{reviewer['name']}」です。
以下のテーマに沿って、まとめ記事の冒頭に掲載する短い導入挨拶コラムをキャラクターの口調で執筆してください。

【あなたの設定】
性格: {reviewer['personality']}
口調: {reviewer['tone']}
今回の感情: {mood}

【コラムのテーマ】
「{display_tag}」のおすすめ作品まとめ

【執筆ルール】
1. キャラクターの口調を全開にして、読者を歓迎し、これからテーマに沿ったおすすめ作品を紹介する興奮を短く語ってください。
2. 吹き出しの圧迫感を防ぐため、文字数は50〜80字程度（2文程度）に必ず収めてください。
3. 作品の具体的なあらすじ紹介や、詳しいレビュー考察は個別記事で書くため、ここには一切記述しないでください。
4. HTMLタグ（divやpなど）は絶対に出力せず、純粋なプレーンテキストのみで出力してください。
5. 以下のAI的で不自然な無難フレーズは一切使用禁止。
{NG_PHRASES}

出力形式: プレーンテキストのみ（前後の挨拶や「はい、分かりました」等のメタ発言は一切不要）
"""

    messages = [
        {"role": "system", "content": "あなたは指定されたキャラクターになりきって、プレーンテキストの短い挨拶を書くプロです。"},
        {"role": "user", "content": prompt}
    ]
    
    logger.info(f"[Curator] Generating intro column by {reviewer['name']}...")
    text, err = _call_deepseek_raw(messages, max_tokens=1000, temperature=0.7, thinking_disabled=True)
    if err != "ok" or not text:
        logger.error("[Curator] Failed to generate intro column. Using fallback greeting.")
        return reviewer['greeting']
        
    return text.strip()

# === AI執筆：ミニレビュー生成 ===
def generate_mini_review(work, tag_name, reviewer):
    """作品のテーマ特化ミニレビューをAIで生成する（セリフ、見出し、解説の3段構成）"""
    display_tag = tag_name.replace(",", "と")
    
    # 伏字処理
    safe_title = mask_input(work['title'], level=0)
    safe_desc = mask_input(work['description'] or "", level=0)

    # v21.5.2: ボイス作品は通常記事と同じ視聴済み装い禁止ルールを適用（SPEC 6-4）
    voice_rules = ""
    if "voice" in str(work.get("genre") or "").lower():
        voice_rules = (
            "\n7. ※音声作品のため「コマ」「見開き」「絵」「描画」「読む」等の漫画・小説表現は使わないこと。"
            "\n※AIは実際に音声を聴くことができないため、「聴いてみたら〜だった」「耳元の囁きが〜だった」等の"
            "【視聴済みを装う一人称の体験表現】は絶対禁止。"
            "\n※ただし以下はすべてOK："
            "①「ぜひ聴いてみて！」「イヤホン必須！」等の読者へのレコメンド表現、"
            "②「想像しただけで鳥肌が立つ」等の期待・想像を語る表現、"
            "③あらすじ・紹介文に明記されているセリフ・CV名・収録情報等の音声関連事実の引用・参照。"
        )
    
    prompt = f"""あなたは「Novelove」のライター「{reviewer['name']}」です。
以下の作品あらすじを読み、「{display_tag}」というテーマでおすすめする「セリフ」「見出し」「解説」をそれぞれ執筆してください。

【あなたの設定】
性格: {reviewer['personality']}
口調: {reviewer['tone']}

【対象作品】
作品名: {safe_title}
あらすじ: {safe_desc}
作品の属性タグ: {','.join(work['tags'])}

【執筆ルール】
1. 出力は必ず指定の【出力フォーマット】の通りに「[セリフ]」「[見出し]」「[解説]」というマーカーを使って3つのブロックに分けてください。
2. 「[セリフ]」ブロックのルール:
   - キャラクターの口調全開で語る短い一言。
   - 文字数は50〜80字以内（1〜2文）とし、吹き出しの圧迫感を絶対に防いでください。
3. 「[見出し]」ブロックのルール:
   - テーマである「{display_tag}」に関連した、作品の特定の魅力や二人の関係性の要約を表すキャッチーな見出し。
   - 文字数は15〜25文字程度（1文）で、HTMLタグやマークダウン記号は含めずプレーンテキストで出力してください。
4. 「[解説]」ブロックのルール:
   - キャラクターの口調は一切使用せず、一般的な丁寧語「です・ます調」を使用して客観的な第三者視点で書いてください。
   - 作品全体の一般的なあらすじ紹介や、全体のストーリー要約は個別記事で解説するため、【絶対に出力禁止】とします。
   - あらすじから読み取れる事実のみに基づき、今回のテーマである「{display_tag}」という属性・要素が、作品内でどのように魅力的に描かれているかだけをピンポイントで論理的に解説してください。
   - 文字数は120〜150字以内（2〜3文）とし、2文ごとに必ず改行（空行）を挟んで読みやすく段落分けしてください。
5. あらすじに存在しない設定、キャラクターの名前、詳細な展開を創作（ハルシネーション）することは絶対に禁止です。
{FACT_GUARD}
6. 以下の無難フレーズは使用禁止です。
{NG_PHRASES}
{voice_rules}

【出力フォーマット】
[セリフ]
（ここに短いキャラクター口調のセリフ）
[見出し]
（ここにキャッチーな魅力見出し）
[解説]
（ここにです・ます調によるテーマ特化した詳しい解説）
"""

    messages = [
        {"role": "system", "content": "あなたは指定されたフォーマットで、[セリフ]、[見出し]、[解説]の3ブロックを正確に執筆し分けるプロです。"},
        {"role": "user", "content": prompt}
    ]
    
    logger.info(f"[Curator] Generating mini review for '{work['title']}'...")
    text, err = _call_deepseek_raw(messages, max_tokens=1000, temperature=0.7, thinking_disabled=True)
    if err != "ok" or not text:
        logger.error(f"[Curator] Failed to generate review for {work['title']}. Using default synopsis snippet.")
        return f"[セリフ]\nおすすめの作品だよ！\n[見出し]\nこの作品の見どころ\n[解説]\n{(work['description'] or '')[:150]}..."
        
    return text.strip()

# === 吹き出しHTMLラッパー ===
def wrap_speech_bubble(text, reviewer):
    """プレーンテキストを speech-bubble HTMLに変換する（改行のHTML変換対応）"""
    face_img = reviewer['face_image']
    name = reviewer['name']
    
    # 吹き出し内での自動改行をサポート
    formatted_text = str(text).replace("\n", "<br />")
    
    return (
        f'<div class="speech-bubble-left">\n'
        f'  <img src="/wp-content/uploads/icons/{face_img}.png" alt="{name}" />\n'
        f'  <div class="speech-text">{formatted_text}</div>\n'
        f'</div>'
    )

# === 比較テーブルHTMLの組み立て ===
def build_comparison_table(works, conn):
    """5作品の比較テーブルHTMLを生成する"""
    table_style = (
        'width:100%; border-collapse:collapse; margin:30px 0; font-size:0.95em; '
        'box-shadow:0 2px 5px rgba(0,0,0,0.05); border-radius:8px; overflow:hidden;'
    )
    th_style = 'background-color:#ffebf2; color:#d81b60; font-weight:bold; padding:12px; text-align:left; border:1px solid #ffcfdf;'
    td_style = 'padding:12px; border:1px solid #eee; text-align:left;'
    
    html = f'<div style="overflow-x:auto;">\n<table style="{table_style}">\n<thead>\n<tr>\n'
    html += f'<th style="{th_style}">作品タイトル</th>\n'
    html += f'<th style="{th_style}">メディア</th>\n'
    html += f'<th style="{th_style}">配信ストア</th>\n'
    html += f'<th style="{th_style}">主な属性</th>\n'
    html += '</tr>\n</thead>\n<tbody>\n'
    
    for w in works:
        # メディア形式判定
        g_lower = w['genre'].lower()
        if "voice" in g_lower:
            media = "ボイス"
        elif "novel" in g_lower:
            media = "小説"
        else:
            media = "漫画"
            
        # タイトルリンク
        # DBにwp_post_urlがある場合はそこへの内部リンクとし、無ければアフィリエイトリンクへのフォールバック
        cur = conn.cursor()
        cur.execute("SELECT wp_post_url FROM novelove_posts WHERE product_id = ?", (w['product_id'],))
        db_row = cur.fetchone()
        cur.close()
        
        post_url = db_row[0] if db_row and db_row[0] else w['affiliate_url']
        # 相対パスの場合はサイトURLと結合
        if post_url.startswith("/"):
            post_url = WP_SITE_URL + post_url
        
        # 内部リンク=同タブ、外部リンク（アフィリエイト）=新タブ+nofollow
        is_internal = post_url.startswith(WP_SITE_URL) or post_url.startswith("/")
        if is_internal:
            title_link = f'<a href="{post_url}" style="color:#d81b60; font-weight:bold; text-decoration:none;">{w["title"]}</a>'
        else:
            title_link = f'<a href="{post_url}" target="_blank" rel="nofollow" style="color:#d81b60; font-weight:bold; text-decoration:none;">{w["title"]}</a>'
        
        # ストア表示
        site_raw = w['site']
        site_display = site_raw.split(":")[0] if isinstance(site_raw, str) and ":" in site_raw else str(site_raw)
        if site_display == "Lovecal":
            site_display = "らぶカル"
            
        # タグ（上位3つ）
        display_tags = " ".join([f"#{t}" for t in w['tags'][:3]])
        
        html += '<tr>\n'
        html += f'<td style="{td_style}">{title_link}</td>\n'
        html += f'<td style="{td_style}">{media}</td>\n'
        html += f'<td style="{td_style}">{site_display}</td>\n'
        html += f'<td style="{td_style}">{display_tags}</td>\n'
        html += '</tr>\n'
        
    html += '</tbody>\n</table>\n</div>\n'
    return html

# === フッターHTMLの組み立て ===
def get_tag_slug_from_wp(name):
    """WordPress REST API から日本語タグ名に対応する英字スラッグを取得する"""
    from novelove_core import WP_USER, WP_APP_PASSWORD, WP_SITE_URL
    auth = (WP_USER, WP_APP_PASSWORD)
    try:
        import requests
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/tags", auth=auth, params={"search": name}, timeout=15)
        hits = r.json()
        for hit in hits:
            if hit.get("name") == name:
                return hit.get("slug")
    except Exception as e:
        logger.error(f"[Curator] Failed to get slug for tag '{name}': {e}")
    return None

def build_footer(tag_name):
    """アーカイブリンクを含むフッターHTMLを生成する (関連記事はYARPPが自動表示するため含めない)"""
    # 1. アーカイブへの誘導
    tags_list = tag_name.split(",")
    archive_links = []
    for t in tags_list:
        slug = get_tag_slug_from_wp(t)
        if slug:
            archive_links.append(f'<a href="/tag/{slug}/" style="color:#d81b60; font-weight:bold; text-decoration:none;">#{t}の作品一覧</a>')
        else:
            # 万が一取得できない場合は安全のために元のタグ名でフォールバック
            import urllib.parse
            escaped = urllib.parse.quote(t)
            archive_links.append(f'<a href="/tag/{escaped}/" style="color:#d81b60; font-weight:bold; text-decoration:none;">#{t}の作品一覧</a>')
    
    links_html = "・".join(archive_links)
    html = (
        f'<div class="curation-footer" style="margin-top:50px; padding:20px; background:#fafafa; border-radius:8px; border-left:4px solid #d81b60;">\n'
        f'<p style="font-weight:bold; margin-bottom:10px;">もっと作品を探すならこちら</p>\n'
        f'<p style="margin-bottom:15px;">今回ご紹介した属性の作品は、以下のリンクからさらに詳しく探すことができます！</p>\n'
        f'<p style="font-size:1.1em; margin-bottom:0px;">👉 {links_html}</p>\n'
        f'</div>\n'
    )
    return html

# === 記事全体の組み立て ===
def assemble_article(intro_html, works, reviews_html, table_html, footer_html, display_tag="", display_genre=""):
    """各パーツを統合して1つのWordPress投稿用本文HTMLを組み立てる"""
    # speech-bubble スタイルはテーマの style.css に定義済みのため、
    # インライン <style> は出力しない（重複CSS防止・メンテナンス性向上）
    content = ""
    content += f"<!-- INTRO START -->\n{intro_html}\n<!-- INTRO END -->\n\n"
    content += f"<h2>「{display_tag}」のおすすめ{display_genre}作品{len(works)}選</h2>\n"
    content += f"<p>露出は控えめながら、あらすじや設定から非常に高いポテンシャルを感じる魅力的な{len(works)}作品をご紹介します。</p>\n\n"
    
    for i, w in enumerate(works):
        num = i + 1
        # 作品バッジ表示
        g_lower = w['genre'].lower()
        is_voice = "voice" in g_lower
        is_novel = "novel" in g_lower
        
        # v21.5.2: ランキング／SPEC 6-4 と統一（📖漫画 / 📝小説 / 🎧ボイス）
        media_icon = "📖"
        if is_voice: media_icon = "🎧"
        elif is_novel: media_icon = "📝"
        
        site_raw = w['site']
        site_display = site_raw.split(":")[0] if isinstance(site_raw, str) and ":" in site_raw else str(site_raw)
        if site_display == "Lovecal": site_display = "らぶカル"
        
        format_name = "漫画"
        if is_voice: format_name = "ボイス"
        elif is_novel: format_name = "小説"
        
        badge_html = f'<p style="text-align:center; margin-bottom:15px;"><span style="background:#fefefe; border:1px solid #ddd; padding:5px 15px; border-radius:20px; font-weight:bold; color:#444; display:inline-block;">{media_icon} {site_display} {format_name}</span></p>'
        
        # 画像
        if w.get('image_url'):
            img_html = f'<p style="text-align:center; margin:20px 0;"><a href="{w["affiliate_url"]}" target="_blank" rel="nofollow"><img src="{w["image_url"]}" alt="{w["title"]}" style="max-width:400px;width:100%;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.15);" /></a></p>'
        else:
            img_html = ""
        
        # 発売日
        release_html = ""
        if w.get('release_date'):
            try:
                rd = w['release_date'][:10].replace("-", "/")
                release_html = f'<p style="text-align:center; color:#666; font-size:0.9em; margin-bottom:10px;">発売日：{rd}</p>'
            except:
                pass
                
        # 試し読み・セール導線リンク
        action_verb = "試し聴き" if is_voice else "試し読み"
        link_text = f"▶ 『{w['title']}』の{action_verb}・お得なセール状況をチェック！"
        text_link = f'<p style="text-align:center; font-weight:bold; margin-top:5px; margin-bottom:15px;"><a href="{w["affiliate_url"]}" target="_blank" rel="nofollow" style="text-decoration:none; color:#d81b60;">{link_text}</a></p>'
        
        # 属性タグの一覧表示
        tags_display = " ".join([f"#{t}" for t in w['tags'][:4]])
        tags_html = f'<p style="text-align:center; color:#888; font-size:0.9em; margin-bottom:20px;">属性: {tags_display}</p>'
        
        # ボタン
        btn_label = "無料で試し聴きする" if is_voice else "無料で試し読みする"
        btn_html = get_affiliate_button_html(w['affiliate_url'], btn_label)
        
        # 結合
        content += f"<h3>{num}. {w['title']}</h3>\n"
        content += badge_html + "\n"
        content += img_html + "\n"
        content += release_html + "\n"
        content += text_link + "\n"
        content += tags_html + "\n"
        content += "<!-- REVIEW START -->\n" + reviews_html[i] + "\n<!-- REVIEW END -->\n\n"
        # 内部リンク（個別レビュー記事への誘導）
        if w.get('wp_post_url'):
            content += f'<p style="text-align:center; margin:10px 0 15px;"><a href="{w["wp_post_url"]}" style="color:#d81b60; font-weight:bold; text-decoration:none;">📖 この作品の個別紹介を見る →</a></p>\n'
        content += btn_html + "\n"
        content += '<hr style="border:0; border-top:1px dashed #ddd; margin:40px 0;" />\n\n'
        
    content += f"<h2>「{display_tag}」作品の比較スペック</h2>\n"
    content += f"<p>今回ご紹介した{len(works)}作品のスペック比較です。お好みのメディア形式や配信ストアから選ぶ際の参考にしてください。</p>\n"
    content += table_html + "\n\n"
    content += footer_html
    return content

# === メイン処理 ===
def main():
    # 修正3: 緊急停止チェック
    if is_emergency_stop():
        logger.info("🚨 [Curator] 緊急停止中のためスキップ。解除: rm emergency_stop.lock")
        return

    parser = argparse.ArgumentParser(description="ノベラブ・テーマ別まとめ記事自動生成バッチ")
    parser.add_argument("--force", action="store_true", help="週番号ローテを使わず強制実行する（--genre または --tag 必須）")
    parser.add_argument("--genre", choices=["BL", "TL", "cross"], help="ジャンルグループを強制指定する")
    parser.add_argument("--tag", help="特定のタグ（カンマ区切りで複数も可）を強制指定する")
    parser.add_argument("--dry-run", action="store_true", help="WordPressに投稿せずローカルHTML出力のみ行う")
    args = parser.parse_args()

    # v21.5.2: --force はデッドフラグにしない。週判定スキップには --genre/--tag が必要（本番cronも両方指定）
    if args.force and not args.genre and not args.tag:
        logger.error("[Curator] --force には --genre=BL|TL|cross または --tag の指定が必要です")
        return
    if args.force:
        logger.info("[Curator] --force モード: 週番号によるジャンル自動判定を使わず指定ジャンル/タグで実行します")

    # 修正4: 他プロセスとの排他制御（dry-run時はスキップ）
    if not args.dry_run:
        if os.path.exists(MAIN_LOCK_FILE):
            mtime = os.path.getmtime(MAIN_LOCK_FILE)
            if _time.time() - mtime > 7200:
                logger.warning("🚨 [Curator] メインロックが2時間を超えています。強制解除します。")
                release_lock(MAIN_LOCK_FILE)
            else:
                logger.info("🕒 [Curator] メイン投稿処理が実行中のためスキップ")
                return
        if os.path.exists(RANK_LOCK_FILE):
            mtime = os.path.getmtime(RANK_LOCK_FILE)
            if _time.time() - mtime > 7200:
                logger.warning("🚨 [Curator] ランキングロックが2時間を超えています。強制解除します。")
                release_lock(RANK_LOCK_FILE)
            else:
                logger.info("🕒 [Curator] ランキング処理が実行中のためスキップ")
                return
        # 原子的キュレーションロック取得 (v21.3.0)
        if not acquire_lock(CURATION_LOCK_FILE, stale_timeout=3600):
            logger.info("🕒 [Curator] 既に実行中です。終了します。")
            return

    try:
        _run_curator_logic(args)
    finally:
        if not args.dry_run:
            release_lock(CURATION_LOCK_FILE)


def _run_curator_logic(args):
    """まとめ記事生成の実処理"""
    # 修正13: 処理時間計測
    _start = _time.time()

    logger.info("==========================================================")
    logger.info("[Curator] Curation Article Generator started.")
    logger.info("==========================================================")

    # 接続
    conn = db_connect(DB_FILE_UNIFIED)

    # 週番号とジャンルの決定
    week = _get_week_number()
    logger.info(f"[Curator] Calculated week number: {week}")

    # テーマと作品の選定
    tag_name, selected_works, genre_group = select_theme_and_works(
        conn, week, forced_tag=args.tag, forced_genre=args.genre
    )

    if not tag_name or not selected_works:
        mode = args.genre or args.tag or f"week{week}-auto"
        logger.error(
            f"[Curator] No theme or works could be selected. Process aborted. (mode={mode})"
        )
        notify_discord(
            f"⚠️ **まとめ記事をスキップしました（候補ゼロ）**\n"
            f"指定モード: `{mode}` / 週番号: 第{week}週\n"
            f"原因: クールダウン外かつ未出演5本以上のタグ/ペアが見つかりませんでした。\n"
            f"（個別タグの未出演不足スキップは正常動作。全候補が尽きたときだけこの通知が出ます）",
            username="📚 まとめ記事投稿くん",
        )
        conn.close()
        return

    logger.info(f"[Curator] Selected Tag: {tag_name}")
    logger.info(f"[Curator] Genre Group: {genre_group}")
    logger.info(f"[Curator] Selected works: {[w['title'] for w in selected_works]}")

    # 修正18: 各作品の内部リンク(wp_post_url)を事前取得
    for w in selected_works:
        cur = conn.cursor()
        cur.execute("SELECT wp_post_url FROM novelove_posts WHERE product_id = ?", (w['product_id'],))
        row = cur.fetchone()
        wp_url = row[0] if row and row[0] else None
        if wp_url and wp_url.startswith("/"):
            wp_url = WP_SITE_URL + wp_url
        w['wp_post_url'] = wp_url
        cur.close()

    # レビュアーの決定
    # BL: 紫苑 (shion) / 葵 (aoi) / 蓮 (ren) からランダム
    # TL: 桃香 (momoka) / 茉莉花 (marika) からランダム
    if "bl" in genre_group.lower():
        candidates = [r for r in REVIEWERS if r['id'] in ("shion", "aoi", "ren")]
    else:
        candidates = [r for r in REVIEWERS if r['id'] in ("momoka", "marika")]

    reviewer = random.choice(candidates)
    logger.info(f"[Curator] Selected reviewer: {reviewer['name']} ({reviewer['id']})")

    # AIコンテンツの生成
    # 1. 導入コラムの生成
    intro_text = generate_intro_column(reviewer, tag_name, genre_group)
    intro_html = wrap_speech_bubble(intro_text, reviewer)

    # 2. 各作品ミニレビューの生成（修正10: AI失敗時の安全弁）
    reviews_html = []
    ai_fail_count = 0
    for w in selected_works:
        rev_text = generate_mini_review(w, tag_name, reviewer)
        if rev_text.endswith("..."):  # フォールバック検知
            ai_fail_count += 1
            
        # [セリフ]、[見出し]、[解説] でパースする
        bubble_text = ""
        heading_text = ""
        detail_text = ""
        
        if "[見出し]" in rev_text and "[解説]" in rev_text:
            parts_heading = rev_text.split("[見出し]")
            bubble_text = parts_heading[0].replace("[セリフ]", "").strip()
            
            parts_detail = parts_heading[1].split("[解説]")
            heading_text = parts_detail[0].strip()
            detail_text = parts_detail[1].strip()
        elif "[解説]" in rev_text:  # 見出しが欠落した場合のフォールバック
            parts = rev_text.split("[解説]")
            bubble_text = parts[0].replace("[セリフ]", "").strip()
            detail_text = parts[1].strip()
            heading_text = ""
        else:
            # すべて崩れた場合の極限フォールバック
            bubble_text = rev_text.replace("[セリフ]", "").strip()
            detail_text = ""
            heading_text = ""
            
        # 1. 吹き出し（セリフのみ）の生成
        bubble_html = wrap_speech_bubble(bubble_text, reviewer)
        
        # 2. 見出しの生成 (目次除外のため <h3> ではなく装飾付き <div> を使用)
        heading_html = ""
        if heading_text:
            clean_heading = heading_text.replace("#", "").replace("[見出し]", "").strip()
            if clean_heading:
                heading_html = (
                    f'<div style="font-size: 1.15em; font-weight: bold; background: #f5f5f5; '
                    f'border-left: 4px solid #888; padding: 10px 15px; margin: 20px 0 15px; '
                    f'color: #333; border-radius: 2px; line-height: 1.4;">{clean_heading}</div>'
                )
                
        # 3. 通常の解説テキストの生成 (pタグ、です・ます調)
        detail_html = ""
        if detail_text:
            formatted_detail = detail_text.replace("\n", "<br />")
            # 通常記事のレイアウトと100%統一するために p タグを使用
            detail_html = f"<p>{formatted_detail}</p>"
            
        # 3段を美しく結合
        rev_html = bubble_html
        if heading_html:
            rev_html += "\n" + heading_html
        if detail_html:
            rev_html += "\n" + detail_html
            
        reviews_html.append(rev_html)

    if ai_fail_count >= 2:
        logger.error(f"[Curator] AI生成が{ai_fail_count}件失敗。品質確保のため投稿を中止します。")
        notify_discord(
            f"🚨 **まとめ記事のAI生成に{ai_fail_count}件失敗しました**\n"
            f"テーマ: {tag_name}\n投稿は中止されました。",
            username="🚨 警告通知"
        )
        conn.close()
        return

    # 3. 比較テーブルの組み立て
    table_html = build_comparison_table(selected_works, conn)

    # 4. フッターの組み立て
    footer_html = build_footer(tag_name)

    # 修正16: display_tag/display_genreをassemble_article呼び出し前に定義
    display_tag = tag_name.replace(",", "×")
    display_genre = "BL" if "bl" in genre_group.lower() else "TL"

    # 5. 全体の組み立て（修正16: キーワード付きH2、修正17: 動的件数）
    full_content = assemble_article(
        intro_html, selected_works, reviews_html, table_html, footer_html,
        display_tag=display_tag, display_genre=display_genre
    )

    # 修正17: タイトルの決定（動的件数）
    num = len(selected_works)
    title = f"【まとめ】「{display_tag}」属性の隠れた名作{display_genre}作品{num}選"

    # 修正6: まとめ記事専用のメタディスクリプション
    excerpt_tags = tag_name.split(",") + [reviewer['name']]  # レビュアー名を追加（通常・ランキング記事と統一）
    excerpt = (
        f"「{display_tag}」属性のおすすめ{display_genre}作品を厳選{num}選！"
        f"Noveloveの{reviewer['name']}が、露出は低くとも魅力的な隠れた名作を"
        f"テーマ特化の視点でご紹介します。"
    )
    if len(excerpt) > 120:
        excerpt = excerpt[:118] + "…"

    # 修正7: SEOタイトル（Google日本語表示枠: 約30〜35文字）
    seo_title = f"【まとめ】「{display_tag}」属性の隠れた名作{display_genre}作品{num}選"
    if len(seo_title) > 35:
        seo_title = f"「{display_tag}」の隠れた名作{display_genre}{num}選"
        if len(seo_title) > 35:
            seo_title = seo_title[:33] + "…"

    # 修正9: スラッグの生成（時分まで含めて重複防止）
    date_str = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    is_eng_tag = bool(re.match(r'^[a-zA-Z0-9\-_,]+$', tag_name))
    sub_genre_lower = "bl" if "bl" in genre_group.lower() else "tl"
    if is_eng_tag:
        slug_tag = tag_name.replace(",", "-").lower()
    else:
        slug_tag = f"w{week}"

    # 先頭を bl-curation / tl-curation の正順かつ英語スラッグで統一
    slug = f"{sub_genre_lower}-curation-{slug_tag}-{date_str}"
    # v21.5.2: 長すぎる場合もジャンル接頭辞を落さない（旧 curation-{date}-{rand} は禁止）
    if len(slug) > 100:
        slug = f"{sub_genre_lower}-curation-{date_str}-{random.randint(1000, 9999)}"

    # 修正2: FIFUサムネイル変換（A+C方式: 本文用=大きい画像、FIFU用=軽量サムネ）
    full_image_url = selected_works[0]['image_url']
    from auto_post import _get_thumbnail_url
    thumb_url = _get_thumbnail_url(full_image_url)

    # ドライラン判定
    if args.dry_run:
        logger.info("[Curator] Dry-run enabled. Saving HTML output locally.")
        output_file = os.path.join(SCRIPT_DIR, f"dry_run_{date_str}.html")
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"<h1>{title}</h1>\n")
                f.write(f"<p><strong>SEO Title:</strong> {seo_title}</p>\n")
                f.write(f"<p><strong>Meta Description:</strong> {excerpt}</p>\n")
                f.write(f"<p><strong>Slug:</strong> {slug}</p>\n")
                f.write("<hr />\n")
                f.write(full_content)
            logger.info(f"[Curator] HTML output saved to: {output_file}")
            print(f"\n[DRY RUN SUCCESS] Output saved to: {output_file}\n")
        except Exception as e:
            logger.error(f"[Curator] Failed to save dry-run output: {e}")
    else:
        logger.info("[Curator] Publishing curation article to WordPress...")
        # 遅延インポートによる循環参照の防止
        from auto_post import post_to_wordpress

        # 投稿ジャンルの設定 (bl-curation / tl-curation)
        post_genre = f"{sub_genre_lower}-curation"

        # 修正2: image_url と thumb_url を分離
        link, wp_post_id = post_to_wordpress(
            title=title,
            content=full_content,
            genre=post_genre,
            image_url=full_image_url,   # 記事本文用（大きい画像）
            excerpt=excerpt,
            seo_title=seo_title,
            slug=slug,
            is_r18=True,  # まとめは基本R18属性作品も含むためTrue
            site_label=None,
            ai_tags=excerpt_tags,
            reviewer=reviewer['name'],
            thumb_url=thumb_url          # FIFU用（軽量サムネ）
        )

        if wp_post_id:
            logger.info(f"[Curator] Published successfully! ID: {wp_post_id}, URL: {link}")

            # v21.5.2: WPが -2 を付けた場合に備え、実URLのスラッグを product_id にする
            db_product_id = slug
            if link:
                m_slug = re.search(r"https?://[^/]+/([^/]+)/?", str(link))
                if m_slug and m_slug.group(1):
                    db_product_id = m_slug.group(1)
                    if db_product_id != slug:
                        logger.warning(f"[Curator] WPスラッグが意図と異なります: intended={slug} actual={db_product_id}")

            # WPタグはテーマ + レビュアー（v21.1.1）。ai_tags / original_tags はテーマのみ
            wp_tags_val = ",".join([t for t in excerpt_tags if t])
            featured_ids_csv = ",".join(w["product_id"] for w in selected_works)

            # 修正8: DB INSERT改善（不要カラム削除、正確なデータ保存）
            _ensure_curation_work_ids_column(conn)
            c = conn.cursor()
            try:
                c.execute("""
                    INSERT INTO novelove_posts (
                        product_id, title, genre, site, status, published_at, post_type,
                        wp_post_id, wp_post_url, reviewer, wp_tags, ai_tags,
                        article_pattern, image_url, is_protected, source_db,
                        original_tags, description, curation_work_ids
                    ) VALUES (?, ?, ?, 'Novelove', 'published', datetime('now', 'localtime'), 'curation',
                              ?, ?, ?, ?, ?,
                              'C', ?, 1, 'curation',
                              ?, ?, ?)
                """, (
                    db_product_id, title, post_genre,
                    wp_post_id, link, reviewer['name'], wp_tags_val, tag_name,
                    full_image_url,
                    tag_name, excerpt, featured_ids_csv
                ))
                
                # === [v21.4.1] まとめ記事に選出された5作品を自動的に永久保護 (is_protected = 1) ===
                for work in selected_works:
                    c.execute(
                        "UPDATE novelove_posts SET is_protected = 1 WHERE product_id = ?",
                        (work['product_id'],)
                    )
                    logger.info(f"[Curator] Protected work: {work['title']} (ID: {work['product_id']})")
                
                conn.commit()
                logger.info("[Curator] Curation post details saved and 5 works marked as protected in DB.")
            except Exception as e:
                # 修正11: DB INSERT失敗時のDiscord通知
                logger.error(f"[Curator] Failed to save curation details to DB: {e}")
                notify_discord(
                    f"🚨 **まとめ記事のDB保存に失敗しました**\n"
                    f"WP投稿ID: `{wp_post_id}` / URL: {link}\n"
                    f"**エラー**: {e}\n"
                    f"手動でDBへの登録が必要です。",
                    username="🚨 警告通知"
                )

            # 修正1: キャッシュクリア（auto_post.py と同一処理）
            try:
                subprocess.Popen(
                    "kusanagi bcache clear myblog && kusanagi fcache clear myblog",
                    shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                logger.info("  [Cache] KUSANAGI bcache/fcache クリアをバックグラウンドで実行")
            except Exception as cache_err:
                logger.warning(f"  [Cache] キャッシュクリア失敗（続行）: {cache_err}")

            # 修正12: Discord通知（ジャンル・週・作品数を追加）
            disc_msg = (
                f"📝 **テーマ別まとめ記事を自動投稿しました**\n"
                f"**タイトル**: {title}\n"
                f"**テーマ（タグ）**: {tag_name}\n"
                f"**ジャンル**: {genre_group} / 第{week}週\n"
                f"**選定作品数**: {len(selected_works)}件\n"
                f"**担当レビュアー**: {reviewer['name']}\n"
                f"**URL**: {link}"
            )
            notify_discord(disc_msg, username="📚 まとめ記事投稿くん")
        else:
            logger.error("[Curator] WordPress post failed.")
            notify_discord("🚨 **テーマ別まとめ記事の投稿に失敗しました**", username="🚨 警告通知")

    conn.close()
    # 修正13: 処理時間ログ
    elapsed = _time.time() - _start
    logger.info(f"[Curator] Process finished. (所要時間: {elapsed:.1f}秒)")
    logger.info("==========================================================")

if __name__ == '__main__':
    main()

