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
    DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET,
    _get_reviewer_for_genre, _genre_label,
    get_db_path, db_connect, init_db,
    WP_SITE_URL, RANK_LOCK_FILE,
    is_emergency_stop,
    DMM_API_ID, DMM_AFFILIATE_API_ID, DMM_AFFILIATE_LINK_ID,
    DLSITE_AFFILIATE_ID, DIGIKET_AFFILIATE_ID,
    generate_affiliate_url,
)

from novelove_fetcher import (
    mask_input,
    scrape_description,
    scrape_digiket_description,
    _is_noise_content,
)

from novelove_writer import _call_deepseek_raw

# post_to_wordpress は auto_post.py に残る（循環import回避のため関数内で遅延importする）

# === ランキング記事 ===
def fetch_ranking_dmm_fanza(site, genre):
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
            desc = scrape_description(item.get("URL", ""), site=site, genre=genre)
            if _is_noise_content(title, desc): continue
            
            final_items.append({
                "title": title, "url": aff_url,
                "image_url": item.get("imageURL", {}).get("large", ""),
                "description": desc,
                "content_id": item.get("content_id", "")
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
        if site == "FANZA":
            if dtype == "novel":
                params.update({"site": "FANZA", "service": "ebook", "floor": "bl" if is_bl else "tl", "keyword": "小説"})
            else:
                params.update({"site": "FANZA", "service": "ebook", "floor": "bl" if is_bl else "tl"})
        else:
            floor = "novel" if dtype == "novel" else "comic"
            art_id = ("66042" if dtype == "novel" else "66036") if is_bl else ("66064" if dtype == "novel" else "66060")
            params.update({"site": "DMM.com", "service": "ebook", "floor": floor, "article": "category", "article_id": art_id})

        try:
            r = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=params, timeout=15)
            if r.status_code == 200:
                for item in r.json().get("result", {}).get("items", []):
                    title = item.get("title", "")
                    if _is_noise_content(title, ""): continue
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
        desc = scrape_description(item.get("URL", ""), site=site, genre=genre)
        if _is_noise_content(title, desc): continue
        
        final_items.append({
            "title": title, "url": aff_url,
            "image_url": item.get("imageURL", {}).get("large", ""),
            "description": desc,
            "content_id": item.get("content_id", "")
        })
        if len(final_items) >= 5: break
        
    return final_items

def fetch_ranking_dlsite(genre):
    """v11.2.0: DLsiteの総合ランキングから漫画・小説のみ抽出"""
    items = []
    is_bl = (genre == "BL")
    path = "bl/ranking/week" if is_bl else "girls/ranking/week"
    url = f"https://www.dlsite.com/{path}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            for anchor in soup.select('table#ranking_table .work_name a')[:30]: # 多めにチェック
                title = anchor.text.strip()
                link = anchor.get('href')
                if _is_noise_content(title, ""): continue
                img_src = ""; desc = ""
                try:
                    dr = requests.get(link, headers=headers, timeout=10)
                    if dr.status_code == 200:
                        dsoup = BeautifulSoup(dr.text, 'html.parser')
                        badges = [wg.get('href', '') for wg in dsoup.select('.work_genre a')]
                        # 漫画(MNG) or 小説(NRE/NVL/TOW)のみ許可
                        if not any(b in str(badges) for b in ['MNG', 'NRE', 'NVL', 'TOW']):
                            continue
                        og_img = dsoup.select_one('meta[property="og:image"]')
                        if og_img: img_src = og_img.get('content', '')
                        desc_tag = dsoup.select_one('meta[property="og:description"]')
                        if desc_tag: desc = desc_tag.get('content', '')
                        if _is_noise_content(title, desc): continue
                except Exception as e:
                    logger.warning(f"  [DLsite詳細取得失敗] {title[:20]}: {e}")
                    continue
                aff_id = DLSITE_AFFILIATE_ID
                pid = link.rstrip("/").split("/")[-1].replace(".html", "")
                floor = "bl" if is_bl else "girls"
                aff_url = f"https://dlaf.jp/{floor}/dlaf/=/t/n/link/work/aid/{aff_id}/id/{pid}.html"
                items.append({"title": title, "url": aff_url, "image_url": img_src, "description": desc})
                if len(items) >= 5: break
    except Exception as e:
        logger.error(f"DLsite Scraping Exception ({genre}): {e}")
    return items

def fetch_ranking_digiket(genre):
    """v11.2.0: DigiKetのランキングを取得"""
    items = []
    is_bl = (genre == "BL")
    seen_ids = set()
    # BLは専用ページ、TLは一般コミックまたはXML API(target=6)を活用
    if is_bl:
        url = "https://www.digiket.com/bl/ranking_week.php"
    else:
        # 乙女・TLは独立したランキングページがないため、一般コミックランキングからTLタグのあるものを抽出
        url = "https://www.digiket.com/comics/ranking_week.php"
        tl_keywords = ["TL", "ティーンズラブ", "乙女", "女性向け"]
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            r.encoding = 'cp932'
            soup = BeautifulSoup(r.text, 'html.parser')
            # ランキング上位30件からTL系を探す
            rank_items = []
            for anchor in soup.find_all("a", href=True):
                if "ID=ITM" not in anchor.get("href"): continue
                t = anchor.text.strip()
                if not t: continue
                # TL系キーワードが含まれるか、乙女向けラベルがあるか
                if any(kw in t for kw in tl_keywords) or "乙女" in str(anchor.parent):
                    link = anchor.get("href")
                    if not link.startswith("http"): link = "https://www.digiket.com" + link
                    _m = re.search(r"ID=(ITM\d+)", link)
                    if not _m: continue  # IDパターン不一致: AttributeError防止
                    itm_id = _m.group(1)
                    if itm_id in seen_ids: continue
                    seen_ids.add(itm_id)
                    desc, img, _, _, _, _ = scrape_digiket_description(link)
                    aff_url = link
                    if DIGIKET_AFFILIATE_ID:
                        if not aff_url.endswith("/"): aff_url += "/"
                        aff_url += f"AFID={DIGIKET_AFFILIATE_ID}/"
                    rank_items.append({"title": t, "url": aff_url, "image_url": img, "description": mask_input(desc, 1)})
                    if len(rank_items) >= 5: break
            if rank_items: return rank_items
        except Exception as e:
            logger.error(f"  [DigiKet TL Ranking] フォールバック失敗: {e}")
        
        # さらに見つからない場合は XML API (sort=new) を最終手段に使用
        xml_url = "https://api.digiket.com/xml/api/getxml.php?target=6&sort=new"
        try:
            r = requests.get(xml_url, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            for entry in soup.find_all("item")[:15]:
                title = entry.find("title").text
                link = entry.find("link").text
                # 詳細を取得
                desc, img, _, _, _, _ = scrape_digiket_description(link)
                # 漫画・小説のみ (DigiKetはカテゴリ名に文字列が含まれる)
                if not any(x in str(entry) for x in ["コミック", "小説", "マンガ", "ノベル"]):
                    continue
                aff_url = link
                if DIGIKET_AFFILIATE_ID:
                    if not aff_url.endswith("/"): aff_url += "/"
                    aff_url += f"AFID={DIGIKET_AFFILIATE_ID}/"
                items.append({"title": title, "url": aff_url, "image_url": img, "description": desc})
                if len(items) >= 5: return items
        except Exception as e:
            logger.warning(f"  [DigiKet XML API失敗] {e}")
        url = "https://www.digiket.com/comics/ranking_week.php" # フォールバック

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.encoding = 'cp932'
        soup = BeautifulSoup(r.text, 'html.parser')
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href")
            if "ID=ITM" not in href: continue
            # ID抽出
            itm_id = re.search(r"ID=(ITM\d+)", href)
            if not itm_id: continue
            pid = itm_id.group(1)
            
            title = anchor.text.strip()
            if not title or len(title) < 2: continue
            if pid in seen_ids: continue
            seen_ids.add(pid)
            
            link = href
            if not link.startswith("http"): link = "https://www.digiket.com" + link
            # 種別確認
            desc, img, _, _, _, _ = scrape_digiket_description(link)
            if not any(x in (title + desc) for x in ["コミック", "小説", "マンガ", "ノベル"]): continue
            aff_url = link
            if DIGIKET_AFFILIATE_ID:
                if not aff_url.endswith("/"): aff_url += "/"
                aff_url += f"AFID={DIGIKET_AFFILIATE_ID}/"
            items.append({"title": title, "url": aff_url, "image_url": img, "description": desc})
            if len(items) >= 5: break
    except Exception as e:
        logger.error(f"DigiKet Ranking Error ({genre}): {e}")
    return items

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
※当サイトは漫画・小説専門です。「聴く」「イヤホン」などの音声表現は避け、「読む・見る」体験として紹介してください。
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
1. 地の文は一切書かないこと。すべての文章を以下のどちらかの吹き出しHTMLで表現すること。
2. {reviewer["name"]}の発言には必ず「メインMC吹き出し（左）」を使用すること:
{mc_open}（セリフ）{mc_close}
3. {guest["name"]}の発言には必ず「ゲスト吹き出し（右）」を使用すること:
{guest_open}（セリフ）{guest_close}
4. 【絶対禁止】{guest["name"]}の発言に speech-bubble-left クラスを使うことは絶対禁止。{reviewer["name"]}の発言に speech-bubble-right クラスを使うことも絶対禁止。
5. 2人の性格の違いと関係性に基づいた自然なテンポで会話を進めること。
6. raw HTMLのみを出力。```やコードブロックは使わないこと。
7. 直接的な性的単語（性器の名称・行為の直接名称）は使用禁止。官能的な比喩を使うこと。
8. 当サイトは漫画・小説専門です。「聴く」「イヤホン」などの音声表現は避け、「読む・見る」体験として紹介してください。
【冒頭の挨拶ルール】
{mc_intro_rule}
{guest_intro_rule}
【記事の構成】
- 冒頭：2人のオープニングトーク（お互いに挨拶し、今週ピックアップされた注目作品への期待を語る。合計4〜6往復。）
- 第5位〜第2位：各作品ごとに、あらすじ説明（MC主導）→ゲストのリアクション→推しポイントの掘り下げ（最低3往復）
  ・各作品の前後に必ず HTML プレースホルダーを置くこと:
    [IMAGE_{{rank}}]
    <div class="ranking-badge" style="font-size:1.6em;font-weight:bold;margin-bottom:15px;color:#ff4785;">[RANK_BADGE_{{rank}}]</div>
    <h3 style="margin-top:20px;font-size:1.3em;">[TITLE_{{rank}}]</h3>
    [REVIEW_LINK_{{rank}}]
- 第1位：2人で熱量MAXに語り倒す（最低5往復）。プレースホルダーは同様に配置。
- 締め：2人で今週の感想と読者へのメッセージを語る（合計3〜4往復）。
【ランキングデータ】
{items_xml}
{FACT_GUARD}{NG_PHRASES}
'''

def _post_ranking_article_to_wordpress(title, content, genre, site_name, top_image_url="", excerpt="", reviewer_name="", guest_name=""):
    from auto_post import post_to_wordpress  # 循環import回避
    now = datetime.now()
    week = str((now.day - 1) // 7 + 1)
    slug = f"{site_name.lower()}-{genre.lower()}-ranking-{now.strftime('%Y')}-{now.strftime('%m')}-w{week}"
    
    tags_to_add = []
    if guest_name:
        tags_to_add.append(guest_name)
        
    wp_url, _wp_id = post_to_wordpress(
        title=title, content=content, genre=genre, image_url=top_image_url,
        excerpt=excerpt, seo_title=f"{title} | Novelove",
        slug=slug, is_r18=False, site_label=site_name,
        reviewer=reviewer_name, ai_tags=tags_to_add
    )
    if wp_url:
        logger.info(f"✅ ランキング投稿成功: {wp_url}")
        try:
            db_path = get_db_path(site_name)
            conn = db_connect(db_path)
            c = conn.cursor()
            # INSERT OR IGNORE: 同一スラグが既に存在する場合は上書きしない。
            # INSERT OR REPLACE だと DELETE+INSERT になり ai_tags 等が消失するリスクがある。
            c.execute("""INSERT OR IGNORE INTO novelove_posts
                (product_id, title, genre, site, status, post_type, wp_post_url, published_at, reviewer)
                VALUES (?, ?, ?, ?, 'published', 'ranking', ?, datetime('now', 'localtime'), ?)""",
                (slug, title, genre, site_name, wp_url, reviewer_name))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"  ランキングDB記録エラー: {e}")
        return True
    return False

def get_ranking_slug(site, genre):
    now = datetime.now()
    week = str((now.day - 1) // 7 + 1)
    return f"{site.lower()}-{genre.lower()}-ranking-{now.strftime('%Y')}-{now.strftime('%m')}-w{week}"

def process_ranking_articles():
    from auto_post import post_to_wordpress  # 循環import回避

    # ★ 緊急停止チェック
    if is_emergency_stop():
        return

    logger.info("=" * 60)
    logger.info("ランキング記事自動生成モード開始")

    try:
        with open(RANK_LOCK_FILE, "w") as f:
            f.write(datetime.now().isoformat())
    except Exception as e:
        logger.error(f"ランキングロック作成失敗: {e}")
        return
    try:
        # 曜日判定 (0=月, 1=火, 2=水, 3=木, 4=金, 5=土, 6=日)
        weekday = datetime.now().weekday()
        # スケジュール: 水=DigiKet, 木=DMM, 金=DLsite, 土=FANZA, 日=Lovecal
        schedule = {2: "DigiKet", 3: "DMM", 4: "DLsite", 5: "FANZA", 6: "Lovecal"}
        
        target_site = schedule.get(weekday)
        if not target_site:
            logger.info(f"今日はランキング投稿日ではありません (曜日コード: {weekday})")
            return

        sites = [target_site]
        medals = {1: "🥇 1位", 2: "🥈 2位", 3: "🥉 3位", 4: "4位", 5: "5位"}
        site_labels = {"FANZA": "FANZA", "DMM": "DMM.com", "DLsite": "DLsite", "DigiKet": "DigiKet", "Lovecal": "らぶカル"}
        
        for i, site in enumerate(sites):
            logger.info(f"--- ランキング処理: {site} ---")
            for genre in ["BL", "TL"]:
                logger.info(f"  [{genre}総合] 取得開始...")
                
                # --- v15.0: DB確認によるクールダウンロジック ---
                slug = get_ranking_slug(site, genre)
                db_path = get_db_path(site)
                conn = db_connect(db_path)
                c = conn.cursor()
                row = c.execute("SELECT published_at FROM novelove_posts WHERE product_id=? AND status='published'", (slug,)).fetchone()
                conn.close()
                if row:
                    logger.info(f"  [{genre}総合] 今週の {site} {genre} は既に投稿済み（{slug}）。スキップします。")
                    continue

                if site in ("FANZA", "DMM", "Lovecal"):
                    items = fetch_ranking_dmm_fanza(site, genre)
                elif site == "DLsite":
                    items = fetch_ranking_dlsite(genre)
                else:
                    items = fetch_ranking_digiket(genre)
                    
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
                    html_text, err = _call_deepseek_raw(messages, max_tokens=8000, temperature=0.7)  # v17.8.9: 6000→8000（2人対話×5作品で截断が発生していたため通常記事と同値に引き上げ）
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
                    img_elem = f'<div style="text-align: center;"><a href="{item["url"]}" target="_blank" rel="noopener"><img src="{item["image_url"]}" alt="{item["title"]}" style="max-height: 400px; max-width: 100%; object-fit: contain; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);" /></a></div>'
                    text_link_elem = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:10px; margin-bottom:15px;"><a href="{item["url"]}" target="_blank" rel="nofollow" style="text-decoration:none; color:#d81b60;">▶ 『{item["title"]}』を試し読みする</a></p>'
                    content_html = content_html.replace(f"[IMAGE_{rank}]", f"{img_elem}{text_link_elem}")
                    
                    pid = item.get("content_id", "")
                    if not pid:
                        # DLsite/DigiKet 両方のパターンを考慮
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
                
                # 自動整形のwpautop対策：Gutenberg HTMLブロックで括る＆安全な表示用CSS注入
                def _wrap_html_block(m):
                    t = m.group(0).strip()
                    return f"<!-- wp:html -->\n{t}\n<!-- /wp:html -->"
                content_html = re.sub(r'<div class="speech-bubble-(?:left|right)".*?</div>\s*</div>', _wrap_html_block, content_html, flags=re.DOTALL)

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
                if genre == "BL":
                    logger.info("BLランキング投稿完了。TLは次回のCron起動で自動処理されます。")
                    return
    finally:
        if os.path.exists(RANK_LOCK_FILE):
            try:
                os.remove(RANK_LOCK_FILE)
            except Exception as e:
                logger.error(f"ランキングロック削除失敗: {e}")
    logger.info("ランキング記事自動生成モード終了")
    logger.info("=" * 60)

