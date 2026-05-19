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
import json
import random
import datetime
import requests
from atproto import Client, client_utils, models
from novelove_core import logger, DEEPSEEK_API_KEY

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

# --- 茉莉花のフォールバック定型コメント（DeepSeek失敗時に使用）---
_MARIKA_FALLBACKS = [
    "ちょっと聞いてーー！！この作品ヤバすぎて心臓もたない😭💖",
    "え待って、これは絶対みんなに共有しなきゃいけないやつだ…！💘",
    "発掘しちゃいました…！ヤバいやつ…ドキドキが止まらない😭✨",
    "これ読んで絶対後悔しないやつ…！早く続きが読みたすぎる💖🔥",
    "語彙力が消えるくらいヤバい作品を見つけてしまった…！😭💕",
    "思わず声に出してしまった…このドキドキ、みんなにも届け〜！💓✨",
    "ひとりで抱えきれないので共有させてください…！これヤバすぎる😭💖",
    "本当に心臓もたないんだけど…！こういう作品に出会いたかった…！💘",
    "見つけちゃった…絶対好きなやつ…ありがとう世界…！😭✨",
    "これは布教しないと一生後悔するやつだ…！みんな読んで…！💖🙌",
]


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


def _parse_tags(wp_tags_str: str) -> list:
    """wp_tagsからレビュアー名・専売系タグを除外してリストで返す。"""
    raw = [t.strip() for t in wp_tags_str.split(",") if t.strip()]
    return [
        t for t in raw
        if t not in REVIEWER_NAMES
        and not any(t.endswith(s) for s in EXCLUDE_SUFFIXES)
    ]


def _generate_marika_comment(title: str, excerpt: str, genre_label: str) -> str:
    """
    DeepSeekで茉莉花の一言コメント（30〜55字）を生成する。
    失敗時はフォールバック定型文を返す。
    """
    if not DEEPSEEK_API_KEY:
        return random.choice(_MARIKA_FALLBACKS)

    safe_excerpt = (excerpt or "")[:80]
    prompt = (
        f"あなたはSNSを担当している24歳のカフェ店員「茉莉花」です。\n"
        f"「全人類ハッピーエンド」が合言葉で、ピュアで甘々な溺愛展開をこよなく愛します。\n"
        f"友達に「これ絶対読んで！」と勧めるノリで、ときめきを全力で共有したがります。\n"
        f"口癖は「ヤバい」「心臓もたない」「ドキドキ」「尊い」。絵文字も使います。\n\n"
        f"以下の作品をBlueskyで紹介する、茉莉花の「思わず声に出た素直なリアクション」を\n"
        f"30〜55字で1文だけ書いてください。\n"
        f"ルール：①他のライター名は出さない ②BL/TLの専門分析はしない ③語尾は「…！」「！！」が多め\n\n"
        f"ジャンル: {genre_label}\n"
        f"タイトル: {title}\n"
        f"あらすじ冒頭: {safe_excerpt}\n\n"
        f"出力: 茉莉花の一言のみ（前置き不要）"
    )

    try:
        resp = requests.post(
            DEEPSEEK_API_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 100,
                "temperature": 0.9,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            comment = resp.json()["choices"][0]["message"]["content"].strip()
            # 長すぎる場合はフォールバック
            if len(comment) <= 60:
                logger.info(f"🔵 Bluesky: 茉莉花コメント生成完了 ({len(comment)}字)")
                return comment
            else:
                logger.warning(f"⚠️ Bluesky: 茉莉花コメントが長すぎる({len(comment)}字)。フォールバック使用。")
        else:
            logger.warning(f"⚠️ Bluesky: DeepSeek API エラー {resp.status_code}。フォールバック使用。")
    except Exception as e:
        logger.warning(f"⚠️ Bluesky: 茉莉花コメント生成失敗（続行）: {e}")

    return random.choice(_MARIKA_FALLBACKS)


def post_to_bluesky(
    title: str,
    genre: str,
    excerpt: str,
    url: str,
    wp_tags_str: str,
    image_url: str,
    is_r18: bool = False,
) -> bool:
    """
    茉莉花（SNS担当）としてBlueskyへ投稿する。
    Returns:
        True: 成功 / False: スキップまたは失敗
    """
    if not BLUESKY_HANDLE or not BLUESKY_PASSWORD:
        logger.warning("⚠️ Bluesky: 認証情報未設定のためスキップ")
        return False

    try:
        # --- ジャンルラベル ---
        g_lower = str(genre).lower()
        is_novel = "novel" in g_lower
        is_bl    = "bl" in g_lower
        if is_novel:
            g_label = "BL小説" if is_bl else "TL小説"
        else:
            g_label = "BL漫画" if is_bl else "TL漫画"

        # --- 茉莉花の一言コメント生成 ---
        marika_comment = _generate_marika_comment(title, excerpt, g_label)

        # --- あらすじのトリミング ---
        safe_excerpt = (excerpt or "")[:80]
        if len(excerpt or "") > 80:
            safe_excerpt += "…"

        # --- タグ処理 ---
        tags = _parse_tags(wp_tags_str or "")
        # 1つ目のタグ: 文中inline埋め込み候補
        inline_tag = tags[0] if tags else None
        # 残りのタグ: 末尾に #タグ として配置（最大3個）
        tail_tags = tags[1:4] if len(tags) > 1 else []

        # --- テキスト組み立て ---
        tb = client_utils.TextBuilder()
        # 茉莉花の一言（inline_tagが含まれていれば分割してtag埋め込み）
        if inline_tag and inline_tag in marika_comment:
            # コメント中のタグワードをinline facetに変換
            idx = marika_comment.index(inline_tag)
            before = marika_comment[:idx]
            after  = marika_comment[idx + len(inline_tag):]
            if before:
                tb.text(before)
            tb.tag(inline_tag, inline_tag)
            if after:
                tb.text(after)
        else:
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
