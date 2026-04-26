#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
過去記事アフィリエイトリンク文言 一括修正スクリプト
==========================================================
【概要】
  DBの post_type で通常記事/ランキング記事を100%正確に判別し、
  WP REST API で各記事の旧文言を新文言に置換する。

【置換ルール】
  ■ 通常記事 (post_type='regular'):
    テキストリンク: 「の詳細をチェック！」→「の試し読み・お得なセール状況をチェック！」
    ボタン: 「作品の詳細を見る」→「無料で試し読みする」
  ■ ランキング記事 (post_type='ranking'):
    テキストリンク: 「の詳細をチェック！」→「を試し読みする」

【実行方法】
  python update_affiliate_text.py --dry-run   # 事前確認（更新しない）
  python update_affiliate_text.py              # 本番実行
==========================================================
"""

import sys
import os
import re
import time
import json
import urllib.request
import urllib.parse
import sqlite3

# 既存のプロジェクト基盤を利用
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv

env_path = "/home/kusanagi/scripts/.env"
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()

from novelove_core import (
    db_connect, WP_SITE_URL,
    DB_FILE_UNIFIED,
)

import base64
WP_USER        = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
AUTH_HEADER = "Basic " + base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()

# === 置換ルール ===
REGULAR_REPLACEMENTS = [
    ("』の詳細をチェック！</a>", "』の試し読み・お得なセール状況をチェック！</a>"),
    (">作品の詳細を見る</a>", ">無料で試し読みする</a>"),
]
RANKING_REPLACEMENTS = [
    ("』の詳細をチェック！</a>", "』を試し読みする</a>"),
]


def get_published_posts_from_db():
    """v18.0.0: 統合DB1本から公開済み記事のproduct_id, wp_post_url, post_type を取得"""
    results = []
    conn = db_connect(DB_FILE_UNIFIED)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT product_id, wp_post_url, post_type FROM novelove_posts WHERE status='published' AND wp_post_url != ''"
    ).fetchall()
    for row in rows:
        results.append({
            "product_id": row["product_id"],
            "wp_post_url": row["wp_post_url"],
            "post_type": row["post_type"] or "regular",
        })
    conn.close()
    return results


def get_wp_post_id_by_slug(slug):
    """WP REST API でスラッグからWP投稿IDを取得"""
    url = f"{WP_SITE_URL}/wp-json/wp/v2/posts?" + urllib.parse.urlencode({
        "slug": slug, "_fields": "id"
    })
    req = urllib.request.Request(url, headers={
        "Authorization": AUTH_HEADER,
        "User-Agent": "Novelove-Backfill/1.0"
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data:
                return data[0]["id"]
    except Exception:
        pass
    return None


def get_wp_post_content(wp_post_id):
    """WP REST API で記事本文を取得"""
    url = f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}?" + urllib.parse.urlencode({
        "_fields": "content"
    })
    req = urllib.request.Request(url, headers={
        "Authorization": AUTH_HEADER,
        "User-Agent": "Novelove-Backfill/1.0"
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data["content"]["rendered"]


def update_wp_post_content(wp_post_id, new_content):
    """WP REST API で記事本文を更新"""
    url = f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}"
    payload = json.dumps({"content": new_content}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Authorization": AUTH_HEADER,
        "Content-Type": "application/json",
        "User-Agent": "Novelove-Backfill/1.0"
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status in (200, 201)


def apply_replacements(content, replacements):
    """置換ルールを適用し、(新しいcontent, 置換回数) を返す"""
    total_count = 0
    for old_text, new_text in replacements:
        count = content.count(old_text)
        if count > 0:
            content = content.replace(old_text, new_text)
            total_count += count
    return content, total_count


def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("=== DRY RUN モード（実際の更新は行いません） ===\n")
    else:
        print("=== 本番実行モード ===\n")

    # DBから公開済み記事一覧を取得
    posts = get_published_posts_from_db()
    print(f"DBから {len(posts)} 件の公開済み記事を取得しました。\n")

    regular_count = 0
    ranking_count = 0
    updated_count = 0
    skipped_count = 0
    error_count = 0

    for post in posts:
        pid = post["product_id"]
        wp_url = post["wp_post_url"]
        post_type = post["post_type"]
        slug = wp_url.rstrip("/").split("/")[-1]

        # DBの post_type で確実に判別
        if post_type == "ranking":
            replacements = RANKING_REPLACEMENTS
            type_label = "ランキング"
            ranking_count += 1
        else:
            replacements = REGULAR_REPLACEMENTS
            type_label = "通常"
            regular_count += 1

        # WP投稿IDを取得
        wp_post_id = get_wp_post_id_by_slug(slug)
        if not wp_post_id:
            print(f"  ⚠️ WP ID取得失敗: {pid} ({slug})")
            error_count += 1
            continue

        # 本文を取得
        try:
            content = get_wp_post_content(wp_post_id)
        except Exception as e:
            print(f"  ⚠️ 本文取得失敗: {pid} ({e})")
            error_count += 1
            continue

        # 置換実行
        new_content, replace_count = apply_replacements(content, replacements)

        if replace_count == 0:
            skipped_count += 1
            continue

        print(f"  [{type_label}] {pid} | WP:{wp_post_id} | 置換{replace_count}箇所", end="")

        if dry_run:
            print(" → (DRY RUN)")
            updated_count += 1
        else:
            try:
                ok = update_wp_post_content(wp_post_id, new_content)
                if ok:
                    print(" → ✅")
                    updated_count += 1
                else:
                    print(" → ❌ 更新失敗")
                    error_count += 1
                time.sleep(0.3)
            except Exception as e:
                print(f" → ❌ {e}")
                error_count += 1

        time.sleep(0.2)  # API負荷軽減

    print(f"\n{'='*50}")
    print(f"処理結果:")
    print(f"  通常記事:       {regular_count}件")
    print(f"  ランキング記事: {ranking_count}件")
    print(f"  更新成功:       {updated_count}件")
    print(f"  スキップ(変更なし): {skipped_count}件")
    print(f"  エラー:         {error_count}件")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
