#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
Novelove Nexus — 自動セール検知 & 売れ筋タグ管理エンジン v1.0.0
==========================================================
【概要】
  DMM / らぶカル / DLsite を巡回し、
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
    DB_FILE_UNIFIED,
    db_connect, notify_discord,
    WP_SITE_URL, HEADERS,
    WP_USER, WP_APP_PASSWORD,
    DMM_API_ID, DMM_AFFILIATE_API_ID,
    WP_CLI_PATH, WP_DOC_ROOT,
)
from novelove_fetcher import scrape_description

# === 定数 ===
SALE_TAG_NAME     = "期間限定セール"
SALE_TAG_SLUG     = "sale"
BESTSELLER_TAG_NAME = "売れ筋作品"
BESTSELLER_TAG_SLUG = "best-seller"




# =====================================================================
# 1. WordPress タグ管理（REST API）
# =====================================================================
def _wp_auth():
    """WP REST API 用の認証タプルを返す。"""
    return (WP_USER, WP_APP_PASSWORD)


def _run_wp_cli(cmd_list, timeout=30):
    """
    KUSANAGIのPHPパスを通した状態でwp-cliを実行する。
    """
    import subprocess
    import os
    env = os.environ.copy()
    env["PATH"] = f"/opt/kusanagi/php/bin:{env.get('PATH', '')}"
    return subprocess.run(
        cmd_list,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env
    )


def get_or_create_tag(name, slug):
    """
    WordPress上で指定タグを探し、なければ作成してIDを返す。
    スラッグ（英字URL名）はサイトルールに従い英字で固定。
    ※ REST APIはGETパラメータ消失バグがあるため、wp-cliを使用。
    """
    import subprocess
    import json
    try:
        # 1. タグの検索
        result = _run_wp_cli(
            [
                WP_CLI_PATH, "term", "list", "post_tag",
                f"--slug={slug}",
                "--fields=term_id",
                "--format=json",
                f"--path={WP_DOC_ROOT}",
                "--allow-root",
            ],
            timeout=30
        )
        terms = json.loads(result.stdout or "[]")
        if isinstance(terms, list) and terms:
            return int(terms[0]["term_id"])

        # 2. 存在しない場合は新規作成
        create_result = _run_wp_cli(
            [
                WP_CLI_PATH, "term", "create", "post_tag", name,
                f"--slug={slug}",
                "--format=json",
                f"--path={WP_DOC_ROOT}",
                "--allow-root",
            ],
            timeout=30
        )
        data = json.loads(create_result.stdout or "{}")
        if "term_id" in data:
            logger.info(f"  [WP] タグ '{name}' (slug={slug}) を新規作成しました: ID={data['term_id']}")
            return int(data["term_id"])
        
        logger.warning(f"  [WP] タグ作成失敗 (stdout): {create_result.stdout}, (stderr): {create_result.stderr}")
        return None
    except Exception as e:
        logger.error(f"  [WP] タグ取得/作成エラー ({name}): {e}")
        return None


def _wp_search_post_by_slug(slug):
    """
    product_id（= WP投稿のslug）からWP記事のID・現在のタグIDリスト・タイトル・本文を取得する。
    ※ REST APIはサーバーのNginxキャッシュ設定によりクエリパラメータが消失するバグがあるため、
      wp-cli（MySQL直接アクセス）を使用して確実に取得する。
    """
    import subprocess
    import json
    try:
        # wp-cli で slug 完全一致の投稿を取得
        result = _run_wp_cli(
            [
                WP_CLI_PATH, "post", "list",
                f"--name={slug}",
                "--post_status=publish",
                "--fields=ID,post_name",
                "--format=json",
                f"--path={WP_DOC_ROOT}",
                "--allow-root",
            ],
            timeout=30
        )
        posts = json.loads(result.stdout or "[]")
        if not posts:
            return None, [], "", ""
        post_id = int(posts[0]["ID"])

        # タグIDリストを取得
        tag_result = _run_wp_cli(
            [
                WP_CLI_PATH, "post", "term", "list", str(post_id), "post_tag",
                "--fields=term_id",
                "--format=json",
                f"--path={WP_DOC_ROOT}",
                "--allow-root",
            ],
            timeout=30
        )
        tag_data = json.loads(tag_result.stdout or "[]")
        tag_ids = [int(t["term_id"]) for t in tag_data]

        # タイトル・本文はREST APIで取得（POSTは通常のAPIを使用、GETのみバグがある）
        auth = _wp_auth()
        r = requests.get(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
            auth=auth, params={"_fields": "title,content"}, timeout=15
        )
        post_data = r.json() if r.status_code == 200 else {}
        title = post_data.get("title", {}).get("rendered", "")
        content = post_data.get("content", {}).get("rendered", "")

        return post_id, tag_ids, title, content
    except Exception as e:
        logger.warning(f"  [WP] 記事検索エラー (slug={slug}): {e}")
    return None, [], "", ""


def update_post_data(wp_post_id, data_dict):
    """
    WP記事のタグ・タイトル・本文を一括更新する。
    data_dict には "tags", "title", "content" などを指定。
    タグはwp-cli経由、タイトル・本文はREST API POST経由（POSTはクエリパラメータ不要なので正常動作）。
    """
    import subprocess
    import json
    try:
        # 1. タグの更新（wp-cli 経由）
        if "tags" in data_dict:
            tag_ids = data_dict["tags"]
            if tag_ids:
                # term_id をカンマ区切りで渡す
                tag_id_strs = [str(t) for t in tag_ids]
                _run_wp_cli(
                    [
                        WP_CLI_PATH, "post", "term", "set", str(wp_post_id), "post_tag",
                        "--by=id",
                    ] + tag_id_strs + [
                        f"--path={WP_DOC_ROOT}",
                        "--allow-root",
                    ],
                    timeout=30
                )
            else:
                # タグを全て外す場合
                _run_wp_cli(
                    [
                        WP_CLI_PATH, "post", "term", "remove", str(wp_post_id), "post_tag", "--all",
                        f"--path={WP_DOC_ROOT}",
                        "--allow-root",
                    ],
                    timeout=30
                )

        # 2. タイトル・本文の更新（REST API POST経由: POSTはクエリパラメータ不要で正常動作）
        rest_payload = {k: v for k, v in data_dict.items() if k != "tags"}
        if rest_payload:
            auth = _wp_auth()
            r = requests.post(
                f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}",
                auth=auth, json=rest_payload, timeout=20
            )
            return r.status_code in (200, 201)
        return True
    except Exception as e:
        logger.error(f"  [WP] 記事一括更新エラー (post={wp_post_id}): {e}")
        return False


def _wp_get_posts_with_tag(tag_id):
    """
    指定タグIDが付いている全記事のslug（= product_id）を取得する。
    ※ REST APIはクエリパラメータ消失バグがあるため、wp-cli（MySQL直接アクセス）を使用。
    """
    import subprocess
    import json
    slugs = set()
    try:
        result = _run_wp_cli(
            [
                WP_CLI_PATH, "post", "list",
                f"--tag_id={tag_id}",
                "--post_status=publish",
                "--fields=post_name",
                "--format=json",
                "--posts_per_page=-1",
                f"--path={WP_DOC_ROOT}",
                "--allow-root",
            ],
            timeout=60
        )
        posts = json.loads(result.stdout or "[]")
        for p in posts:
            if p.get("post_name"):
                slugs.add(p["post_name"])
    except Exception as e:
        logger.warning(f"  [WP] タグ付き記事取得エラー (tag={tag_id}): {e}")
    return slugs


# =====================================================================
# 2. DB から published 記事の product_id 一覧を取得
# =====================================================================
def get_all_published_product_ids():
    """
    統合DBから status='published' の product_id, site, is_exclusive を返す。
    戻り値: { product_id: {"site": site_string, "is_exclusive": is_exclusive_value}, ... }
    """
    result = {}
    # v18.0.0: 統合DB1本から取得
    # v20.0.5: is_exclusive も併せて取得
    try:
        conn = db_connect(DB_FILE_UNIFIED)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT product_id, site, is_exclusive FROM novelove_posts WHERE status='published' AND post_type='regular'"
        ).fetchall()
        conn.close()
        for r in rows:
            result[r["product_id"].lower()] = {
                "site": r["site"] or "",
                "is_exclusive": r["is_exclusive"] if "is_exclusive" in r.keys() else 0
            }
    except Exception as e:
        logger.warning(f"  [DB] 読み込みエラー: {e}")
    return result


# =====================================================================
# 3. DMM / らぶカル セール & ランキング取得
# =====================================================================
def fetch_dmm_sale_product_ids():
    """
    DMM / らぶカル の公式セール中の商品IDを取得する。
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
                        campaign = item.get("campaign")
                        if campaign:
                            # 30%以上かどうか判定
                            prices = item.get("prices", {})
                            price = prices.get("price")
                            list_price = prices.get("list_price")
                            pct = 0
                            if list_price and price and int(list_price) > 0:
                                pct = int((1 - float(price) / float(list_price)) * 100)
                            
                            # 定価が取得できない場合はキャンペーン名からパース
                            if pct == 0 and isinstance(campaign, list) and len(campaign) > 0:
                                title = campaign[0].get("title", "")
                                m_pct = re.search(r"(\d+)", title)
                                if m_pct:
                                    pct = int(m_pct.group(1))
                                    
                            if pct >= 30:
                                cid = item.get("content_id", "")
                                if cid: sale_ids.add(cid.lower())
            except Exception as e:
                logger.warning(f"  [DMM/らぶカル] セール取得エラー (API / {fl.get('floor')}): {e}")

    # === 2. DMM 商業コミック セール抽出（スクレイピング方式） ===
    # 理由: 商業作品はAPIで「campaign」フラグが出力されない仕様のため、
    # 30%OFF以上のセール一覧ページを直接スクレイピングしてIDを網羅取得する。
    scrape_targets = [
        "https://book.dmm.com/list/?floor=Gbl&sale=discount&discount_rate=30&sort=ranking",    # DMM BL（人気順）
        "https://book.dmm.com/list/?floor=Gtl&sale=discount&discount_rate=30&sort=ranking"     # DMM TL（人気順）
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
                # 例: /product/824544/b412arvmj03374/ -> b412arvmj03374 を抽出する (DMM商業)
                found_ids = []
                for l in product_links:
                    m_second = re.search(r'/product/[^/]+/([^/]+)/', l)
                    if m_second:
                        found_ids.append(m_second.group(1).lower())
                    else:
                        m_first = re.search(r'/product/([^/]+)/', l)
                        if m_first:
                            found_ids.append(m_first.group(1).lower())
                found_ids = list(dict.fromkeys(found_ids))
                
                if not found_ids:
                    break  # これ以上商品がなければ次のカテゴリへ
                    
                for pid in found_ids:
                    sale_ids.add(pid)
                
                time.sleep(1)  # サーバー負荷への配慮
            except Exception as e:
                logger.warning(f"  [DMM/らぶカル] スクレイピングエラー ({url}): {e}")
                break

    logger.info(f"  [DMM/らぶカル] セール作品 {len(sale_ids)}件 検知")
    return sale_ids


def fetch_dmm_ranking_product_ids():
    """
    DMM / らぶカル のランキングAPI（sort=rank）でTOP30の商品IDを取得する。
    戻り値: set of product_id (content_id)
    """
    ranking_ids = set()
    if not DMM_API_ID or not DMM_AFFILIATE_API_ID:
        return ranking_ids

    # v21.5.0: DMM電子書籍を「全ジャンル総合ランキング」から
    #   「BL/TLカテゴリ別ランキング」へ変更（ランキング記事 fetch_ranking_dmm と同じ取得軸に統一）。
    #   従来の comic/novel 総合ランキングは男性向けが大半を占め、女性向けBL/TL作品が
    #   売れ筋に載らず best-seller タグが付かない偽陰性の温床だった。
    #   article_id: BL漫画=66036 / BL小説=66042 / TL漫画=66060 / TL小説=66064
    floors = [
        {"site": "DMM.com", "service": "ebook", "floor": "comic", "article": "category", "article_id": "66036"},  # BL漫画
        {"site": "DMM.com", "service": "ebook", "floor": "novel", "article": "category", "article_id": "66042"},  # BL小説
        {"site": "DMM.com", "service": "ebook", "floor": "comic", "article": "category", "article_id": "66060"},  # TL漫画
        {"site": "DMM.com", "service": "ebook", "floor": "novel", "article": "category", "article_id": "66064"},  # TL小説
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
            if fl.get("article") and fl.get("article_id"):
                params["article"] = fl["article"]
                params["article_id"] = fl["article_id"]
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
            logger.warning(f"  [DMM/らぶカル] ランキング取得エラー ({fl.get('floor')}): {e}")

    logger.info(f"  [DMM/らぶカル] ランキング作品 {len(ranking_ids)}件 検知")
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

    # 4フロアのセール検索ページ + 2フロアの一般向けセール検索ページ
    # v18.1.2: URL修正 — discount_rate_min/50 + manga はDLsiteのパーサーを壊し割引指定が無視されていた
    #   修正: discount_rates[0]/c8/discount_rates[1]/c9 (30%OFF以上) + comic/gekiga/tateyomi/novel/kanno (ノベラブ全対象種別)
    # v20.0.1: DLsite一般（全年齢）セール検索URLを追記
    sale_search_urls = [
        "https://www.dlsite.com/girls/fsr/=/language/jp/sex_category[0]/female/work_type_category[0]/comic/work_type_category[1]/gekiga/work_type_category[2]/tateyomi/work_type_category[3]/novel/work_type_category[4]/kanno/work_type_category[5]/SOU/order/trend/per_page/100/discount_rates[0]/c8/discount_rates[1]/c9/",      # 女性向け同人
        "https://www.dlsite.com/bl/fsr/=/language/jp/sex_category[0]/female/sex_category[1]/gay/work_type_category[0]/comic/work_type_category[1]/gekiga/work_type_category[2]/tateyomi/work_type_category[3]/novel/work_type_category[4]/kanno/work_type_category[5]/SOU/order/trend/per_page/100/discount_rates[0]/c8/discount_rates[1]/c9/",  # BL同人
        "https://www.dlsite.com/girls-pro/fsr/=/language/jp/sex_category[0]/female/work_type_category[0]/comic/work_type_category[1]/gekiga/work_type_category[2]/tateyomi/work_type_category[3]/novel/work_type_category[4]/kanno/work_type_category[5]/SOU/order/trend/per_page/100/discount_rates[0]/c8/discount_rates[1]/c9/",   # 女性向け商業
        "https://www.dlsite.com/bl-pro/fsr/=/language/jp/sex_category[0]/female/sex_category[1]/gay/work_type_category[0]/comic/work_type_category[1]/gekiga/work_type_category[2]/tateyomi/work_type_category[3]/novel/work_type_category[4]/kanno/work_type_category[5]/SOU/order/trend/per_page/100/discount_rates[0]/c8/discount_rates[1]/c9/",  # BL商業
        "https://www.dlsite.com/home/fsr/=/language/jp/sex_category[0]/female/work_type_category[0]/MNG/work_type_category[1]/novel/work_type_category[2]/SOU/order/trend/per_page/100/discount_rates[0]/c8/discount_rates[1]/c9/",      # 女性向け一般同人（全年齢）
        "https://www.dlsite.com/home/fsr/=/language/jp/sex_category[0]/female/sex_category[1]/gay/work_type_category[0]/MNG/work_type_category[1]/novel/work_type_category[2]/SOU/order/trend/per_page/100/discount_rates[0]/c8/discount_rates[1]/c9/",  # BL一般同人（全年齢）
    ]

    for base_url in sale_search_urls:
        page = 1
        while page <= 10:  # 安全弁: 最大10ページ（1000件）
            try:
                url = base_url if page == 1 else f"{base_url}page/{page}/"
                r = requests.get(url, headers=HEADERS, timeout=20)
                if r.status_code != 200:
                    break

                # メイン作品リストの dd class="work_name" のみから抽出することで、サイドバーや履歴などの誤検知を100%防止
                dd_blocks = re.findall(r'<dd class="work_name">(.*?)</dd>', r.text, re.DOTALL)
                codes = []
                for block in dd_blocks:
                    match = re.search(r"((?:RJ|BJ|VJ)\d{6,10})", block)
                    if match:
                        codes.append(match.group(1).lower())
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
    # v21.5.0: DLsite一般（全年齢）週間ランキングURLを撤去。
    #   `home/ranking/week?is_tl=1(is_bl=1&is_gay=1)` はクエリが無視され男性向け一般が混入するため、
    #   売れ筋タグ判定でも「女性向けBL/TLが売れ筋に載らない（偽陰性）」原因になっていた。
    #   女性向けであることが担保された R-18 BL/TL 週間ランキングのみを使用する。
    ranking_urls = [
        "https://www.dlsite.com/girls/ranking/week",  # 女性向け（TL）週間ランキング
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

    # DMM / らぶカル
    try:
        dmm_sales = fetch_dmm_sale_product_ids()
        all_sale_ids.update(dmm_sales)
    except Exception as e:
        err_msg = f"[DMM/らぶカル セール] {e}"
        logger.error(f"  🚨 {err_msg}")
        errors.append(err_msg)

    try:
        dmm_ranks = fetch_dmm_ranking_product_ids()
        all_ranking_ids.update(dmm_ranks)
    except Exception as e:
        err_msg = f"[DMM/らぶカル ランキング] {e}"
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

    SALE_BANNER_HTML = (
        "<!-- NOVELOVE_SALE_BANNER_START -->\n"
        '<div class="novelove-sale-banner" style="background: linear-gradient(135deg, #ff4e50, #f9d423); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(255, 78, 80, 0.2);">\n'
        "    【期間限定セール中！】 今だけお得に購入できるチャンスです！\n"
        "</div>\n"
        "<!-- NOVELOVE_SALE_BANNER_END -->\n"
    )

    RANK_BANNER_HTML = (
        "<!-- NOVELOVE_RANK_BANNER_START -->\n"
        '<div class="novelove-rank-banner" style="background: linear-gradient(135deg, #f5af19, #f12711); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(245, 175, 25, 0.2);">\n'
        "    【売れ筋！】 週間ランキング入りした人気作品です！\n"
        "</div>\n"
        "<!-- NOVELOVE_RANK_BANNER_END -->\n"
    )

    COMBINED_BANNER_HTML = (
        "<!-- NOVELOVE_COMBINED_BANNER_START -->\n"
        '<div class="novelove-combined-banner" style="background: linear-gradient(135deg, #ff4e50, #f5af19); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(255, 78, 80, 0.2);">\n'
        "    【セール＆売れ筋！】 週間ランキング入り！今だけお得な注目作！\n"
        "</div>\n"
        "<!-- NOVELOVE_COMBINED_BANNER_END -->\n"
    )

    # === v20.0.5: 専売バナー定義（共通マーカー NOVELOVE_EXCLUSIVE_BANNER を使用） ===
    DLSITE_EXCL_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #7b1fa2, #e91e63); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(123, 31, 162, 0.2);">\n'
        "    【DLsite専売】 ここでしか読めない限定配信作品です！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )
    DLSITE_EXCL_SALE_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #7b1fa2, #e91e63); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(123, 31, 162, 0.2);">\n'
        "    【DLsite専売・セール中！】 今だけお得に購入できるチャンスです！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )
    DLSITE_EXCL_RANK_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #7b1fa2, #e91e63); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(123, 31, 162, 0.2);">\n'
        "    【DLsite専売・売れ筋！】 週間ランキング入りした限定注目作です！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )
    DLSITE_EXCL_TRIPLE_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #7b1fa2, #e91e63); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(123, 31, 162, 0.2);">\n'
        "    【DLsite専売・セール＆売れ筋！】 週間ランキング入り！今だけお得な限定注目作！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )

    LOVECAL_EXCL_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #ff5722, #ff9800); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(255, 87, 34, 0.2);">\n'
        "    【らぶカル専売】 ここでしか読めない限定配信作品です！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )
    LOVECAL_EXCL_SALE_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #ff5722, #ff9800); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(255, 87, 34, 0.2);">\n'
        "    【らぶカル専売・セール中！】 今だけお得に購入できるチャンスです！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )
    LOVECAL_EXCL_RANK_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #ff5722, #ff9800); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(255, 87, 34, 0.2);">\n'
        "    【らぶカル専売・売れ筋！】 週間ランキング入りした限定注目作です！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )
    LOVECAL_EXCL_TRIPLE_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #ff5722, #ff9800); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(255, 87, 34, 0.2);">\n'
        "    【らぶカル専売・セール＆売れ筋！】 週間ランキング入り！今だけお得な限定注目作！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )

    DMM_EXCL_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #0d47a1, #29b6f6); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(13, 71, 161, 0.2);">\n'
        "    【DMM独占】 ここでしか読めない限定配信作品です！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )
    DMM_EXCL_SALE_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #0d47a1, #29b6f6); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(13, 71, 161, 0.2);">\n'
        "    【DMM独占・セール中！】 今だけお得に購入できるチャンスです！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )
    DMM_EXCL_RANK_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #0d47a1, #29b6f6); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(13, 71, 161, 0.2);">\n'
        "    【DMM独占・売れ筋！】 週間ランキング入りした限定注目作です！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )
    DMM_EXCL_TRIPLE_BANNER_HTML = (
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->\n"
        '<div class="novelove-exclusive-banner" style="background: linear-gradient(135deg, #0d47a1, #29b6f6); color: #fff; padding: 10px 12px; border-radius: 6px; margin-bottom: 20px; font-weight: bold; text-align: center; font-size: 14px; line-height: 1.4; box-shadow: 0 2px 10px rgba(13, 71, 161, 0.2);">\n'
        "    【DMM独占・セール＆売れ筋！】 週間ランキング入り！今だけお得な限定注目作！\n"
        "</div>\n"
        "<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n"
    )

    for pid in all_targets:
        wp_post_id, current_tags, current_title, current_content = _wp_search_post_by_slug(pid)
        if not wp_post_id:
            continue
        stats["checked"] += 1

        # リストをSetに変換して計算を容易にする
        new_tags = set(current_tags)
        original_tags = set(current_tags)
        
        logs = []
        wp_payload = {}

        # 専売（独占）状態の取得 (v20.0.5)
        post_info = published_pids.get(pid.lower())
        is_exclusive = False
        site_str = ""
        if post_info:
            is_exclusive = post_info.get("is_exclusive", 0) == 1
            site_str = post_info.get("site", "")

        # 現在および更新後のセール状態を決定
        is_sale = False
        if pid in pids_needing_sale_tag:
            is_sale = True
            new_tags.add(sale_tag_id)
        elif pid in pids_losing_sale_tag:
            is_sale = False
            new_tags.discard(sale_tag_id)
        else:
            is_sale = sale_tag_id in original_tags

        # 現在および更新後の売れ筋状態を決定
        is_rank = False
        if pid in pids_needing_rank_tag:
            is_rank = True
            new_tags.add(bestseller_tag_id)
        elif pid in pids_losing_rank_tag:
            is_rank = False
            new_tags.discard(bestseller_tag_id)
        else:
            is_rank = bestseller_tag_id in original_tags

        # --- タイトル先頭のクリーンアップ & 再構築 ---
        clean_title = current_title
        while True:
            prev_title = clean_title
            # 新旧すべてのプレフィックスを安全に除去
            clean_title = re.sub(r"^【期間限定セール中！】", "", clean_title).strip()
            clean_title = re.sub(r"^【人気売れ筋！】", "", clean_title).strip()
            clean_title = re.sub(r"^【セール中】", "", clean_title).strip()
            clean_title = re.sub(r"^【売れ筋】", "", clean_title).strip()
            clean_title = re.sub(r"^【DLsite専売】", "", clean_title).strip()
            clean_title = re.sub(r"^【らぶカル専売】", "", clean_title).strip()
            clean_title = re.sub(r"^【DMM独占】", "", clean_title).strip()
            clean_title = re.sub(r"^【セール＆売れ筋！】", "", clean_title).strip()
            if clean_title == prev_title:
                break

        # ストア別の専売プレフィックス決定
        excl_prefix = ""
        if is_exclusive:
            if "DLsite" in site_str:
                excl_prefix = "【DLsite専売】"
            elif "Lovecal" in site_str or "らぶカル" in site_str:
                excl_prefix = "【らぶカル専売】"
            elif "DMM" in site_str or "FANZA" in site_str:
                excl_prefix = "【DMM独占】"

        # 重複制御ルール：
        # - 専売 ＆ セール ＆ 売れ筋 ➔ 【専売】【セール中】 に制限（売れ筋を非表示）
        # - それ以外は最大2つまで並べる
        prefix = ""
        if is_exclusive and is_sale and is_rank:
            prefix = f"{excl_prefix}【セール中】"
        else:
            if is_exclusive and excl_prefix:
                prefix += excl_prefix
            if is_sale:
                prefix += "【セール中】"
            if is_rank:
                prefix += "【売れ筋】"

        new_title = f"{prefix}{clean_title}"

        # --- 本文バナーのクリーンアップ & 再構築 ---
        clean_content = current_content
        clean_content = re.sub(
            r"<!-- NOVELOVE_SALE_BANNER_START -->.*?<!-- NOVELOVE_SALE_BANNER_END -->\n?",
            "",
            clean_content,
            flags=re.DOTALL
        )
        clean_content = re.sub(
            r"<!-- NOVELOVE_RANK_BANNER_START -->.*?<!-- NOVELOVE_RANK_BANNER_END -->\n?",
            "",
            clean_content,
            flags=re.DOTALL
        )
        clean_content = re.sub(
            r"<!-- NOVELOVE_COMBINED_BANNER_START -->.*?<!-- NOVELOVE_COMBINED_BANNER_END -->\n?",
            "",
            clean_content,
            flags=re.DOTALL
        )
        # 専売バナー（共通マーカー仕様）を一括除去
        clean_content = re.sub(
            r"<!-- NOVELOVE_EXCLUSIVE_BANNER_START -->.*?<!-- NOVELOVE_EXCLUSIVE_BANNER_END -->\n?",
            "",
            clean_content,
            flags=re.DOTALL
        )

        # ストア別の専売バナーHTMLの決定
        excl_banner = ""
        excl_sale_banner = ""
        excl_rank_banner = ""
        excl_triple_banner = ""
        if "DLsite" in site_str:
            excl_banner = DLSITE_EXCL_BANNER_HTML
            excl_sale_banner = DLSITE_EXCL_SALE_BANNER_HTML
            excl_rank_banner = DLSITE_EXCL_RANK_BANNER_HTML
            excl_triple_banner = DLSITE_EXCL_TRIPLE_BANNER_HTML
        elif "Lovecal" in site_str or "らぶカル" in site_str:
            excl_banner = LOVECAL_EXCL_BANNER_HTML
            excl_sale_banner = LOVECAL_EXCL_SALE_BANNER_HTML
            excl_rank_banner = LOVECAL_EXCL_RANK_BANNER_HTML
            excl_triple_banner = LOVECAL_EXCL_TRIPLE_BANNER_HTML
        elif "DMM" in site_str or "FANZA" in site_str:
            excl_banner = DMM_EXCL_BANNER_HTML
            excl_sale_banner = DMM_EXCL_SALE_BANNER_HTML
            excl_rank_banner = DMM_EXCL_RANK_BANNER_HTML
            excl_triple_banner = DMM_EXCL_TRIPLE_BANNER_HTML

        new_content = clean_content
        # 帯の全8パターン・排他統合挿入ロジック
        if is_exclusive:
            if is_sale and is_rank:
                new_content = excl_triple_banner + new_content
            elif is_sale:
                new_content = excl_sale_banner + new_content
            elif is_rank:
                new_content = excl_rank_banner + new_content
            else:
                new_content = excl_banner + new_content
        else:
            if is_sale and is_rank:
                new_content = COMBINED_BANNER_HTML + new_content
            elif is_sale:
                new_content = SALE_BANNER_HTML + new_content
            elif is_rank:
                new_content = RANK_BANNER_HTML + new_content

        # ログメッセージの生成
        if sale_tag_id not in original_tags and sale_tag_id in new_tags:
            logs.append(("sale_added", f"  🔥 セールタグ付与・テキスト自動更新: {pid}"))
        elif sale_tag_id in original_tags and sale_tag_id not in new_tags:
            logs.append(("sale_removed", f"  ❄️ セールタグ剥奪・テキスト元戻し: {pid}"))

        if bestseller_tag_id not in original_tags and bestseller_tag_id in new_tags:
            logs.append(("rank_added", f"  🏆 売れ筋タグ付与・テキスト自動更新: {pid}"))
        elif bestseller_tag_id in original_tags and bestseller_tag_id not in new_tags:
            logs.append(("rank_removed", f"  📉 売れ筋タグ剥奪・テキスト元戻し: {pid}"))

        # 一括更新用のペイロード作成
        if new_tags != original_tags:
            wp_payload["tags"] = list(new_tags)
        if new_title != current_title:
            wp_payload["title"] = new_title
        if new_content != current_content:
            wp_payload["content"] = new_content

        # 何かしら変更がある場合のみ一括更新APIを叩く
        if wp_payload:
            if update_post_data(wp_post_id, wp_payload):
                # タイトルや本文の更新があった場合もキャッシュクリアのトリガーにするためのステータス加算
                if new_title != current_title or new_content != current_content:
                    stats["sale_added"] += 1  # キャッシュクリアを強制発動するためのダミー加算
                for stat_key, log_msg in logs:
                    stats[stat_key] += 1
                    logger.info(log_msg)
            else:
                logger.warning(f"  ⚠️ 記事一括更新失敗: {pid}")

    # --- キャッシュクリア処理（追加） ---
    if stats["sale_added"] > 0 or stats["sale_removed"] > 0 or stats["rank_added"] > 0 or stats["rank_removed"] > 0:
        logger.info("  [WP] タグの更新があったため、KUSANAGIキャッシュをクリアします...")
        try:
            import subprocess
            subprocess.run("kusanagi bcache clear myblog && kusanagi fcache clear myblog", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
               WHERE status='published' AND post_type='regular' AND product_url != '' AND product_url IS NOT NULL"""
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
            new_desc, *_ = scrape_description(product_url, site=site_raw, genre=genre_raw)
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
