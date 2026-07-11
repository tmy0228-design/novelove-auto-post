#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novelove_ranking.py — Novelove ランキング記事生成モジュール
週次ランキングの取得・記事生成・投稿を担当
"""
import os
import re
import time
import random
import urllib.parse
import requests
from bs4 import BeautifulSoup
from datetime import datetime

from novelove_soul import REVIEWERS, get_relationship, FACT_GUARD, NG_PHRASES, MOOD_PATTERNS

from novelove_core import (
    logger, notify_discord,
    _get_reviewer_for_genre, _genre_label,
    get_db_path, get_source_db, db_connect, init_db,
    WP_SITE_URL, RANK_LOCK_FILE, MAIN_LOCK_FILE,
    is_emergency_stop,
    DMM_API_ID, DMM_AFFILIATE_API_ID, DMM_AFFILIATE_LINK_ID,
    DLSITE_AFFILIATE_ID,
    generate_affiliate_url,
    acquire_lock, release_lock,
)

from novelove_fetcher import (
    mask_input,
    scrape_description,
    _is_noise_content,
)

from novelove_writer import _call_deepseek_raw

# post_to_wordpress は auto_post.py に残る（循環import回避のため関数内で遅延importする）

# === ランキング用メディア判定・アイコン衛生 ===
_LOVECAL_VOICE_GENRE_KW = ("バイノーラル", "ASMR", "ボイス", "ボイスドラマ", "KU100", "3Dサウンド", "シチュエーションボイス")
_LOVECAL_NOVEL_GENRE_KW = ("小説", "ノベル")


def _detect_lovecal_media_type(item, db_genre=None):
    """らぶカル作品の media_type を判定する（v21.5.1）。

    優先順（過去DB検証済み）:
      1. 画像/商品URLの `/digital/voice/` パス（公式の種別パス。例外ほぼゼロ）
      2. 自社DBに既存の通常記事があればその genre 接頭辞
      3. APIジャンル名の音声/小説キーワード
      4. デフォルト comic
    ※ `/digital/novel/` はらぶカルではほぼ使われず小説も comic パスになるため、
      小説判定は 2 or 3 に依存する。
    """
    img = (item.get("imageURL") or {}).get("large") or (item.get("imageURL") or {}).get("list") or ""
    url = item.get("URL") or ""
    blob = f"{img} {url}".lower()
    if "/digital/voice/" in blob:
        return "voice"

    if db_genre:
        g = str(db_genre).lower()
        if g.startswith("voice"):
            return "voice"
        if g.startswith("novel"):
            return "novel"
        if g.startswith(("comic", "doujin")):
            return "comic"

    genres = [g.get("name", "") for g in (item.get("iteminfo") or {}).get("genre", [])]
    genres_str = " ".join(genres)
    if any(k in genres_str for k in _LOVECAL_VOICE_GENRE_KW):
        return "voice"
    if any(k in genres_str for k in _LOVECAL_NOVEL_GENRE_KW):
        return "novel"
    return "comic"


def _lookup_db_genre_by_product_id(product_id):
    """通常記事として published 済みなら genre を返す。なければ None。"""
    if not product_id:
        return None
    try:
        # site 横断の統合DBを参照（ランキングの site 引数に依存しない）
        conn = db_connect(get_db_path())
        row = conn.execute(
            "SELECT genre FROM novelove_posts WHERE product_id=? AND status='published' AND post_type='regular' LIMIT 1",
            (product_id,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return row[0] if not isinstance(row, dict) else row.get("genre")
    except Exception as e:
        logger.warning(f"  [media_type] DBジャンル参照失敗 ({product_id}): {e}")
        return None


def _force_ranking_speech_icons(html, reviewer, guest=None):
    """ランキングAI出力の吹き出しアイコンを担当者画像に強制上書きする（v21.5.1）。

    通常記事の writer サニタイザーと同等の保護。AIが外部URLやゴミパスを
    src に入れても、left=MC / right=ゲスト の正規アイコンへ置換する。
    """
    mc = reviewer.get("face_image", "momoka")
    guest_face = (guest or reviewer).get("face_image", mc)
    mc_src = f"/wp-content/uploads/icons/{mc}.png"
    guest_src = f"/wp-content/uploads/icons/{guest_face}.png"
    mc_alt = reviewer.get("name", "")
    guest_alt = (guest or reviewer).get("name", mc_alt)

    # writer と同様: 日本語・英語ファイル名のゆれを正規パスへ
    _ICON_NAMES = r"(葵|紫苑|桃香|蓮|茉莉花|shion|marika|aoi|momoka|ren)"
    html = re.sub(
        rf'src="[^"]*?{_ICON_NAMES}\.png"',
        r'src="/wp-content/uploads/icons/\1.png"',
        html,
    )

    soup = BeautifulSoup(html, "html.parser")
    changed = 0
    for div in soup.select(".speech-bubble-left, .speech-bubble-right"):
        img = div.find("img")
        if img is None:
            continue
        is_left = "speech-bubble-left" in (div.get("class") or [])
        want_src = mc_src if is_left else guest_src
        want_alt = mc_alt if is_left else guest_alt
        cur = img.get("src") or ""
        if cur != want_src:
            img["src"] = want_src
            changed += 1
        if want_alt and img.get("alt") != want_alt:
            img["alt"] = want_alt
    if changed:
        logger.info(f"  [ランキング] 吹き出しアイコンを強制修正: {changed}件")
    return str(soup)


# === ランキング記事 ===
def fetch_ranking_dmm(site, genre):
    """v15.0: Lovecal対応 ＆ アナログな交互表示(偏り)の廃止。
    らぶカルは統合ランキングをそのまま取得。
    FANZA/DMMは漫画3枠・小説2枠を取得し、1位は漫画固定、残りはシャッフル。
    """
    is_bl = (genre == "BL")
    
    if site == "Lovecal":
        items = []
        params = {
            "api_id": DMM_API_ID, "affiliate_id": DMM_AFFILIATE_API_ID,
            "site": "FANZA", "service": "doujin", 
            "floor": "digital_doujin_bl" if is_bl else "digital_doujin_tl",
            "hits": 10, "sort": "rank", "output": "json",
        }
        try:
            r = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=params, timeout=15)
            if r.status_code == 200:
                for item in r.json().get("result", {}).get("items", []):
                    title = item.get("title", "")
                    if _is_noise_content(title, ""): continue
                    items.append(item)
                    if len(items) >= 5: break
        except Exception as e:
            logger.error(f"Lovecal API Fetch Error ({genre}): {e}")
            
        final_items = []
        for item in items:
            title = item.get("title", "")
            base_url = item.get("URL", "")
            # generate_affiliate_url が lovecul.dmm.co.jp を検知し自動でDMM用URLに変換する
            aff_url = generate_affiliate_url("FANZA", base_url)
            desc, *_ = scrape_description(item.get("URL", ""), site=site, genre=genre, is_ranking=True)
            if _is_noise_content(title, desc): continue
            
            # v21.5.1: 画像パス → DBジャンル → APIジャンルKW の順で判定
            cid = item.get("content_id", "")
            db_genre = _lookup_db_genre_by_product_id(cid)
            media_type = _detect_lovecal_media_type(item, db_genre=db_genre)
            logger.info(f"  [Lovecal media] {cid} -> {media_type} (db_genre={db_genre})")

            final_items.append({
                "title": title, "url": aff_url,
                "image_url": item.get("imageURL", {}).get("large", ""),
                "description": desc,
                "content_id": cid,
                "media_type": media_type
            })
        return final_items

    # --- FANZA / DMM.com の場合 ---
    comic_items = []
    novel_items = []
    
    for dtype in ["comic", "novel"]:
        params = {
            "api_id": DMM_API_ID, "affiliate_id": DMM_AFFILIATE_API_ID,
            "hits": 10, "sort": "rank", "output": "json",
        }
        floor = "novel" if dtype == "novel" else "comic"
        art_id = ("66042" if dtype == "novel" else "66036") if is_bl else ("66064" if dtype == "novel" else "66060")
        params.update({"site": "DMM.com", "service": "ebook", "floor": floor, "article": "category", "article_id": art_id})

        try:
            r = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=params, timeout=15)
            if r.status_code == 200:
                for item in r.json().get("result", {}).get("items", []):
                    title = item.get("title", "")
                    if _is_noise_content(title, ""): continue
                    item['_dtype'] = dtype
                    if dtype == "comic":
                        comic_items.append(item)
                    else:
                        novel_items.append(item)
        except Exception as e:
            logger.error(f"DMM API Fetch Error ({site}/{genre}/{dtype}): {e}")

    # 漫画3枠、小説2枠を抽出してシャッフル
    selected = []
    if comic_items:
        selected.append(comic_items[0]) # 1位固定
    
    pool = []
    for c in comic_items[1:3]: pool.append(c)
    for n in novel_items[:2]:  pool.append(n)
    random.shuffle(pool)
    
    selected.extend(pool)
    
    final_items = []
    for item in selected:
        title = item.get("title", "")
        base_url = item.get("URL", "")
        # generate_affiliate_url が FANZA/DMM.com を自動判定してURLを生成する
        aff_url = generate_affiliate_url(site, base_url)
        desc, *_ = scrape_description(item.get("URL", ""), site=site, genre=genre, is_ranking=True)
        if _is_noise_content(title, desc): continue
        
        final_items.append({
            "title": title, "url": aff_url,
            "image_url": item.get("imageURL", {}).get("large", ""),
            "description": desc,
            "content_id": item.get("content_id", ""),
            "media_type": item.get("_dtype", "comic")
        })
        if len(final_items) >= 5: break
        
    return final_items

def _fetch_dlsite_ranking_items_from_url(url, is_bl, limit, skip_titles=None):
    if skip_titles is None:
        skip_titles = []
    items = []
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            for anchor in soup.select('table#ranking_table .work_name a')[:30]: # 多めにチェック
                title = anchor.text.strip()
                link = anchor.get('href')
                if _is_noise_content(title, ""): continue
                if title in skip_titles: continue
                img_src = ""; desc = ""
                is_r18_badge = False
                media_type = "comic"
                try:
                    dr = requests.get(link, headers=headers, timeout=10)
                    if dr.status_code == 200:
                        dsoup = BeautifulSoup(dr.text, 'html.parser')
                        badges = [wg.get('href', '') for wg in dsoup.select('.work_genre a')]
                        badges_str = str(badges)
                        # 漫画(MNG) or 小説(NRE/NVL/TOW) or ボイス(SOU)のみ許可
                        if not any(b in badges_str for b in ['MNG', 'NRE', 'NVL', 'TOW', 'SOU']):
                            continue
                        # メディアタイプ判定
                        if 'SOU' in badges_str: media_type = "voice"
                        elif any(x in badges_str for x in ['NRE', 'NVL', 'TOW']): media_type = "novel"
                        else: media_type = "comic"
                        
                        og_img = dsoup.select_one('meta[property="og:image"]')
                        if og_img: img_src = og_img.get('content', '')
                        desc_tag = dsoup.select_one('meta[property="og:description"]')
                        if desc_tag: desc = desc_tag.get('content', '')
                        if _is_noise_content(title, desc): continue
                        
                        # R-18バッジ判定でアフィURLのfloorを決定 (v20.0.1)
                        is_r18_badge = bool(dsoup.select_one(".icon_ADL"))
                except Exception as e:
                    logger.warning(f"  [DLsite詳細取得失敗] {title[:20]}: {e}")
                    continue
                aff_id = DLSITE_AFFILIATE_ID
                pid = link.rstrip("/").split("/")[-1].replace(".html", "")
                
                # アフィURLの全年齢判定 (v20.0.1)
                if is_r18_badge:
                    floor = "bl" if is_bl else "girls"
                else:
                    floor = "home"
                    
                aff_url = f"https://dlaf.jp/{floor}/dlaf/=/t/n/link/work/aid/{aff_id}/id/{pid}.html"
                items.append({"title": title, "url": aff_url, "image_url": img_src, "description": desc, "content_id": pid, "media_type": media_type})
                if len(items) >= limit: break
    except Exception as e:
        logger.error(f"DLsite Ranking URL Fetch Error ({url}): {e}")
    return items

def fetch_ranking_dlsite(genre):
    """v21.5.0: DLsiteランキングはR-18 女性向け（BL/TL）専用ページのみを使用。

    旧v20.0.1では全年齢 `home/ranking/week?is_bl=1(is_tl=1)` を2件混ぜていたが、
    DLsiteはこのクエリを無視し男性向け一般ASMR等が混入する事故が発生したため全廃。
    女性向けであることが担保された `/bl/ranking/week`・`/girls/ranking/week` のみで
    TOP5を構成する。
    """
    is_bl = (genre == "BL")
    r18_path = "bl/ranking/week" if is_bl else "girls/ranking/week"
    r18_url = f"https://www.dlsite.com/{r18_path}"
    items = _fetch_dlsite_ranking_items_from_url(r18_url, is_bl, limit=5)
    return items[:5]


def format_ranking_prompt(site_name, genre, items, reviewer, guest=None):
    """
    v12.0.0: ランキング記事を2名の掛け合い形式で生成するプロンプトを作成。
    reviewer = メインMC（専門担当者）
    guest    = ゲスト（別ジャンル担当者）。Noneの場合は1名形式にフォールバック。
    v14.9.0: 挨拶ランダム化・吹き出しクラス厳格化・FACT_GUARD/NG_PHRASES/成人向け表現規制を追加。
    """
    items_xml = ""
    for idx, item in enumerate(items):
        desc = mask_input(item.get("description", ""), level=1)[:300]
        items_xml += f'\n<item rank="{idx+1}">\n  <title>{item["title"]}</title>\n  <description>{desc}...</description>\n</item>\n'

    # MC（左）の吹き出し
    mc_open  = f'<div class="speech-bubble-left"><img src="/wp-content/uploads/icons/{reviewer["face_image"]}.png" alt="{reviewer["name"]}" /><div class="speech-text">'
    mc_close = '</div></div>'

    if guest is None:
        # フォールバック: 旧来の1名形式（v14.9.0: 挨拶ランダム化・ガード追加）
        if random.random() < 0.1:
            intro_rule = f"冒頭の挨拶では、あなたのキャラクター設定にある身の上話（例: {reviewer.get('greeting', '')}）を自然に絡めてください。"
        else:
            intro_rule = "冒頭の挨拶では、身の上話・自己紹介は一切しないこと。今週のランキングへの期待感や驚きを、あなたの口調だけで語り出すこと。毎回違う切り口で始めること。"
        return f'''あなたは「{reviewer["name"]}」として、今週の{site_name}における{genre}総合人気ランキング（漫画＋小説）TOP5を紹介するアフィリエイト記事を執筆してください。
【キャラクター設定: {reviewer["name"]}】
・性格: {reviewer["personality"]}
・文体: {reviewer["tone"]}
【執筆ルール】HTML形式で出力してください。
・直接的な性的単語（性器の名称・行為の直接名称）は使用禁止。官能的な比喩を使うこと。
※紹介する作品には漫画、小説、音声作品（ボイス・ASMR）が含まれます。メディアタイプを特定する表現（「読む」「聴く」「本を開く」「耳を澄ます」など）は避け、どのメディアであっても違和感のない中立的な表現（「この作品を楽しむ」「チェックする」「体験する」など）で紹介してください。
1. 冒頭キャラコメント
{mc_open}（{intro_rule} 60〜80字以内）{mc_close}
2. ランキングTOP5（各作品につき紹介文＋推しポイント吹き出し）
[IMAGE_{{rank}}] [RANK_BADGE_{{rank}}] [TITLE_{{rank}}] [REVIEW_LINK_{{rank}}]
3. 締めキャラコメント
{mc_open}（振り返りと読者への呼びかけ。100〜120字以内）{mc_close}
【ランキングデータ】
{items_xml}
{FACT_GUARD}{NG_PHRASES}
'''

    # ゲスト（右）の吹き出し
    guest_open  = f'<div class="speech-bubble-right"><img src="/wp-content/uploads/icons/{guest["face_image"]}.png" alt="{guest["name"]}" /><div class="speech-text">'
    guest_close = '</div></div>'

    # 2人の関係性テキストを取得
    relationship = get_relationship(reviewer["id"], guest["id"])

    if random.random() < 0.1:
        mc_intro_rule   = f"{reviewer['name']}は冒頭で自分の身の上話（例: {reviewer.get('greeting', '')}）を自然に絡めて語り出すこと。"
        guest_intro_rule = f"{guest['name']}は自分の日常のエピソードや近況で自然に返すこと。"
    else:
        mc_intro_rule   = f"{reviewer['name']}は身の上話・自己紹介は一切禁止。今週のピックアップ作品への期待感や驚きを口調そのままで語り出すこと。毎回違う切り口で始めること。"
        guest_intro_rule = f"{guest['name']}も同様に、身の上話・自己紹介は一切禁止。2人の関係性に沿った自然なリアクションで返すこと。毎回違うリアクションにすること。"

    # 今週の感情モード（会話全体のトーンを決める）
    weekly_mood = random.choice(MOOD_PATTERNS)

    # v15.0: 全サイト「厳選ピックアップ」コンセプトへ統一
    return f'''今回は「{reviewer["name"]}」（メインMC）と「{guest["name"]}」（ゲスト）の2人の対話形式で、今週の{site_name}における{genre}の中から、ノベラブ編集部が特におすすめしたい厳選ピックアップ5作品を、独自のランキング形式で紹介するアフィリエイト記事を執筆してください。
【今週の会話トーン】
{weekly_mood}
【メインMC: {reviewer["name"]}】
・性格: {reviewer["personality"]}
・文体: {reviewer["tone"]}
【ゲスト: {guest["name"]}】
・性格: {guest["personality"]}
・文体: {guest["tone"]}
【2人の関係性】
{relationship}
【執筆の最重要ルール（必ず守ること）】
1. 吹き出しHTMLと通常テキスト（あらすじ・見どころ紹介）を適切に組み合わせて執筆すること。
2. {reviewer["name"]}の発言には必ず「メインMC吹き出し（左）」を使用すること:
{mc_open}（セリフ）{mc_close}
3. {guest["name"]}の発言には必ず「ゲスト吹き出し（右）」を使用すること:
{guest_open}（セリフ）{guest_close}
4. 【絶対禁止】{guest["name"]}の発言に speech-bubble-left クラスを使うことは絶対禁止。{reviewer["name"]}の発言に speech-bubble-right クラスを使うことも絶対禁止。
5. 【文字数厳守】各吹き出しの発言は必ず60文字以上・90文字以内（最大100文字厳守）。長文をダラダラと話すのは絶対禁止。
6. 2人の性格の違いと関係性に基づいた自然なテンポで会話を進めること。
7. raw HTMLのみを出力。```やコードブロックは使わないこと。
8. 直接的な性的単語（性器の名称・行為の直接名称）は使用禁止。官能的な比喩を使うこと。
9. 紹介する作品には漫画、小説、音声作品（ボイス・ASMR）が含まれます。メディアタイプを特定する表現（「読む」「聴く」「本を開く」「耳を澄ます」など）は避け、どのメディアであっても違和感のない中立的な表現（「この作品を楽しむ」「チェックする」「体験する」など）で紹介してください。
【冒頭の挨拶ルール】
{mc_intro_rule}
{guest_intro_rule}
【記事の構成（合計文字数目標: 3,000〜3,500文字）】
- 冒頭：2人のオープニングトーク（今週の作品への期待を簡潔に語る。合計2往復。）
- 第5位〜第2位：各作品ごとに以下の順で構成すること（吹き出しは計2往復＝4発言のみ）:
  1. MCが作品への第一印象を吹き出し1発言で語る
  2. 通常テキスト（<p>タグ）であらすじ・見どころを箇条書き（<ul><li>）2〜3点で紹介（吹き出し不使用）
  3. ゲストがリアクション・推しポイントを吹き出し1発言で語る
  4. MCが締めのひと言を吹き出し1発言で語る
  ・各作品の前後に必ず HTML プレースホルダーを置くこと:
    [MEDIA_BADGE_{{rank}}]
    [IMAGE_{{rank}}]
    <div class="ranking-badge" style="font-size:1.6em;font-weight:bold;margin-bottom:15px;color:#ff4785;">[RANK_BADGE_{{rank}}]</div>
    <h3 style="margin-top:20px;font-size:1.3em;">[TITLE_{{rank}}]</h3>
    [REVIEW_LINK_{{rank}}]
- 第1位：少し熱量多めで語る（吹き出しは計3往復＝6発言）。プレースホルダーは同様に配置。
- 締め：2人で今週の感想と読者へのメッセージを語る（合計2往復）。
【ランキングデータ】
{items_xml}
{FACT_GUARD}{NG_PHRASES}
'''

def _post_ranking_article_to_wordpress(title, content, genre, site_name, top_image_url="", excerpt="", reviewer_name="", guest_name=""):
    from auto_post import post_to_wordpress  # 循環import回避
    # v21.5.0: 固定スラグ運用。既存記事があればWP側を上書き更新する（overwrite=True）。
    slug = get_ranking_slug(site_name, genre)
    
    tags_to_add = []
    if guest_name:
        tags_to_add.append(guest_name)
        
    wp_url, _wp_id = post_to_wordpress(
        title=title, content=content, genre=genre, image_url=top_image_url,
        excerpt=excerpt, seo_title=f"{title} | Novelove",
        slug=slug, is_r18=False, site_label=site_name,
        reviewer=reviewer_name, ai_tags=tags_to_add, overwrite=True
    )
    if wp_url:
        logger.info(f"✅ ランキング投稿成功: {wp_url}")
        try:
            db_path = get_db_path(site_name)
            conn = db_connect(db_path)
            c = conn.cursor()
            # v21.5.0: 固定スラグは毎週上書き更新するため、published_at を最新化する必要がある。
            # （クールダウン判定が「今週更新済みか」を published_at で判定するため）
            # まずUPDATEを試み、対象行が無ければINSERTする（ai_tags等を消さないためON REPLACEは使わない）。
            c.execute("""UPDATE novelove_posts
                SET title=?, wp_post_url=?, published_at=datetime('now', 'localtime'), reviewer=?
                WHERE product_id=? AND post_type='ranking'""",
                (title, wp_url, reviewer_name, slug))
            if c.rowcount == 0:
                c.execute("""INSERT INTO novelove_posts
                    (product_id, title, genre, site, status, post_type, wp_post_url, published_at, reviewer, source_db)
                    VALUES (?, ?, ?, ?, 'published', 'ranking', ?, datetime('now', 'localtime'), ?, ?)""",
                    (slug, title, genre, site_name, wp_url, reviewer_name, get_source_db(site_name)))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"  ランキングDB記録エラー: {e}")
        return True
    return False

def get_ranking_slug(site, genre):
    # v21.5.0: 週次日付スラグを廃止し「固定スラグ」に統一。
    # 例: dlsite-bl-ranking / lovecal-tl-ranking / dmm-bl-ranking
    # 毎週新規作成せず、同一URLの記事を上書き更新することでSEO評価を1本に集約する。
    return f"{site.lower()}-{genre.lower()}-ranking"

def process_ranking_articles(force_all=False):
    """force_all=True の場合、曜日スケジュールを無視して DLsite/らぶカル/DMM の
    BL・TL 全6本を一括生成/更新する（v21.5.0: 固定スラグ移行時の初回一括生成用の特別モード）。
    通常のcron運用では force_all=False（曜日ごとに1サイト、BL→TLの2回起動）。"""
    from auto_post import post_to_wordpress  # 循環import回避

    # ★ 緊急停止チェック
    if is_emergency_stop():
        return

    logger.info("=" * 60)
    logger.info("ランキング記事自動生成モード開始")

    # メイン投稿処理との排他チェック
    if os.path.exists(MAIN_LOCK_FILE):
        mtime = os.path.getmtime(MAIN_LOCK_FILE)
        if time.time() - mtime > 7200:
            logger.warning("🚨 通常投稿のロックが2時間を超えています。強制解除します。")
            release_lock(MAIN_LOCK_FILE)
        else:
            logger.info("🕒 通常投稿処理が実行中のためランキングはスキップします。")
            return

    # 原子的ランキングロック取得
    if not acquire_lock(RANK_LOCK_FILE, stale_timeout=7200):
        logger.info("🕒 ランキング処理は既に実行中です。終了します。")
        return

    try:
        # 曜日判定 (0=月, 1=火, 2=水, 3=木, 4=金, 5=土, 6=日)
        weekday = datetime.now().weekday()
        # スケジュール (v19.5.0): 水曜日はランキング非投稿日。
        # 優先度: DLsite（最高・土曜最強枠）> らぶカル（金曜週末前）> DMM（日曜週末締め）
        schedule = {4: "Lovecal", 5: "DLsite", 6: "DMM"}
        
        if force_all:
            # v21.5.0: 特別モード。曜日制限を無視して全サイトを対象にする。
            sites = ["DLsite", "Lovecal", "DMM"]
            logger.info("🔧 強制全生成モード: 曜日制限を無視して6本すべてを生成/更新します")
        else:
            target_site = schedule.get(weekday)
            if not target_site:
                logger.info(f"今日はランキング投稿日ではありません (曜日コード: {weekday})")
                return
            sites = [target_site]
        medals = {1: "🥇 1位", 2: "🥈 2位", 3: "🥉 3位", 4: "4位", 5: "5位"}
        site_labels = {"DMM": "DMM.com", "DLsite": "DLsite", "Lovecal": "らぶカル"}
        
        for i, site in enumerate(sites):
            logger.info(f"--- ランキング処理: {site} ---")
            for genre in ["BL", "TL"]:
                logger.info(f"  [{genre}総合] 取得開始...")
                
                # --- v21.5.0: 固定スラグ運用に伴うクールダウンロジック ---
                # 固定スラグは毎週上書きするため、「行の有無」ではなく
                # published_at が今週（ISO週）に属するかで二重投稿を防止する。
                slug = get_ranking_slug(site, genre)
                db_path = get_db_path(site)
                conn = db_connect(db_path)
                c = conn.cursor()
                row = c.execute("SELECT published_at FROM novelove_posts WHERE product_id=? AND post_type='ranking'", (slug,)).fetchone()
                conn.close()
                if row and row[0] and not force_all:
                    try:
                        _pub = datetime.strptime(str(row[0])[:19], "%Y-%m-%d %H:%M:%S")
                        _now_iso = datetime.now().isocalendar()
                        _pub_iso = _pub.isocalendar()
                        already_this_week = (_pub_iso[0] == _now_iso[0] and _pub_iso[1] == _now_iso[1])
                    except Exception:
                        already_this_week = False
                    if already_this_week:
                        logger.info(f"  [{genre}総合] 今週の {site} {genre} は既に更新済み（{slug}）。スキップします。")
                        continue

                if site in ("DMM", "Lovecal"):
                    items = fetch_ranking_dmm(site, genre)
                elif site == "DLsite":
                    items = fetch_ranking_dlsite(genre)
                else:
                    logger.warning(f"  [{site}] 未対応サイトのためスキップ")
                    continue
                    
                if len(items) < 5:
                    logger.warning(f"  -> データ不足のためスキップ (取得数: {len(items)})")
                    continue

                top_image_url = items[0].get("image_url", "")
                reviewer, _ = _get_reviewer_for_genre(genre)
                # v12.0.0: ゲストレビュアーをMC以外のREVIEWERSからランダムに選出
                guest_candidates = [r for r in REVIEWERS if r["id"] != reviewer["id"]]
                guest = random.choice(guest_candidates) if guest_candidates else None
                logger.info(f"  [ランキング] MC={reviewer['name']} / ゲスト={guest['name'] if guest else 'なし'}")
                prompt = format_ranking_prompt(site, genre, items, reviewer, guest=guest)

                
                messages = [
                    {"role": "system", "content": "あなたは優秀なアフィリエイトブロガーです。"},
                    {"role": "user", "content": prompt}
                ]
                
                content_html = ""
                for attempt in range(3):
                    html_text, err = _call_deepseek_raw(messages, max_tokens=16000, temperature=0.7)  # v17.8.11: 8000→16000（DeepSeek V4 Flashの実際の上限は384Kトークン。余裕を持って設定）
                    if err == "ok" and html_text:
                        content_html = html_text
                        break
                    elif err == "rate_limit":
                        logger.warning(f"  [ランキング] OpenRouter レート制限 → 30秒待機")
                        time.sleep(30)
                    else:
                        logger.warning(f"  [ランキング] 試行{attempt+1} 失敗 ({err})")
                        time.sleep(5)

                if not content_html:
                    logger.error(f"  -> AI生成失敗（リトライ上限到達）")
                    continue

                db_path = get_db_path(site)
                conn = db_connect(db_path)
                c = conn.cursor()
                for idx, item in enumerate(items):
                    rank = idx + 1
                    content_html = content_html.replace(f"[RANK_BADGE_{rank}]", medals.get(rank, f"{rank}位"))
                    content_html = content_html.replace(f"[TITLE_{rank}]", item["title"])
                    # メディアタイプバッジの差し込み
                    _badge_map = {
                        "comic":  '<div style="display:inline-block;padding:3px 10px;border-radius:20px;font-size:0.82em;font-weight:bold;margin-bottom:8px;background:#ffe0ef;color:#ff6b9d;">📖 漫画</div>',
                        "novel":  '<div style="display:inline-block;padding:3px 10px;border-radius:20px;font-size:0.82em;font-weight:bold;margin-bottom:8px;background:#f0e6ff;color:#9b59b6;">📝 小説</div>',
                        "voice":  '<div style="display:inline-block;padding:3px 10px;border-radius:20px;font-size:0.82em;font-weight:bold;margin-bottom:8px;background:#e6f3ff;color:#3498db;">🎧 ボイス</div>',
                        "doujin": '<div style="display:inline-block;padding:3px 10px;border-radius:20px;font-size:0.82em;font-weight:bold;margin-bottom:8px;background:#fff3e0;color:#e67e22;">📦 同人</div>',
                    }
                    _media = item.get("media_type", "comic")
                    badge_elem = _badge_map.get(_media, _badge_map["comic"])
                    badge_centered = f'<div style="text-align:center; margin-bottom:8px;">{badge_elem}</div>'
                    content_html = content_html.replace(f"[MEDIA_BADGE_{rank}]", badge_centered)
                    img_elem = f'<div style="text-align: center;"><a href="{item["url"]}" target="_blank" rel="noopener"><img src="{item["image_url"]}" alt="{item["title"]}" style="max-height: 400px; max-width: 100%; object-fit: contain; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);" /></a></div>'
                    link_text = "を試聴する" if _media == "voice" else "を試し読みする"
                    text_link_elem = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:10px; margin-bottom:15px;"><a href="{item["url"]}" target="_blank" rel="nofollow" style="text-decoration:none; color:#d81b60;">▶ 『{item["title"]}』{link_text}</a></p>'
                    content_html = content_html.replace(f"[IMAGE_{rank}]", f"{img_elem}{text_link_elem}")
                    
                    pid = item.get("content_id", "")
                    if not pid:
                        # DLsite のパターンを考慮
                        m_pid = re.search(r"(product_id/|ID=)([^/?&]+)", item["url"])
                        if m_pid: pid = m_pid.group(2).replace(".html", "")
                    
                    internal_link_html = ""
                    if pid:
                        row = c.execute("SELECT wp_post_url FROM novelove_posts WHERE product_id=? AND status='published'", (pid,)).fetchone()
                        if row and row[0]:
                            internal_link_html = f'<p style="text-align:center; font-size:0.9em; margin-top:-10px; margin-bottom:20px;"><a href="{row[0]}" style="color:#d81b60; text-decoration:none;">📝 詳しい紹介記事はこちら</a></p>'
                    content_html = content_html.replace(f"[REVIEW_LINK_{rank}]", internal_link_html)
                conn.close()

                content_html = re.sub(r"^```html\n?", "", content_html, flags=re.MULTILINE)
                content_html = re.sub(r"^```\n?", "", content_html, flags=re.MULTILINE)

                # v21.5.1: AIが吹き出しアイコンを捏造しても担当者画像へ強制上書き
                content_html = _force_ranking_speech_icons(content_html, reviewer, guest)
                
                # v17.8.10: _wrap_html_block を削除（writer.py v17.8.3 revert と同様）
                # 各speech-bubbleをwp:htmlで個別ラップするとGutenbergが大量ブロックを生成しページ崩れの原因になる

                css_injection = """<!-- wp:html -->
<style>
.speech-bubble-left, .speech-bubble-right {
    display: flex !important;
    align-items: flex-start !important;
    margin-bottom: 24px !important;
    gap: 16px !important;
    clear: both !important;
}
.speech-bubble-right {
    flex-direction: row-reverse !important;
}
.speech-bubble-left img, .speech-bubble-right img {
    width: 60px !important;
    height: 60px !important;
    min-width: 60px !important;
    border-radius: 50% !important;
    object-fit: cover !important;
    margin: 0 !important;
}
.speech-bubble-left .speech-text, .speech-bubble-right .speech-text {
    background: #fff0f5 !important;
    padding: 16px !important;
    border-radius: 8px !important;
    border: 1px solid #ffb6c1 !important;
    position: relative !important;
    max-width: 80% !important;
    line-height: 1.6 !important;
}
.speech-bubble-right .speech-text {
    background: #f0f8ff !important;
    border: 1px solid #add8e6 !important;
}
.speech-bubble-left .speech-text::before {
    content: "" !important; position: absolute !important; top: 20px !important;
    left: -12px !important; right: auto !important;
    border-width: 6px 12px 6px 0 !important; border-style: solid !important;
    border-color: transparent #ffb6c1 transparent transparent !important;
}
.speech-bubble-left .speech-text::after {
    content: "" !important; position: absolute !important; top: 20px !important;
    left: -10px !important; right: auto !important;
    border-width: 6px 12px 6px 0 !important; border-style: solid !important;
    border-color: transparent #fff0f5 transparent transparent !important;
}
.speech-bubble-right .speech-text::before {
    content: "" !important; position: absolute !important; top: 20px !important;
    right: -12px !important; left: auto !important;
    border-width: 6px 0 6px 12px !important; border-style: solid !important;
    border-color: transparent transparent transparent #add8e6 !important;
}
.speech-bubble-right .speech-text::after {
    content: "" !important; position: absolute !important; top: 20px !important;
    right: -10px !important; left: auto !important;
    border-width: 6px 0 6px 12px !important; border-style: solid !important;
    border-color: transparent transparent transparent #f0f8ff !important;
}
@media (max-width:600px) {
    .speech-bubble-left img, .speech-bubble-right img {
        width: 40px !important;
        height: 40px !important;
        min-width: 40px !important;
    }
}
</style>
<!-- /wp:html -->
"""
                content_html = css_injection + content_html

                _now = datetime.now()
                _wk = (_now.day - 1) // 7 + 1
                title_date = f"{_now.year}年{_now.month}月第{_wk}週"
                genre_label_map = {"BL": "BL総合", "TL": "TL総合"}
                genre_full = genre_label_map.get(genre, genre)
                disp_site = site_labels.get(site, site)
                
                # v15.0: 全サイト統一で「厳選おすすめピックアップ」コンセプトに
                post_title = f"【{disp_site}】今週の{genre_full}おすすめピックアップ5選！ノベラブ厳選ランキング（{title_date}）"
                meta_desc = f"【{disp_site}】今週の{genre_full}の中から、ノベラブ編集部が厳選したおすすめ作品TOP5を{reviewer['name']}が熱く紹介！"
                
                final_content = content_html
                
                # 相互リンク (BL <=> TL)
                other_genre = "TL" if genre == "BL" else "BL"
                other_slug = get_ranking_slug(site, other_genre)
                other_url = f"{WP_SITE_URL}/{other_slug}/"
                _now2 = datetime.now()
                _wk2 = (_now2.day - 1) // 7 + 1
                cross_link = (
                    f'<div style="border:1px solid #f0c0c0; border-radius:8px; padding:15px; margin:20px 0; background:#fff8f8;">\n'
                    f'<p style="margin:0 0 8px; font-weight:bold; color:#c0607f;">📚 あわせて読みたい</p>\n'
                    f'<p><a href="{other_url}">【{disp_site}】{other_genre}ピックアップ5選！厳選ランキング（{_now2.year}年{_now2.month}月第{_wk2}週）はこちら</a></p>\n'
                    f'</div>\n'
                )
                final_content += cross_link
                
                # クレジット: らぶカルはFANZA同人APIなのでFANZAバナーを使用
                if "FANZA" in disp_site or site == "Lovecal":
                    ranking_credit = f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;"><a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/r18_135_17.gif" width="135" height="17" alt="WEB SERVICE BY FANZA" style="border:none;"></a></div>'
                elif "DMM" in disp_site:
                    ranking_credit = f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;"><a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/com_135_17.gif" width="135" height="17" alt="WEB SERVICE BY DMM.com" style="border:none;"></a></div>'
                else:
                    ranking_credit = f'<p style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee; font-size:0.8em; color:#bbb;">PRESENTED BY {disp_site} / Novelove Affiliate Program</p>'
                
                final_content += ranking_credit
                
                logger.info(f"  -> {genre} 投稿実行中...")
                guest_n = guest["name"] if guest else ""
                _post_ranking_article_to_wordpress(post_title, final_content, genre, site, top_image_url, excerpt=meta_desc, reviewer_name=reviewer["name"], guest_name=guest_n)
                
                # BL投稿完了後はプロセスを即時終了する（ステートレス設計）
                # TLは次回のCron起動時に「BLは投稿済み」と判定されて自動処理される
                # ⚠️ Cron設定注意: ランキング処理日はBL・TL各1回ずつ合計2回以上スクリプトを起動すること
                # force_all時は途中終了せず、全サイト・全ジャンルを続けて処理する。
                if genre == "BL" and not force_all:
                    logger.info("BLランキング投稿完了。TLは次回のCron起動で自動処理されます。")
                    return
    finally:
        release_lock(RANK_LOCK_FILE)
    logger.info("ランキング記事自動生成モード終了")
    logger.info("=" * 60)

