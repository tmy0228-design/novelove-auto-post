#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
novelove_bluesky.py — Novelove Bluesky自動投稿モジュール
==========================================================
【役割】
  WordPressへの投稿完了後、Blueskyへ自動投稿する。
  - 画像直接アップロード（アイキャッチ）
  - 成人向けコンテンツラベルの自動付与（is_r18=True時）
  - 返信完全ブロック（Threadgate）
  - タグのフィルタリング（専売・レビュアー名を除外）
  - セッションキャッシュ（毎回ログインしない）
  - 日本語フラグ（langs: ['ja']）の付与

【エラー方針】
  投稿失敗はログに記録するのみ。WordPress投稿処理は止めない。

【環境変数】（.envに記載）
  BLUESKY_HANDLE         : ハンドル名
  BLUESKY_APP_PASSWORD   : アプリパスワード
==========================================================
"""

import os
import json
import datetime
import requests
from atproto import Client, client_utils, models
from novelove_core import logger

# --- 認証情報（環境変数から取得）---
BLUESKY_HANDLE   = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD", "")

# セッションキャッシュファイル
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bluesky_session.json")

# Bluesky用タグ除外設定
REVIEWER_NAMES    = {"紫苑", "茉莉花", "葵", "桃香", "蓮"}
EXCLUDE_SUFFIXES  = ("専売", "限定", "独占")


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


def _filter_tags(wp_tags_str: str) -> str:
    """wp_tagsからレビュアー名・専売系タグを除外してハッシュタグ文字列を返す。"""
    raw = [t.strip() for t in wp_tags_str.split(",") if t.strip()]
    filtered = [
        t for t in raw
        if t not in REVIEWER_NAMES
        and not any(t.endswith(s) for s in EXCLUDE_SUFFIXES)
    ]
    return " ".join([f"#{t}" for t in filtered])


def post_to_bluesky(
    title: str,
    genre: str,
    excerpt: str,
    url: str,
    wp_tags_str: str,
    image_url: str,
    is_r18: bool = False
) -> bool:
    """
    Blueskyへ投稿する。
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

        # --- あらすじのトリミング ---
        safe_excerpt = (excerpt or "")[:100]
        if len(excerpt or "") > 100:
            safe_excerpt += "..."

        # --- タグ ---
        hashtags_str = _filter_tags(wp_tags_str or "")

        # --- テキスト組み立て ---
        tb = client_utils.TextBuilder()
        tb.text(f"【{g_label}】{title}\n\n")
        if safe_excerpt:
            tb.text(f"{safe_excerpt}\n\n")
        tb.text("▼ 作品の詳しい紹介はこちら\n")
        tb.link(url, url)
        if hashtags_str:
            tb.text(f"\n\n{hashtags_str}")

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
