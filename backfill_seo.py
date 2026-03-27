#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
過去の全記事に対してDeepSeek APIを使用し、
最新のロジック（通常/ランキング別）に沿ったSEOタイトル・メタディスクリプションを再生成して、
CocoonテーマのSEOメタフィールドを上書き更新するバッチスクリプトです。
"""

import os
import json
import subprocess
import time
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# DeepSeek API設定
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

# WP-CLI設定
# スクリプト実行者の環境に合わせて調整してください
DOC_ROOT = "/home/kusanagi/novelove/DocumentRoot"
WP_CMD = "wp"

def run_wp_cli(cmd_list):
    """WP-CLIコマンドを実行し、結果の標準出力を返す"""
    full_cmd = [WP_CMD] + cmd_list + ["--path=" + DOC_ROOT, "--allow-root"]
    try:
        res = subprocess.run(full_cmd, capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"[Error] WP-CLI Error: {' '.join(full_cmd)}\n{e.stderr}")
        return None

def get_all_posts():
    """公開済みの全記事リスト（ID, post_title, post_content）を取得"""
    print("公開済みの記事一覧を取得しています...")
    # JSON形式で出力させてパースする
    json_str = run_wp_cli(["post", "list", "--post_type=post", "--post_status=publish", "--posts_per_page=-1", "--format=json", "--fields=ID,post_title,post_content"])
    if not json_str:
        return []
    try:
        posts = json.loads(json_str)
        return posts
    except json.JSONDecodeError:
        print("[Error] Failed to parse WP-CLI JSON output.")
        return []

def strip_html(html_str):
    """HTMLタグを除去し、プレーンテキストを抽出"""
    return BeautifulSoup(html_str, "html.parser").get_text(separator=" ", strip=True)

def generate_seo_meta(title, content):
    """DeepSeekを用いて記事本文からSEOタイトルとメタディスクリプションを生成"""
    
    # 本文が長すぎる場合は先頭2000文字程度に制限（APIコスト・コンテキスト長削減のため）
    plain_content = strip_html(content)[:2000]
    
    is_ranking = "ランキング" in title or "TOP" in title.upper()
    
    if is_ranking:
        prompt = f"""
以下のランキング記事の本文を読み、Google検索エンジン向けの魅力的なSEOメタデータを生成してください。

【記事タイトル】
{title}

【記事本文】
{plain_content}

【出力ルール】
・⚠️記事本文に存在しない情報（架空の作品名、設定、展開など）は絶対に記述しないでください。
・必ず以下の形式で、値のみを出力してください。それ以外の余計な文章は一切不要です。
SEO_META:
seo_title=（32文字以内。ランキングの魅力が伝わるキャッチーなタイトル。「絶対に外さない」「おすすめ」等の惹句を入れる。末尾に「| Novelove」等は不要）
meta_desc=（60〜80文字程度。本文に登場する具体的な作品の傾向や要素を拾い、どんな人におすすめかを端的に伝える。嘘は書かないこと。）
"""
    else:
        # 通常の単体作品紹介記事
        prompt = f"""
以下の作品紹介記事の本文を読み、Google検索エンジン向けの魅力的なSEOメタデータを生成してください。

【記事タイトル】
{title}

【記事本文】
{plain_content}

【出力ルール】
・⚠️記事本文に存在しない情報（架空の作品名、属性、展開など）は絶対に記述しないでください。
・必ず以下の形式で、値のみを出力してください。それ以外の余計な文章は一切不要です。
SEO_META:
seo_title=（32文字以内。あらすじにある具体的な設定・属性・キーワードを使うこと。読者の感情を揺さぶる言葉で表現する。末尾に「| Novelove」等は不要）
meta_desc=（60〜80文字程度。あらすじの魅力を端的に伝える文章。検索者が読みたくなるようなフックを入れること。）
"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "あなたはプロのSEOライターです。指定されたルールに従って検索エンジン向けの最適化された出力を行ってください。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }

    try:
        req = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30)
        req.raise_for_status()
        res_json = req.json()
        ai_response = res_json.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        # 応答からパース
        seo_title = ""
        meta_desc = ""
        
        if "SEO_META:" in ai_response:
            parts = ai_response.split("SEO_META:")
            lines = parts[1].strip().splitlines()
        else:
            lines = ai_response.strip().splitlines()
            
        for line in lines:
            line = line.strip()
            if line.startswith("seo_title="):
                seo_title = line.replace("seo_title=", "").strip().strip('「」')
            elif line.startswith("meta_desc="):
                meta_desc = line.replace("meta_desc=", "").strip().strip('「」')
                
        return seo_title, meta_desc

    except Exception as e:
        print(f"  [Error] DeepSeek API Request Failed: {e}")
        return "", ""

def main():
    if not DEEPSEEK_API_KEY:
        print("[Error] DEEPSEEK_API_KEY is not set.")
        return

    posts = get_all_posts()
    print(f"対象記事総数: {len(posts)} 件")
    
    success_count = 0
    
    for i, post in enumerate(posts, 1):
        post_id = post.get("ID")
        title = post.get("post_title")
        content = post.get("post_content")
        
        print(f"\n[{i}/{len(posts)}] ID:{post_id} 「{title}」の処理を開始します...")
        
        seo_title_body, meta_desc = generate_seo_meta(title, content)
        
        if not seo_title_body or not meta_desc:
            print("  [Warning] SEOデータの生成に失敗したか、内容が空でした。スキップします。")
            continue
            
        # 最新のロジックを適用: 『作品名』 + AI生成キャッチコピー
        # 32文字を超えた分はDeepSeek側で制限しているが、念の為安全に結合
        final_seo_title = f"『{title}』{seo_title_body}"
        if len(final_seo_title) > 68:
            final_seo_title = final_seo_title[:68] + "…"
            
        print(f"  -> 生成SEOタイトル: {final_seo_title}")
        print(f"  -> 生成メタ記述: {meta_desc[:30]}...")
        
        # WP-CLIでアップデート実行
        res1 = run_wp_cli(["post", "meta", "update", str(post_id), "the_page_seo_title", final_seo_title])
        res2 = run_wp_cli(["post", "meta", "update", str(post_id), "the_page_meta_description", meta_desc])
        
        if res1 is not None and res2 is not None:
            print("  -> DB更新完了！")
            success_count += 1
        else:
            print("  -> [Error] DB更新に失敗しました。")
            
        # APIレートリミット対策で少し待機
        time.sleep(1.5)
        
    print(f"\n--- 処理完了 ---")
    print(f"更新成功: {success_count} / {len(posts)} 件")

if __name__ == "__main__":
    main()
