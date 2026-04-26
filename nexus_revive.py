#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
Novelove Nexus — 自動セール検知 & 売れ筋タグ管理エンジン v1.0.0
==========================================================
【概要】
  FANZA / DLsite / DigiKet の3サイトを巡回し、
  セール中作品 → 「🔥期間限定セール」タグ (slug: sale)
  ランキング上位 → 「🏆売れ筋作品」タグ (slug: best-seller)
  を既存の WordPress 記事に自動付与・自動剥奪する。

【安全設計】
  - 各サイトの取得処理は個別に隔離（1サイトの障害が他に波及しない）
  - すべてのエラーは Discord に即時通知
  - 記事本文・他タグには一切触れない（タグIDの差分更新のみ）

【実行タイミング】
  cron で 8:30 / 20:30 の1日2回実行を想定
==========================================================
"""

import os
import re
import sqlite3
import difflib
import requests
import time
from bs4 import BeautifulSoup
from datetime import datetime

# 環境変数・.envの読み込みは novelove_core.py で一元管理
from novelove_core import (
    logger,
    DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET, DB_FILE_UNIFIED,
    db_connect, notify_discord,
    WP_SITE_URL, HEADERS,
    WP_USER, WP_APP_PASSWORD,
    DMM_API_ID, DMM_AFFILIATE_API_ID,
)
from novelove_fetcher import scrape_description

# === 定数 ===
SALE_TAG_NAME     = "期間限定セール"
SALE_TAG_SLUG     = "sale"
BESTSELLER_TAG_NAME = "売れ筋作品"
BESTSELLER_TAG_SLUG = "best-seller"


# DigiKet XML API のターゲットID
DIGIKET_TARGETS = [
    {"target": "8", "label": "商業BL"},
    {"target": "6", "label": "商業TL"},
    {"target": "2", "label": "同人"},
]


# =====================================================================
# 1. WordPress タグ管理（REST API）
# =====================================================================
def _wp_auth():
    """WP REST API 用の認証タプルを返す。"""
    return (WP_USER, WP_APP_PASSWORD)


def get_or_create_tag(name, slug):
    """
    WordPress上で指定タグを探し、なければ作成してIDを返す。
    スラッグ（英字URL名）はサイトルールに従い英字で固定。
    """
    auth = _wp_auth()
    try:
        r = requests.get(
            f"{WP_SITE_URL}/wp-json/wp/v2/tags",
            auth=auth, params={"slug": slug}, timeout=15
        )
        hits = r.json()
        if isinstance(hits, list) and hits:
            return hits[0]["id"]
        # タグが存在しない場合は新規作成
        r2 = requests.post(
            f"{WP_SITE_URL}/wp-json/wp/v2/tags",
            auth=auth, json={"name": name, "slug": slug}, timeout=15
        )
        data = r2.json()
        if "id" in data:
            logger.info(f"  [WP] タグ '{name}' (slug={slug}) を新規作成しました: ID={data['id']}")
            return data["id"]
        logger.warning(f"  [WP] タグ作成失敗: {data}")
        return None
    except Exception as e:
        logger.error(f"  [WP] タグ取得/作成エラー ({name}): {e}")
        return None


def _wp_search_post_by_slug(slug):
    """
    product_id（= WP投稿のslug）からWP記事のID・現在のタグIDリストを取得する。
    """
    auth = _wp_auth()
    try:
        r = requests.get(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts",
            auth=auth, params={"slug": slug, "status": "publish", "_fields": "id,slug,tags"},
            timeout=15
        )
        posts = r.json()
        if isinstance(posts, list) and posts:
            # bcache対策: _検索の誤爆を防ぐため、完全一致でフィルタ
            exact = [p for p in posts if p.get("slug", "").lower() == slug.lower()]
            if exact:
                return exact[0]["id"], exact[0].get("tags", [])
    except Exception as e:
        logger.warning(f"  [WP] 記事検索エラー (slug={slug}): {e}")
    return None, []


def update_post_tags(wp_post_id, updated_tag_ids):
    """
    計算済みの最新タグ配列（updated_tag_ids）を1回だけWPにPOSTして上書き更新する。
    これによりキャッシュによる先祖返りを防ぎ、API通信を最小化する。
    """
    auth = _wp_auth()
    try:
        r = requests.post(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}",
            auth=auth, json={"tags": updated_tag_ids}, timeout=15
        )
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"  [WP] タグ一括更新エラー (post={wp_post_id}): {e}")
        return False


def _wp_get_posts_with_tag(tag_id):
    """
    WP REST API で指定タグIDが付いている全記事のslug（= product_id）を取得する。
    ページネーションで全件取得。タグ剥奪対象の特定に使用。
    """
    auth = _wp_auth()
    slugs = set()
    page = 1
    while True:
        try:
            r = requests.get(
                f"{WP_SITE_URL}/wp-json/wp/v2/posts",
                auth=auth,
                params={"tags": tag_id, "status": "publish", "_fields": "slug", "per_page": 100, "page": page},
                timeout=20
            )
            if r.status_code != 200:
                break
            posts = r.json()
            if not isinstance(posts, list) or not posts:
                break
            for p in posts:
                if p.get("slug"):
                    slugs.add(p["slug"])
            total_pages = int(r.headers.get("X-WP-TotalPages", 1))
            if page >= total_pages:
                break
            page += 1
        except Exception as e:
            logger.warning(f"  [WP] タグ付き記事取得エラー (tag={tag_id}, page={page}): {e}")
            break
    return slugs


# =====================================================================
# 2. DB から published 記事の product_id 一覧を取得
# =====================================================================
def get_all_published_product_ids():
    """
    統合DBから status='published' の product_id と site を返す。
    戻り値: { product_id: site_string, ... }
    """
    result = {}
    # v18.0.0: 統合DB1本から取得
    try:
        conn = db_connect(DB_FILE_UNIFIED)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT product_id, site FROM novelove_posts WHERE status='published'"
        ).fetchall()
        conn.close()
        for r in rows:
            result[r["product_id"].lower()] = r["site"] or ""
    except Exception as e:
        logger.warning(f"  [DB] 読み込みエラー: {e}")
    return result


# =====================================================================
# 3. FANZA / DMM セール & ランキング取得
# =====================================================================
def fetch_fanza_sale_product_ids():
    """
    FANZA / DMM の公式セール中の商品IDを取得する。
    - らぶカル（同人等）はAPIで campaign フィールドの有無で判定（確実）。
    - 商業作品はAPIでセールフラグが出力されないため、ブラウザからセール指定URLを最大10ページスクレイピングする。
    戻り値: set of product_id (content_id)
    """
    sale_ids = set()

    # === 1. らぶカル（同人等）のセール取得（API方式） ===
    if DMM_API_ID and DMM_AFFILIATE_API_ID:
        api_floors = [
            # らぶカルBL/TL（専用フロアがすべての同人作品を網羅する）
            {"site": "FANZA", "service": "doujin", "floor": "digital_doujin_bl"},
            {"site": "FANZA", "service": "doujin", "floor": "digital_doujin_tl"},
        ]
        for fl in api_floors:
            try:
                # offset で 100件ずつ最大1,000件まで取得（人気上位作品のセールを網羅）
                for offset in range(1, 1001, 100):
                    params = {
                        "api_id": DMM_API_ID,
                        "affiliate_id": DMM_AFFILIATE_API_ID,
                        "site": fl["site"],
                        "service": fl["service"],
                        "floor": fl["floor"],
                        "hits": 100,
                        "sort": "rank",
                        "offset": offset,
                        "output": "json",
                    }
                    if fl.get("keyword"): params["keyword"] = fl["keyword"]
                    r = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=params, timeout=15)
                    if r.status_code != 200:
                        break
                    items = r.json().get("result", {}).get("items", [])
                    if not items:
                        break  # これ以上作品がなければ終了
                    for item in items:
                        if item.get("campaign"):
                            cid = item.get("content_id", "")
                            if cid: sale_ids.add(cid.lower())
            except Exception as e:
                logger.warning(f"  [FANZA] セール取得エラー (API / {fl.get('floor')}): {e}")

    # === 2. DMM/FANZA 商業コミック セール抽出（スクレイピング方式） ===
    # 理由: 商業作品はAPIで「campaign」フラグが出力されない仕様のため、
    # 50%OFF以上のセール一覧ページを直接スクレイピングしてIDを網羅取得する。
    scrape_targets = [
        "https://book.dmm.com/list/?floor=Gbl&sale=discount&discount_rate=50&sort=ranking",    # DMM BL（人気順）
        "https://book.dmm.com/list/?floor=Gtl&sale=discount&discount_rate=50&sort=ranking",    # DMM TL（人気順）
        "https://book.dmm.co.jp/list/?category=670008&sale=discount&discount_rate=50&sort=ranking",  # FANZA BL（人気順）
        "https://book.dmm.co.jp/list/?category=670009&sale=discount&discount_rate=50&sort=ranking"   # FANZA TL（人気順）
    ]
    
    session = requests.Session()
    for domain in [".dmm.co.jp", ".book.dmm.co.jp", "book.dmm.com", "book.dmm.co.jp"]:
        # セールページの年齢確認・初回アクセス対策
        session.cookies.set("age_check_done", "1", domain=domain)
        session.cookies.set("ckcy", "1", domain=domain)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
        "Referer": "https://book.dmm.co.jp/",
    })

    for base_url in scrape_targets:
        # 人気順ソート済みのため、上位10ページ（約1,200件）で人気作品は十分カバーできる
        for page in range(1, 11):
            url = f"{base_url}&page={page}"
            try:
                r = session.get(url, timeout=15)
                if r.status_code != 200:
                    break
                soup = BeautifulSoup(r.text, "html.parser")
                all_links = [a.get("href") for a in soup.find_all("a") if a.get("href")]
                product_links = [l for l in all_links if "/product/" in l]
                
                # 正規表現で商品IDを抽出
                found_ids = list(dict.fromkeys([
                    re.search(r'/product/([^/]+)/', l).group(1) 
                    for l in product_links if re.search(r'/product/([^/]+)/', l)
                ]))
                
                if not found_ids:
                    break  # これ以上商品がなければ次のカテゴリへ
                    
                for pid in found_ids:
                    sale_ids.add(pid.lower())
                
                time.sleep(1)  # サーバー負荷への配慮
            except Exception as e:
                logger.warning(f"  [FANZA] スクレイピングエラー ({url}): {e}")
                break

    logger.info(f"  [FANZA] セール作品 {len(sale_ids)}件 検知")
    return sale_ids


def fetch_fanza_ranking_product_ids():
    """
    FANZA / DMM のランキングAPI（sort=rank）でTOP10の商品IDを取得する。
    戻り値: set of product_id (content_id)
    """
    ranking_ids = set()
    if not DMM_API_ID or not DMM_AFFILIATE_API_ID:
        return ranking_ids

    floors = [
        {"site": "FANZA", "service": "ebook",  "floor": "bl"},
        {"site": "FANZA", "service": "ebook",  "floor": "tl"},
        {"site": "DMM.com", "service": "ebook", "floor": "comic"},
        {"site": "DMM.com", "service": "ebook", "floor": "novel"},
        # らぶカルBL/TL（専用フロアがすべての同人作品を網羅する）
        {"site": "FANZA", "service": "doujin", "floor": "digital_doujin_bl"},
        {"site": "FANZA", "service": "doujin", "floor": "digital_doujin_tl"},
    ]

    for fl in floors:
        try:
            params = {
                "api_id": DMM_API_ID,
                "affiliate_id": DMM_AFFILIATE_API_ID,
                "site": fl["site"],
                "service": fl["service"],
                "floor": fl["floor"],
                "hits": 30,  # 全サイト統一: 30件
                "sort": "rank",
                "output": "json",
            }
            if fl.get("keyword"):
                params["keyword"] = fl["keyword"]
            r = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=params, timeout=15)
            if r.status_code != 200:
                continue
            items = r.json().get("result", {}).get("items", [])
            for item in items:
                cid = item.get("content_id", "")
                if cid:
                    ranking_ids.add(cid.lower())
        except Exception as e:
            logger.warning(f"  [FANZA] ランキング取得エラー ({fl.get('floor')}): {e}")

    logger.info(f"  [FANZA] ランキング作品 {len(ranking_ids)}件 検知")
    return ranking_ids


# =====================================================================
# 4. DLsite セール & ランキング取得（裏JSON API）
# =====================================================================
def fetch_dlsite_sale_product_ids(published_pids):
    """
    DLsiteのセール検索ページ (fsr/=/campaign/1/) をスクレイピングし、
    現在セール中の全 product_id を取得する。
    裏JSON API の discount/campaign フィールドは常にNoneを返すため使用不可。(v14.6.0刷新)
    ※ girls/bl/girls-pro/bl-pro の4エンドポイントを巡回し、
      ページネーションで全件取得する。
    """
    sale_ids = set()

    # 4フロアのセール検索ページ
    # v18.1.2: URL修正 — discount_rate_min/50 + manga はDLsiteのパーサーを壊し割引指定が無視されていた
    #   修正: discount_rates[0]/c9 (50%OFF以上) + comic/gekiga/tateyomi/novel/kanno (ノベラブ全対象種別)
    sale_search_urls = [
        "https://www.dlsite.com/girls/fsr/=/language/jp/sex_category[0]/female/work_type_category[0]/comic/work_type_category[1]/gekiga/work_type_category[2]/tateyomi/work_type_category[3]/novel/work_type_category[4]/kanno/order/trend/per_page/100/discount_rates[0]/c9/",      # 女性向け同人
        "https://www.dlsite.com/bl/fsr/=/language/jp/sex_category[0]/female/sex_category[1]/gay/work_type_category[0]/comic/work_type_category[1]/gekiga/work_type_category[2]/tateyomi/work_type_category[3]/novel/work_type_category[4]/kanno/order/trend/per_page/100/discount_rates[0]/c9/",  # BL同人
        "https://www.dlsite.com/girls-pro/fsr/=/language/jp/sex_category[0]/female/work_type_category[0]/comic/work_type_category[1]/gekiga/work_type_category[2]/tateyomi/work_type_category[3]/novel/work_type_category[4]/kanno/order/trend/per_page/100/discount_rates[0]/c9/",   # 女性向け商業
        "https://www.dlsite.com/bl-pro/fsr/=/language/jp/sex_category[0]/female/sex_category[1]/gay/work_type_category[0]/comic/work_type_category[1]/gekiga/work_type_category[2]/tateyomi/work_type_category[3]/novel/work_type_category[4]/kanno/order/trend/per_page/100/discount_rates[0]/c9/",  # BL商業
    ]

    for base_url in sale_search_urls:
        page = 1
        while page <= 10:  # 安全弁: 最大10ページ（1000件）
            try:
                url = base_url if page == 1 else f"{base_url}page/{page}/"
                r = requests.get(url, headers=HEADERS, timeout=20)
                if r.status_code != 200:
                    break

                # ページからproduct_idを正規表現で抽出
                codes = re.findall(r"((?:RJ|BJ|VJ)\d{6,10})", r.text)
                unique_codes = list(dict.fromkeys(codes))  # 出現順を保持して重複除去

                if not unique_codes:
                    break  # 作品が見つからなければ次のフロアへ

                for code in unique_codes:
                    sale_ids.add(code.lower())

                # 次ページがあるか確認（作品数が per_page 未満なら最終ページ）
                if len(unique_codes) < 50:  # per_page=100だが、重複除去後50件未満なら最終ページと判断
                    break
                page += 1
            except Exception as e:
                logger.warning(f"  [DLsite] セール検索ページ取得エラー ({base_url}, page={page}): {e}")
                break

    logger.info(f"  [DLsite] セール作品 {len(sale_ids)}件 検知 (セール検索ページスクレイピング)")
    return sale_ids



def fetch_dlsite_ranking_product_ids():
    """
    DLsiteのランキングページからTOP30のRJコードを取得する。
    各URLあたり30件（FANZA=20×4フロアとのバランス調整、v12.7.0）。
    """
    RANKING_TOP_N = 30  # 各URLから取得するTOP件数（FANZA=20×4フロア に合わせたバランス調整）
    ranking_ids = set()
    ranking_urls = [
        "https://www.dlsite.com/girls/ranking/week",  # 女性向け週間ランキング
        "https://www.dlsite.com/bl/ranking/week",     # BL週間ランキング（v12.7.0追加）
    ]
    for url in ranking_urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            # 出現順を保持して重複除去→先頭30件のみ取得
            codes = list(dict.fromkeys(re.findall(r"((?:RJ|BJ|VJ)\d{6,10})", r.text)))[:RANKING_TOP_N]
            ranking_ids.update(c.lower() for c in codes)
        except Exception as e:
            logger.warning(f"  [DLsite] ランキング取得エラー: {e}")

    logger.info(f"  [DLsite] ランキング作品 {len(ranking_ids)}件 検知")
    return ranking_ids


# =====================================================================
# 5. DigiKet ランキング取得（公式XML API sort=week）
# =====================================================================
def fetch_digiket_ranking_product_ids():
    """
    DigiKet公式XML API の sort=week を使って週間ランキングTOP30の作品IDを取得する。
    各ターゲットあたり30件（FANZA=20×4フロアとのバランス調整、v12.7.0）。
    """
    RANKING_TOP_N = 30  # 各ターゲットから取得するTOP件数（FANZA=20×4フロア に合わせたバランス調整）
    ranking_ids = set()
    for tgt in DIGIKET_TARGETS:
        for sort_type in ["week"]:  # 週間のみ（各サイト統一：月間は除外）
            try:
                url = f"https://api.digiket.com/xml/api/getxml.php?target={tgt['target']}&sort={sort_type}"
                r = requests.get(url, timeout=15)
                if r.status_code != 200:
                    continue
                content = r.content.decode("utf-8", errors="ignore")
                # 出現順を保持して重複除去→先頭30件のみ取得
                item_ids = list(dict.fromkeys(re.findall(r"ITM(\d+)", content)))[:RANKING_TOP_N]
                for iid in item_ids:
                    ranking_ids.add(f"itm{iid}")
            except Exception as e:
                logger.warning(f"  [DigiKet] ランキング取得エラー ({tgt['label']}/{sort_type}): {e}")

    logger.info(f"  [DigiKet] ランキング作品 {len(ranking_ids)}件 検知")
    return ranking_ids


def fetch_digiket_sale_product_ids():
    """
    DigiKetのセール情報をジャンル別専用URLからスクレイピングで取得する（v12.7.0刷新）。
    camp=on パラメータにより本物のセール中作品のみを厳密に取得。
    取得に失敗しても他サイトの処理に影響しない（隔離設計）。
    """
    sale_ids = set()
    # 女性向けジャンル別のセール専用URL（camp=on で本物のセール中のみに絞込）
    sale_urls = [
        "https://www.digiket.com/b/result/_data/limit=300/camp=on/sort=camp_end/",   # 女性向同人
        "https://www.digiket.com/bl/result/_data/limit=300/camp=on/sort=camp_end/",  # BL商業
    ]
    for url in sale_urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                logger.warning(f"  [DigiKet] セールページ取得失敗: status={r.status_code} url={url}")
                continue
            html_text = r.content.decode("EUC-JP", errors="ignore")
            for iid in re.findall(r"ITM(\d+)", html_text):
                sale_ids.add(f"itm{iid}")
        except Exception as e:
            logger.warning(f"  [DigiKet] セール取得エラー ({url}): {e}")

    logger.info(f"  [DigiKet] セール作品 {len(sale_ids)}件 検知")
    return sale_ids


# =====================================================================
# 6. メイン処理：突合 → タグ付与/剥奪 → Discord通知
# =====================================================================
def run_nexus():
    """Nexusメイン処理：全サイト巡回 → DB突合 → WPタグ更新 → Discord通知"""
    logger.info("=" * 60)
    logger.info("🚀 Nexus エンジン起動")
    logger.info("=" * 60)

    # --- Step 0: WPタグIDの確保 ---
    sale_tag_id = get_or_create_tag(SALE_TAG_NAME, SALE_TAG_SLUG)
    bestseller_tag_id = get_or_create_tag(BESTSELLER_TAG_NAME, BESTSELLER_TAG_SLUG)
    if not sale_tag_id or not bestseller_tag_id:
        msg = "🚨 [Nexus] WPタグの取得/作成に失敗しました。WP認証情報を確認してください。"
        logger.error(msg)
        notify_discord(msg, username="🚨 Nexus通知")
        return

    logger.info(f"  [WP] タグID確保完了: sale={sale_tag_id}, best-seller={bestseller_tag_id}")

    # --- Step 1: 自社DBの全published記事を取得 ---
    published_pids = get_all_published_product_ids()
    logger.info(f"  [DB] published 記事: {len(published_pids)}件")

    # --- Step 2: 各サイトからセール & ランキング情報を【隔離して】取得 ---
    all_sale_ids = set()
    all_ranking_ids = set()
    errors = []

    # FANZA
    try:
        fanza_sales = fetch_fanza_sale_product_ids()
        all_sale_ids.update(fanza_sales)
    except Exception as e:
        err_msg = f"[FANZA セール] {e}"
        logger.error(f"  🚨 {err_msg}")
        errors.append(err_msg)

    try:
        fanza_ranks = fetch_fanza_ranking_product_ids()
        all_ranking_ids.update(fanza_ranks)
    except Exception as e:
        err_msg = f"[FANZA ランキング] {e}"
        logger.error(f"  🚨 {err_msg}")
        errors.append(err_msg)

    # DLsite
    try:
        dlsite_sales = fetch_dlsite_sale_product_ids(published_pids)
        all_sale_ids.update(dlsite_sales)
    except Exception as e:
        err_msg = f"[DLsite セール] {e}"
        logger.error(f"  🚨 {err_msg}")
        errors.append(err_msg)

    try:
        dlsite_ranks = fetch_dlsite_ranking_product_ids()
        all_ranking_ids.update(dlsite_ranks)
    except Exception as e:
        err_msg = f"[DLsite ランキング] {e}"
        logger.error(f"  🚨 {err_msg}")
        errors.append(err_msg)

    # DigiKet（最も不安定なため、エラーに最も寛容）
    try:
        digiket_sales = fetch_digiket_sale_product_ids()
        all_sale_ids.update(digiket_sales)
    except Exception as e:
        err_msg = f"[DigiKet セール] {e}"
        logger.error(f"  🚨 {err_msg}")
        errors.append(err_msg)

    try:
        digiket_ranks = fetch_digiket_ranking_product_ids()
        all_ranking_ids.update(digiket_ranks)
    except Exception as e:
        err_msg = f"[DigiKet ランキング] {e}"
        logger.error(f"  🚨 {err_msg}")
        errors.append(err_msg)

    # エラー発生時のDiscord警告
    if errors:
        error_text = "\n".join(errors)
        notify_discord(
            f"🚨 **[Nexus注意] 一部サイトの取得に失敗しました**\n"
            f"サイトの構造が変わった可能性があります。\n"
            f"```\n{error_text}\n```\n"
            f"※ 他の正常なサイトの処理は続行しています。",
            username="🚨 Nexus通知"
        )

    logger.info(f"  [集計] セール候補: {len(all_sale_ids)}件 / ランキング候補: {len(all_ranking_ids)}件")

    # --- Step 3: DB突合 → 「変更が必要な記事だけ」にWP APIを叩く ---
    # パフォーマンス最適化: 全published記事にAPIコールするのではなく、
    # 「新たにタグを付ける対象」と「タグを剥がす対象」だけを特定する。
    stats = {
        "sale_added": 0, "sale_removed": 0,
        "rank_added": 0, "rank_removed": 0,
        "checked": 0,
    }

    # (A) タグを付けるべき記事: セール/ランキングに乗っていて、かつ自社DBにある記事
    pids_needing_sale_tag = all_sale_ids & set(published_pids.keys())
    pids_needing_rank_tag = all_ranking_ids & set(published_pids.keys())

    # (B) タグを剥がすべき記事: 現在WP上でセール/売れ筋タグを持っている記事をまず取得
    pids_with_sale_tag = _wp_get_posts_with_tag(sale_tag_id)
    pids_with_rank_tag = _wp_get_posts_with_tag(bestseller_tag_id)
    pids_losing_sale_tag = pids_with_sale_tag - pids_needing_sale_tag
    pids_losing_rank_tag = pids_with_rank_tag - pids_needing_rank_tag

    # 処理対象だけに絞り込み（WP APIコール数を最小化）
    all_targets = pids_needing_sale_tag | pids_needing_rank_tag | pids_losing_sale_tag | pids_losing_rank_tag
    logger.info(f"  [最適化] WP API対象: {len(all_targets)}件 (全{len(published_pids)}件中)")

    for pid in all_targets:
        wp_post_id, current_tags = _wp_search_post_by_slug(pid)
        if not wp_post_id:
            continue
        stats["checked"] += 1

        # リストをSetに変換して計算を容易にする
        new_tags = set(current_tags)
        original_tags = set(current_tags)
        logs = []

        # --- セールタグの計算 ---
        if pid in pids_needing_sale_tag:
            new_tags.add(sale_tag_id)
            logs.append(("sale_added", f"  🔥 セールタグ付与: {pid}"))
        elif pid in pids_losing_sale_tag:
            new_tags.discard(sale_tag_id)
            logs.append(("sale_removed", f"  ❄️ セールタグ剥奪: {pid}"))

        # --- 売れ筋タグの計算 ---
        if pid in pids_needing_rank_tag:
            new_tags.add(bestseller_tag_id)
            logs.append(("rank_added", f"  🏆 売れ筋タグ付与: {pid}"))
        elif pid in pids_losing_rank_tag:
            new_tags.discard(bestseller_tag_id)
            logs.append(("rank_removed", f"  📉 売れ筋タグ剥奪: {pid}"))

        # --- WPへの一括更新リクエスト ---
        if new_tags != original_tags:
            if update_post_tags(wp_post_id, list(new_tags)):
                for stat_key, log_msg in logs:
                    stats[stat_key] += 1
                    logger.info(log_msg)
            else:
                # 失敗時はカウントを戻すような厳密なロールバックは行わない（次回cronで再トライされるため）
                logger.warning(f"  ⚠️ タグ一括更新失敗: {pid}")

    # --- キャッシュクリア処理（追加） ---
    if stats["sale_added"] > 0 or stats["sale_removed"] > 0 or stats["rank_added"] > 0 or stats["rank_removed"] > 0:
        logger.info("  [WP] タグの更新があったため、KUSANAGIキャッシュをクリアします...")
        try:
            import subprocess
            subprocess.run("kusanagi bcache clear && kusanagi fcache clear", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("  [WP] キャッシュクリア完了")
        except Exception as e:
            logger.warning(f"  [WP] キャッシュクリア失敗: {e}")

    # --- Step 4: Discord 日次サマリー ---
    summary = (
        f"📊 **[Nexus日次サマリー]** ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n"
        f"┣ チェック対象: {stats['checked']}件\n"
        f"┣ 🔥 セール: 新規{stats['sale_added']}件 / 解除{stats['sale_removed']}件\n"
        f"┣ 🏆 売れ筋: 新規{stats['rank_added']}件 / 解除{stats['rank_removed']}件\n"
    )
    if errors:
        summary += f"┗ ⚠️ 取得エラー: {len(errors)}件（一部サイトの構造変更の可能性）"
    else:
        summary += f"┗ ✅ 全サイト正常取得完了"

    notify_discord(summary, username="📊 Nexusレポート")
    logger.info(summary.replace("**", "").replace("┣", "  ").replace("┗", "  "))
    logger.info("=" * 60)
    logger.info("🏁 Nexus エンジン完了")
    logger.info("=" * 60)


# =====================================================================
# 7. あらすじ更新検知（S4）
# =====================================================================
def run_desc_check():
    """
    公開済み記事のあらすじを取得元サイトから再取得し、
    DBの既存 description と比較して変化があれば is_desc_updated=1 をセット。
    旧あらすじは prev_description に退避し、description を新しいあらすじで上書き。
    """
    logger.info("=" * 60)
    logger.info("📝 あらすじ更新検知バッチ開始")
    logger.info("=" * 60)

    updated_count = 0
    checked_count = 0
    errors = []

    # v18.0.0: 統合DB1本から取得
    try:
        conn = db_connect(DB_FILE_UNIFIED)
        conn.row_factory = sqlite3.Row
        # product_url がある published 記事のみ対象
        rows = conn.execute(
            """SELECT product_id, product_url, description, site, genre
               FROM novelove_posts
               WHERE status='published' AND product_url != '' AND product_url IS NOT NULL"""
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"  [DB] 読み込みエラー: {e}")
        errors.append(str(e))
        rows = []

    for row in rows:
        pid         = row["product_id"]
        product_url = row["product_url"]
        old_desc    = row["description"] or ""
        site_raw    = row["site"] or ""
        genre_raw   = row["genre"] or ""

        try:
            new_desc = scrape_description(product_url, site=site_raw, genre=genre_raw)
        except Exception as e:
            logger.warning(f"  [DESC] 取得失敗 ({pid}): {e}")
            errors.append(f"{pid}: {e}")
            continue

        # 取得失敗・除外判定は無視
        if not new_desc or new_desc in ("__EXCLUDED_TYPE__",):
            continue

        checked_count += 1

        # difflib で内容の変化を検知（空白・改行を正規化してノイズをカット）
        _old_norm = re.sub(r'\s+', ' ', old_desc).strip()
        _new_norm = re.sub(r'\s+', ' ', new_desc).strip()
        ratio = difflib.SequenceMatcher(None, _old_norm, _new_norm).ratio()
        if ratio >= 0.99:  # 99%以上一致 → 変化なし
            continue

        # 変化あり: 旧あらすじを退避して新しいあらすじで上書き
        logger.info(
            f"  [📝 更新検知] {pid} | 類似度={ratio:.1%} "
            f"| 旧:{len(old_desc)}文字 → 新:{len(new_desc)}文字"
        )
        try:
            conn2 = db_connect(DB_FILE_UNIFIED)
            conn2.execute(
                """UPDATE novelove_posts
                   SET is_desc_updated = 1,
                       prev_description = ?,
                       description = ?
                   WHERE product_id = ?""",
                (old_desc, new_desc, pid),
            )
            conn2.commit()
            conn2.close()
            updated_count += 1
        except Exception as e:
            logger.error(f"  [DB] 更新失敗 ({pid}): {e}")
            errors.append(f"{pid}: DB更新失敗 {e}")


    # Discord サマリー
    summary = (
        f"📝 **[あらすじ更新検知]** ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n"
        f"┣ 確認済み: {checked_count}件 / 更新検知: {updated_count}件\n"
    )
    if errors:
        summary += f"┗ ⚠️ 取得エラー: {len(errors)}件"
    else:
        summary += f"┗ ✅ 全件正常取得完了"

    if updated_count > 0:
        notify_discord(summary, username="📝 あらすじ更新検知")

    logger.info(summary.replace("**", "").replace("┣", "  ").replace("┗", "  "))
    logger.info("=" * 60)
    logger.info("🏁 あらすじ更新検知バッチ完了")
    logger.info("=" * 60)


# =====================================================================
# 8. エントリーポイント
# =====================================================================
if __name__ == "__main__":
    run_nexus()
    run_desc_check()
