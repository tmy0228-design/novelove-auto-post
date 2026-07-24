#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
novelove_bluesky.py — Novelove Bluesky自動投稿モジュール
==========================================================
【役割】
  WordPressへの投稿完了後、Blueskyへ自動投稿する。
  - SNS担当: 茉莉花（まりか）が中の人として投稿
  - DeepSeekで茉莉花らしい一言コメントを生成（失敗時は定型フォールバック）
  - 文中に1つタグをinline埋め込み（.tag()）、残りは末尾に配置
  - 成人向けコンテンツラベルの自動付与（is_r18=True時）
  - 返信完全ブロック（Threadgate）
  - セッションキャッシュ（毎回ログインしない）
  - 日本語フラグ（langs: ['ja']）の付与

【エラー方針】
  投稿失敗はログに記録するのみ。WordPress投稿処理は止めない。

【環境変数】（.envに記載）
  BLUESKY_HANDLE         : ハンドル名
  BLUESKY_APP_PASSWORD   : アプリパスワード
  DEEPSEEK_API_KEY       : DeepSeek API キー（茉莉花コメント生成用）
==========================================================
"""

import os
import re
import json
import datetime
import requests
from atproto import Client, client_utils, models
from novelove_core import logger, DEEPSEEK_API_KEY, get_source_db
from novelove_soul import REVIEWERS, first_person_prompt_line

# DLsiteフロア: 商業は先に判定（"bl-pro" が "bl" を含むため）
_DLSITE_COMMERCIAL_FLOORS = frozenset({"bl-pro", "girls-pro"})
_DLSITE_DOUJIN_FLOORS = frozenset({"bl", "girls", "home"})


def _extract_dlsite_floor(*urls):
    """product_url / affiliate_url（dlaf.jp/{floor}/…）から DLsite フロアを抽出。"""
    blob = " ".join(str(u or "") for u in urls).lower()
    if not blob:
        return None
    for fl in _DLSITE_COMMERCIAL_FLOORS:
        if fl in blob:
            return fl
    m = re.search(r"(?:dlsite\.com|dlaf\.jp)/([a-z0-9\-]+)/", blob)
    if m:
        fl = m.group(1)
        if fl in _DLSITE_COMMERCIAL_FLOORS or fl in _DLSITE_DOUJIN_FLOORS:
            return fl
    return None


def classify_is_doujin_market(
    *,
    genre="",
    site="",
    source_db="",
    wp_tags_str="",
    product_url="",
    affiliate_url="",
    author_detail="",
):
    """
    同人=True / 商業=False。
    Bluesky投稿本文の url（novelove.jp）では判定しない。
    優先順: source_db・サイト → DLsiteフロア(URL) → genre doujin_* → author_detail。
    """
    site_raw = str(site or "")
    site_key = site_raw.split(":")[0].strip() if site_raw else ""
    tags = str(wp_tags_str or "")
    g = str(genre or "").lower()
    detail = str(author_detail or "")

    sdb = (source_db or "").strip().lower()
    if not sdb and site_key:
        sdb = get_source_db(site_key)

    # 1) サイト確定（らぶカル=同人 / DMMブックス=商業）
    if (
        sdb == "lovecal"
        or site_key == "Lovecal"
        or "らぶカル" in tags
        or "lovecul.dmm.co.jp" in str(product_url or "").lower()
    ):
        return True
    if sdb == "dmm" or site_key in ("DMM.com", "DMM"):
        return False

    # 2) DLsite フロア（product_url / dlaf affiliate）
    floor = _extract_dlsite_floor(product_url, affiliate_url)
    if floor in _DLSITE_COMMERCIAL_FLOORS:
        return False
    if floor in _DLSITE_DOUJIN_FLOORS:
        return True

    # 3) genre が doujin_*（漫画同人など）
    if g.startswith("doujin") or "doujin_" in g:
        return True

    # 4) メタ: サークル=同人、出版社/レーベルのみ=商業
    if "サークル:" in detail or "サークル：" in detail:
        return True
    has_pub = any(k in detail for k in ("出版社:", "出版社：", "レーベル:", "レーベル："))
    has_circle = "サークル:" in detail or "サークル：" in detail
    if has_pub and not has_circle:
        return False

    # 5) 不明時は商業（誤って同人タグを付けるより安全）＋警告
    logger.warning(
        f"⚠️ Bluesky同人/商業: 判定材料不足のため商業扱い "
        f"(site={site_key!r}, genre={genre!r}, floor={floor!r})"
    )
    return False

# --- 認証情報（環境変数から取得）---
BLUESKY_HANDLE   = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD", "")

# セッションキャッシュファイル
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bluesky_session.json")

# Bluesky用タグ除外設定
REVIEWER_NAMES    = {"紫苑", "茉莉花", "葵", "桃香", "蓮"}
EXCLUDE_SUFFIXES  = ("専売", "限定", "独占")

# DeepSeek API
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"




def _get_client() -> Client:
    """セッションをディスクから復元。なければ新規ログインしてキャッシュ保存。"""
    client = Client()
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            client.login(session_string=data.get("session_string"))
            logger.info("🔵 Bluesky: 既存セッションを復元")
            return client
        except Exception as e:
            logger.warning(f"⚠️ Bluesky: セッション復元失敗、新規ログイン: {e}")

    client.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)
    logger.info("🔵 Bluesky: 新規ログイン完了")
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({"session_string": client.export_session_string()}, f)
    except Exception as e:
        logger.warning(f"⚠️ Bluesky: セッション保存失敗: {e}")
    return client


def _parse_tags(wp_tags_str: str, exclude_extra=None) -> list:
    """wp_tagsからレビュアー名・専売系タグを除外してリストで返す。
    exclude_extra: 追加で除外する名前（声優/サークル/作者など。SNSでは属性タグを優先し人名/サークル名は出さない）。
    """
    raw = [t.strip() for t in wp_tags_str.split(",") if t.strip()]
    extra = set(exclude_extra or [])
    return [
        t for t in raw
        if t not in REVIEWER_NAMES
        and t not in extra
        and not any(t.endswith(s) for s in EXCLUDE_SUFFIXES)
    ]


def _generate_marika_comment(title: str, excerpt: str, genre_label: str) -> str:
    """
    DeepSeekで茉莉花の紹介コメント（80文字程度、2文以内）を生成する。
    novelove_soul.pyのペルソナ設定を活用し、表現はAIに委ねる。
    失敗時は空文字を返す（定型文は使用しない）。
    """
    if not DEEPSEEK_API_KEY:
        return ""

    # novelove_soul.py から茉莉花のペルソナを取得
    marika = next((r for r in REVIEWERS if r["id"] == "marika"), None)
    personality = marika["personality"] if marika else ""
    tone = marika["tone"] if marika else ""
    fp_line = first_person_prompt_line(marika) if marika else ""
    fp_block = f"【{fp_line}】\n" if fp_line else ""

    safe_excerpt = (excerpt or "")[:120]
    prompt = (
        f"あなたはSNSを担当している「茉莉花」です。\n"
        f"【キャラクター】\n{personality}\n"
        f"【口調】\n{tone}\n"
        f"{fp_block}\n"
        f"以下の作品をBlueskyで軽く紹介するコメントを書いてください。\n"
        f"ルール：\n"
        f"・80文字程度、2文以内で\n"
        f"・友達におすすめする感覚で、作品のあらすじに沿った具体的な魅力に触れること\n"
        f"・他のライター名は出さない\n"
        f"・一人称はキャラ設定どおりに固定すること\n"
        f"・毎回同じような言い回しにならないよう、作品の内容に合わせて自由に表現すること\n\n"
        f"ジャンル: {genre_label}\n"
        f"タイトル: {title}\n"
        f"あらすじ: {safe_excerpt}\n\n"
        f"出力: 茉莉花の紹介コメントのみ（前置き不要）"
    )

    try:
        resp = requests.post(
            DEEPSEEK_API_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0.9,
                "thinking": {"type": "disabled"},
            },
            timeout=15,
        )
        if resp.status_code == 200:
            comment = (resp.json()["choices"][0]["message"]["content"] or "").strip()
            # カギカッコで囲まれている場合は除去
            if comment.startswith("「") and comment.endswith("」"):
                comment = comment[1:-1]
            if len(comment) >= 5:
                logger.info(f"🔵 Bluesky: 茉莉花コメント生成完了 ({len(comment)}字)")
                return comment
            else:
                logger.warning("⚠️ Bluesky: 茉莉花コメントが短すぎる。コメントなしで投稿。")
        else:
            logger.warning(f"⚠️ Bluesky: DeepSeek API エラー {resp.status_code}。コメントなしで投稿。")
    except Exception as e:
        logger.warning(f"⚠️ Bluesky: 茉莉花コメント生成失敗（続行）: {e}")

    return ""


def post_to_bluesky(
    title: str,
    genre: str,
    excerpt: str,
    url: str,
    wp_tags_str: str,
    image_url: str,
    is_r18: bool = False,
    exclude_extra=None,
    site: str = "",
    source_db: str = "",
    product_url: str = "",
    affiliate_url: str = "",
    author_detail: str = "",
) -> bool:
    """
    茉莉花（SNS担当）としてBlueskyへ投稿する。
    Returns:
        True: 成功 / False: スキップまたは失敗
    Note:
        url は WP記事URL（投稿本文用）。同人/商業判定には使わない。
    """
    if not BLUESKY_HANDLE or not BLUESKY_PASSWORD:
        logger.warning("⚠️ Bluesky: 認証情報未設定のためスキップ")
        return False

    try:
        # --- ジャンルラベル ---
        g_lower = str(genre).lower()
        is_novel = "novel" in g_lower
        is_voice = "voice" in g_lower  # v19.0.0
        is_bl    = "bl" in g_lower
        if is_voice:
            g_label = "BLボイス" if is_bl else "TLボイス"
        elif is_novel:
            g_label = "BL小説" if is_bl else "TL小説"
        else:
            g_label = "BL漫画" if is_bl else "TL漫画"

        # --- 茉莉花の紹介コメント生成 ---
        marika_comment = _generate_marika_comment(title, excerpt, g_label)

        # --- タグ処理 (同人/商業: v21.7.11 サイト・フロア決定的判定) ---
        tags = _parse_tags(wp_tags_str or "", exclude_extra=exclude_extra)

        is_doujin_market = classify_is_doujin_market(
            genre=genre,
            site=site,
            source_db=source_db,
            wp_tags_str=wp_tags_str or "",
            product_url=product_url,
            affiliate_url=affiliate_url,
            author_detail=author_detail,
        )
        market_tag = f"{g_label[:2]}同人" if is_doujin_market else f"商業{g_label[:2]}"
        logger.info(
            f"🔵 Bluesky市場タグ: #{market_tag} "
            f"(doujin={is_doujin_market}, site={site!r}, genre={genre!r})"
        )

        # タグ重複防止
        tail_tags = [t for t in tags if t not in (market_tag, "同人", "商業", "商業BL", "商業TL", "BL同人", "TL同人")][:2]
        tail_tags.insert(0, market_tag)

        # --- あらすじの動的トリミング（茉莉花コメント優先） ---
        # 固定部分の文字数を計算し、残りをあらすじに割り当てる
        title_line = f"\n\n【{g_label}】{title}\n"
        link_line = "\n▼ 詳しくはこちら\n"
        tag_text = ""
        if tail_tags:
            tag_text = "\n\n" + " ".join(f"#{t}" for t in tail_tags)
        fixed_len = len(marika_comment) + len(title_line) + len(link_line) + len(url) + len(tag_text)
        remaining = 300 - fixed_len - 4  # 4 = 「」\n + マージン

        safe_excerpt = ""
        if remaining >= 20 and excerpt:
            safe_excerpt = (excerpt or "")[:remaining]
            if len(excerpt or "") > remaining:
                safe_excerpt = safe_excerpt[:-1] + "…"

        # --- テキスト組み立て ---
        tb = client_utils.TextBuilder()

        # 茉莉花のコメント（ある場合のみ）
        if marika_comment:
            tb.text(marika_comment)

        tb.text(f"\n\n【{g_label}】{title}\n")

        if safe_excerpt:
            tb.text(f"「{safe_excerpt}」\n")

        tb.text("\n▼ 詳しくはこちら\n")
        tb.link(url, url)

        # 末尾タグ
        if tail_tags:
            tb.text("\n\n")
            for i, t in enumerate(tail_tags):
                if i > 0:
                    tb.text(" ")
                tb.tag(f"#{t}", t)

        # --- Bluesky接続 ---
        client = _get_client()

        # --- 画像アップロード ---
        embed = None
        if image_url:
            try:
                img_resp = requests.get(image_url, timeout=15)
                if img_resp.status_code == 200:
                    upload = client.com.atproto.repo.upload_blob(img_resp.content)
                    embed = models.AppBskyEmbedImages.Main(
                        images=[models.AppBskyEmbedImages.Image(
                            alt=f"{title} の表紙",
                            image=upload.blob,
                        )]
                    )
                    logger.info("🔵 Bluesky: 画像アップロード完了")
            except Exception as e:
                logger.warning(f"⚠️ Bluesky: 画像アップロード失敗（続行）: {e}")

        # --- 投稿レコード作成 ---
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        record = models.AppBskyFeedPost.Record(
            created_at=now_iso,
            text=tb.build_text(),
            facets=tb.build_facets(),
            embed=embed,
            langs=["ja"],
        )

        # 成人向けラベル付与
        if is_r18:
            record.labels = models.ComAtprotoLabelDefs.SelfLabels(
                values=[models.ComAtprotoLabelDefs.SelfLabel(val="porn")]
            )
            logger.info("🔵 Bluesky: 成人向けラベル付与")

        # --- 投稿送信 ---
        post = client.com.atproto.repo.create_record(
            models.ComAtprotoRepoCreateRecord.Data(
                repo=client.me.did,
                collection="app.bsky.feed.post",
                record=record,
            )
        )
        post_uri = post.uri
        rkey     = post_uri.split("/")[-1]
        logger.info(f"✅ Bluesky投稿完了: {post_uri}")

        # --- 返信ブロック（Threadgate）---
        tg_record = models.AppBskyFeedThreadgate.Record(
            post=post_uri,
            allow=[],
            created_at=now_iso,
        )
        client.com.atproto.repo.create_record(
            models.ComAtprotoRepoCreateRecord.Data(
                repo=client.me.did,
                collection="app.bsky.feed.threadgate",
                rkey=rkey,
                record=tg_record,
            )
        )
        logger.info("🔵 Bluesky: 返信ブロック設定完了")
        return True

    except Exception as e:
        logger.error(f"🚨 Bluesky投稿エラー: {e}")
        return False
