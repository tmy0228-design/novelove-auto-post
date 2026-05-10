#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bluesky テスト投稿スクリプト（実データ版）
"""

import os
import json
import datetime
import requests
from atproto import Client, client_utils, models
from dotenv import load_dotenv

# .envから認証情報を読み込む（サーバー環境に合わせたパス）
_env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(_env_path):
    load_dotenv(_env_path)
else:
    load_dotenv()

HANDLE   = os.environ.get("BLUESKY_HANDLE", "novelove-official.bsky.social")
PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD", "dic6-wyt3-ib6e-enlj")

# セッションキャッシュファイル（毎回ログインせず再利用する）
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bluesky_session.json")

def get_client() -> Client:
    """セッションをディスクから復元し、なければ新規ログインする"""
    client = Client()
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                session_data = json.load(f)
            client.login(session_string=session_data.get("session_string"))
            print("既存セッションを復元しました")
            return client
        except Exception as e:
            print(f"セッション復元失敗、新規ログインします: {e}")

    client.login(HANDLE, PASSWORD)
    print("新規ログイン完了")
    # セッションを保存
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({"session_string": client.export_session_string()}, f)
    except Exception as e:
        print(f"セッション保存失敗（次回も新規ログインになります）: {e}")
    return client

def main():
    # 実際の記事データ
    title      = "トラップエルフ3"
    g_label    = "BL漫画"
    excerpt    = "触手や産卵のファンタジーBLが好きな方に。年下攻めデリクとエルフのエルフィス、互いへの気持ちが変わる様子に注目。"
    url        = "https://novelove.jp/rj01593213/"
    image_url  = "https://img.dlsite.jp/resize/images2/work/doujin/RJ01594000/RJ01593213_img_main_300x300.webp"
    # wp_tagsそのまま（実際の投稿でDBから取得されるデータを想定）
    wp_tags_raw = "DLsite, DLsite専売, 執着, 年下攻め, 運命, 紫苑"
    is_r18     = True  # BL成人向けのためTrueに設定

    # Bluesky用タグフィルタリング
    # 除外1: レビュアー名（AI臭対策）
    REVIEWER_NAMES = {"紫苑", "茉莉花", "葵", "桃香", "蓮"}
    # 除外2: 「専売・限定・独占」系のSNS検索需要の低いタグ
    EXCLUDE_SUFFIXES = ("専売", "限定", "独占")
    
    raw_tags = [t.strip() for t in wp_tags_raw.split(",") if t.strip()]
    filtered_tags = [
        t for t in raw_tags
        if t not in REVIEWER_NAMES
        and not any(t.endswith(s) for s in EXCLUDE_SUFFIXES)
    ]
    hashtags_str = " ".join([f"#{t}" for t in filtered_tags])

    # 投稿テキスト組み立て
    text_builder = client_utils.TextBuilder()
    text_builder.text(f"【{g_label}】{title}\n\n")
    text_builder.text(f"{excerpt}\n\n")
    text_builder.text("▼ 作品の詳しい紹介はこちら\n")
    text_builder.link(url, url)
    text_builder.text(f"\n\n{hashtags_str}")

    print("=== 投稿プレビュー ===")
    print(text_builder.build_text())
    print("===================\n")

    # Bluesky接続（セッション再利用）
    print("Bluesky接続中...")
    client = get_client()

    # 画像ダウンロード＆アップロード
    print(f"画像取得中: {image_url}")
    img_resp = requests.get(image_url, timeout=15)
    img_data = img_resp.content
    upload = client.com.atproto.repo.upload_blob(img_data)
    print("画像アップロード完了")

    # 埋め込み画像の作成
    embed = models.AppBskyEmbedImages.Main(
        images=[models.AppBskyEmbedImages.Image(
            alt=f"{title} の表紙",
            image=upload.blob,
        )]
    )

    # 成人向けラベル付与
    labels = models.ComAtprotoLabelDefs.SelfLabels(
        values=[models.ComAtprotoLabelDefs.SelfLabel(val="porn")]
    )

    # 投稿レコード作成
    record = models.AppBskyFeedPost.Record(
        createdAt=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        text=text_builder.build_text(),
        facets=text_builder.build_facets(),
        embed=embed,
        labels=labels,
        langs=["ja"],  # 日本語フラグ
    )

    print("投稿送信中...")
    post = client.com.atproto.repo.create_record(
        models.ComAtprotoRepoCreateRecord.Data(
            repo=client.me.did,
            collection="app.bsky.feed.post",
            record=record
        )
    )
    post_uri = post.uri
    rkey = post_uri.split("/")[-1]
    print(f"投稿完了: {post_uri}")

    # 返信ブロック（Threadgate）
    print("返信ブロック設定中...")
    tg_record = models.AppBskyFeedThreadgate.Record(
        post=post_uri,
        allow=[],
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    client.com.atproto.repo.create_record(
        models.ComAtprotoRepoCreateRecord.Data(
            repo=client.me.did,
            collection="app.bsky.feed.threadgate",
            rkey=rkey,
            record=tg_record
        )
    )
    print("返信ブロック完了！")
    print("\n実際のアカウントで確認してみてください！")

if __name__ == "__main__":
    main()
