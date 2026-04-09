#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
Novelove 自動投稿エンジン v13.8.1
【多重投稿ループ停止・データフロー修復・堅牢性強化】
==========================================================
【変更点 v11.4.8】
 - 修正: `get_internal_link` の動的SQL構築時、SQLite特有の ORDER BY 0 インデックスエラーを修正
【変更点 v11.4.7】
 - 修正: 投稿失敗時（画像設定エラー時等）の status 更新漏れを修正し、多重投稿を完全停止
 - 改善: SELECT * を廃止し、カラム名を明示指定することで将来の不整合リスクを排除
 - 機能: 投稿直前のタイトル重複チェック（24hガードレール）を追加
 - 改善: WP-CLI エラーログに stderr（詳細メッセージ）を含めるように変更
【変更点 v11.4.6】
 - 修正: 画像設定（FIFU）のタイムアウトを 15秒 ➔ 60秒に延長
 - 機能: 画像設定失敗時に WP 投稿を自動削除（ロールバック）するガードレールを実装
【変更点 v11.4.5】
 - 修正: _check_image_ok 関数のインポート漏れ（NameError）を修正
【変更点 v11.4.4】
 - 修正: _call_deepseek_raw 関数の消失を復元
 - 修正: クールダウン判定の時刻計算バグ（utcnow -> now）を修正
【変更点 v11.4.3】
 - 構造: 取得ロジックを novelove_fetcher.py へ完全分離
 - 修正: ランキング機能（fetch_ranking_...）でのインポート漏れを修正
==========================================================
【変更点 v11.3.5】
 - 改善: プロンプト刷新（感情モード/事実性ガード/NGフレーズ集）
【変更点 v10.1.0】
 - 修正: DigiKet等のエンコーディング判定を強化（文字化け解消）
 - 修正: FIFUアイキャッチ設定のメタキーを修正（画像欠落解消）
 - 修正: 関連記事（内部リンク）の取得ロジックを強化・安定化
 - 修正: AIタグ抽出を「部分一致マッチング」に改良（タグ消失解消）
 - 修正: ログ/出力時のエンコーディング例外対策（強制終了防止）
 - 機能: WordPress記事IDをDB（wp_post_id）に保存する機能を追加
 - 統合: ジャンル・サイト・AI・R18の4層タグ構成を標準化
==========================================================
【変更点 v9.5.3】
 - 修正: scrape_description()内のdigiket呼び出しをタプル対応に修正
【変更点 v9.4.0】
 - 機能: FIFU外部リンク化（画像アップロード廃止）
==========================================================
"""
import random
import difflib
import subprocess
import requests
import os
import urllib.parse
import sqlite3
import time
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import argparse

# --- Discord通知機能 ---
# --- ライター性格設定・執筆ルール（novelove_soul.py に分離管理）---
from novelove_soul import REVIEWERS, MOOD_PATTERNS, FACT_GUARD, NG_PHRASES, get_relationship

from novelove_core import (
    logger, ERROR_LABELS, notify_discord,
    DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET,
    get_affiliate_button_html,
    _get_reviewer_for_genre, _genre_label,
    get_db_path, db_connect, init_db, get_genre_index, save_genre_index,
    WP_SITE_URL,
    MAIN_LOCK_FILE, RANK_LOCK_FILE,
    is_emergency_stop, trigger_emergency_stop,
    DEEPSEEK_API_KEY, WP_USER, WP_APP_PASSWORD,
    DMM_API_ID, DMM_AFFILIATE_API_ID, DMM_AFFILIATE_LINK_ID,
    DLSITE_AFFILIATE_ID, DIGIKET_AFFILIATE_ID,
    WP_PHP_PATH, WP_CLI_PATH, WP_DOC_ROOT,
)

# === 取得ロジックは novelove_fetcher.py に分離 ===
from novelove_fetcher import (
    fetch_and_stock_all,
    fetch_digiket_items,
    FETCH_TARGETS,
    AI_TAG_WHITELIST,
    mask_input,
    scrape_description,
    scrape_digiket_description,
    _is_noise_content,
    _check_image_ok,
)

# === 設定欄（環境変数は novelove_core.py で一元管理・import済み） ===
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = "deepseek-chat"

# FETCH_TARGETS は novelove_fetcher.py で定義・管理（import 済み）


# mask_input / MASK_*_MAP は novelove_fetcher.py で定義・管理（import 済み）
# AI_TAG_WHITELIST は novelove_fetcher.py で定義・管理（import 済み）
# キャラクター設定 (novelove_soul.py で管理・import済み)



def _evaluate_article_potential(title, description, original_tags=""):
    """
    執筆前にあらすじ情報だけを元に、「情報量と面白さ」で記事化ポテンシャルを1〜5点で評価する。
    1〜2: 不採用（即スキップ）、3: ショート記事採用、4: 標準記事採用、5: 特大記事採用。
    """
    if not description or len(description.strip()) < 50:
        return 2  # 情報が少なすぎる場合はAPIを叩かずに即終了
    
    prompt = f"""
以下のタイトルとあらすじを読み、「嘘や補完なしで、充実した紹介記事が書ける情報量があるか」を1〜5点で評価してください。

【必須の審査ルール】
・「物語や世界観の設定」「キャラクターのプロフィール」「フェティッシュな属性・性癖」「プレイや描写内容の箇条書き」は、記事の価値が高い有用な情報として加点してください。
・「ファイル形式・価格・特典」「サークルやスタッフ名」「他作品の宣伝・URL」等の宣伝メタ情報は、完全なノイズとして無視してください。

5: 有用な情報が極めて豊富。嘘なく深い分析記事（約2000字）が余裕で書ける。
4: 情報量は標準的かつ十分。手堅い紹介記事（約1000字）が書ける。
3: 情報は少ないが設定に魅力がある。コンパクトな紹介（約500字）なら書ける。
2: 情報が薄い、またはノイズが大半で記事化が困難。
1: 判定不能・外国語。

出力形式: 1〜5の数字1文字のみ

タイトル: {title}
あらすじ: {description[:1000]}
{f"公式属性タグ: {original_tags}" if original_tags else ""}
"""
    messages = [
        {"role": "system", "content": "あなたはプロの編集者です。情報量と面白さだけで厳密に審査してください。"},
        {"role": "user", "content": prompt}
    ]
    # トークンを極限まで絞って即座に結果を返す（AIの無駄話で数字が途切れないようマージンを取る）
    content, err = _call_deepseek_raw(messages, max_tokens=50, temperature=0.3)
    if err != "ok" or not content:
        return 0

    match = re.search(r"[1-5]", content)
    if match:
        return int(match.group())
    return 0

# === AI執筆 ===
def build_prompt(target, reviewer, mask_level=0, is_novel=False, is_guest=False, mood="", ai_score=4, original_tags="", is_exclusive=False):
    safe_title = mask_input(target["title"], mask_level)
    safe_desc  = mask_input(target["description"], mask_level)
    chat_open  = f'<div class="speech-bubble-left"><img src="/wp-content/uploads/icons/{reviewer["face_image"]}.png" alt="{reviewer["name"]}" /><div class="speech-text">'
    chat_close = '</div></div>'

    focus = reviewer.get("novel_focus", "") if is_novel else reviewer.get("manga_focus", "")
    medium_label = "小説・ノベル" if is_novel else "漫画・コミック"

    novel_rules = ""
    if is_novel:
        novel_rules = (
            "\n[小説・ノベル作品の執筆ルール]"
            "\n「コマ」「見開き」「絵」「描画」「ページ」など漫画特有の表現は一切使わないこと。"
            "\n代わりに「文章」「心理描写」「行間」「表現」「語彙」「文体」「語り口」「読了感」など活字特有の視点で話すこと。"
        )

    guest_hint = ""
    if is_guest:
        guest_hint = (
            f"\n[ゲスト紹介設定]"
            f"\n{reviewer['name']}は通常とは別のジャンルを担当しているが、今回は特別にこの作品を紹介することになった。"
            f"\n自分の専門外だからこそ気付く「新鮮な視点」を活かして紹介記事を書くこと。専門的になりすぎず、自分らしい言葉で正直に感想を伝えること。"
        )

    voice_hint = "\n※当サイトは漫画・小説専門です。「聴く」「イヤホン」などの音声表現は避け、「読む・見る」体験として紹介してください。"

    mood_note = f"\n今回の感情モード: {mood}" if mood else ""
    _tag_rule_nl = "\n"
    _tag_rule_str = (_tag_rule_nl + "[公式属性タグの活用]" + _tag_rule_nl +
        "作品には以下の公式属性タグが設定されています: " + original_tags + _tag_rule_nl +
        "これらを活かして、具体的で読者に刺さる紹介文・おすすめコメントを書いてください。" +
        "ファイル形式等の形式情報は無視し、内容・属性に関わる情報のみを参考にしてください。"
    ) if original_tags else ""
    
    # === マンネリ化防止ロジック（10%の確率で設定の身の上話を引き出す） ===
    if random.random() < 0.1:
        intro_rule = f"冒頭の挨拶では、あなたのキャラクター設定にある身の上話（例: {reviewer.get('greeting', '')}）を自然に絡めてください。"
    else:
        intro_rule = "冒頭の挨拶では、あなたの年齢や職業等といった自己紹介・身の上話をするのは【絶対に禁止】します。" \
                     "あなたの【キャラクターの口調や口癖だけ】を維持したまま、作品のあらすじに対する新鮮なリアクションだけで書き出してください。"

    # === スコア別構成制御ロジック ===
    if ai_score >= 5:
        # スコア5：2000文字規模の特大フルダイブ記事
        html_structure = f"""
{chat_open}（60〜80字程度。{intro_rule}）{chat_close}
<h2>（作品の世界観や魅力を引き出すキャッチーな見出し）</h2>
<p>（標準語で執筆）あらすじ・世界観・作品の属性情報。提供されたすべての有用な情報（シチュエーション・キャラ属性・プレイ内容の箇条書きを含む）を深く噛み砕いて400〜700字程度でリッチに解説。既にある設定の魅力を別の角度から掘り下げたり、読者の期待を煽る表現で膨らませること。</p>
{chat_open}（50〜70字程度。設定への熱いリアクション）{chat_close}
<h2>キャラクターの魅力と関係性</h2>
<p>（標準語で執筆）キャラクターの性格、2人の関係性がどう変化するかなど、深い分析を400〜700字程度で執筆。</p>
{chat_open}（50〜70字程度。キャラ愛や尊さへのリアクション）{chat_close}
<h2>見どころ</h2>
<ul>
  <li><strong>（魅力ポイント1）</strong>：（標準語で執筆）魅力を具体的に。</li>
  <li><strong>（魅力ポイント2）</strong>：（標準語で執筆）魅力を具体的に。</li>
  <li><strong>（魅力ポイント3）</strong>：（標準語で執筆）魅力を具体的に。</li>
</ul>
<h2>こんな人におすすめ</h2>
<ul style="list-style-type: none; padding-left: 0;">
  <li>✅ （標準語で執筆）おすすめの層1</li>
  <li>✅ （標準語で執筆）おすすめの層2</li>
  <li>✅ （標準語で執筆）おすすめの層3</li>
</ul>
{chat_open}（100〜120字程度の熱い総評・布教）{chat_close}
"""
    elif ai_score == 4:
        # スコア4：1000文字規模の標準安定記事
        html_structure = f"""
{chat_open}（60〜80字程度。{intro_rule}）{chat_close}
<h2>（作品の世界観や魅力を引き出すキャッチーな見出し）</h2>
<p>（標準語で執筆）あらすじ・世界観・作品の属性情報。提供されたすべての有用な情報（シチュエーション・キャラ属性・プレイ内容の箇条書きを含む）を300〜600字程度で解説。既にある設定の魅力を別の角度から掘り下げたり、読者の期待を煽る表現で膨らませること。</p>
{chat_open}（50〜70字程度の紹介への反応）{chat_close}
<h2>見どころ</h2>
<ul>
  <li><strong>（魅力ポイント1）</strong>：（標準語で執筆）魅力を具体的に。</li>
  <li><strong>（魅力ポイント2）</strong>：（標準語で執筆）魅力を具体的に。</li>
  <li><strong>（魅力ポイント3）</strong>：（標準語で執筆）魅力を具体的に。</li>
</ul>
<h2>こんな人におすすめ</h2>
<ul style="list-style-type: none; padding-left: 0;">
  <li>✅ （標準語で執筆）おすすめの層1</li>
  <li>✅ （標準語で執筆）おすすめの層2</li>
  <li>✅ （標準語で執筆）おすすめの層3</li>
</ul>
{chat_open}（100〜120字程度の熱い総評・布教）{chat_close}
"""
    else:
        # スコア3：500文字規模のショート記事（嘘・捏造・想像を一切禁止の厳格構成）
        html_structure = f"""
{chat_open}（60〜80字程度。{intro_rule}）{chat_close}
<h2>（あらすじから抽出したキャッチーで目を引く見出し）</h2>
<p>（標準語で執筆）あらすじ・ツカミ。少ない情報をきれいに整理し、250〜500字程度で魅力的に書き直す。既にある設定の魅力を別の角度から掘り下げたり、読者の期待を煽る表現で膨らませること。
【重要禁止事項】あらすじに書かれていないキャラクターの心理・後半の展開・存在しない設定を一切書かないこと。事実だけで完結させること。）</p>
<h2>見どころ</h2>
<ul>
  <li>（あらすじに明記されている事実から1点目。推測・補完・創作は絶対禁止。）</li>
  <!--もう1点書ける事実があれば: <li>（2点目）</li>。書けない場合はこの行ごと削除すること。最大2点まで。絶対に3点書かないこと。-->
</ul>
<h2>こんな人におすすめ</h2>
<ul style="list-style-type: none; padding-left: 0;">
  （あらすじから自然に読み取れる対象者を✅マーク付きで1〜3点書くこと。「BL/TLが好きな方」のような汎用表現は禁止。書ける点数だけ書いてOK。足りない分はシステムが自動補完するので無理に水増ししないこと。）
</ul>
{chat_open}（50〜70字程度。「こういうシチュエーション最高ですよね！」など、明らかになっている設定に対する純粋な興奮・オススメ感を短く語る。想像の展開を語ることは禁止。）{chat_close}
"""



    return f"""あなたは人気ファンブログ「Novelove」の特別ライター「{reviewer["name"]}」です。
【キャラクター設定】
名前: {reviewer["name"]}
性格: {reviewer["personality"]}
文体・口調: {reviewer["tone"]}
今回の紹介の注目点（{medium_label}）: {focus}{mood_note}{guest_hint}{novel_rules}
【執筆ルール】
1. キャラクターコメント（吹き出し）と記事本文（HTMLタグ部分）を完全に書き分けること。
2. 記事本文（<h2>, <p>, <ul>, <li>タグの中身）は**「標準的で丁寧な日本語（ですます調）」**で、客観的な紹介文として執筆すること。担当ライターの口調や一人称を混ぜないこと。
3. 直接的な性的単語（性器の名称・行為の直接名称）は使用禁止。官能的な比喩を使うこと。
4. キャラクターコメント（吹き出し）の中身のみ、{reviewer["name"]}の個性を全開にした口調で執筆すること。
5. 吹き出しコメントではキャラ設定に合ったオタク用語や口癖を自然に使うこと。ただし記事本文（ですます調パート）には使用しないこと。
6. {voice_hint}
7. 記事本文（<p>タグ）では、あらすじ情報から「存在しない設定やキャラクター」を創作（ハルシネーション）して文字を水増しすることは絶対禁止。
8. h2見出しは毎回異なる切り口で書くこと。「○○に迫る」「○○が紡ぐ」のようなテンプレ表現は避けること。
{f'9. 見どころの3点は、この作品ならではの魅力を優先順に並べること。毎回「ストーリー→ビジュアル→キャラクター」の同じ順番にしないこと。' + chr(10) + '10. 「こんな人におすすめ」は、具体的な設定に基づくこと。「BL/TLが好きな方」のような汎用表現は禁止。' if ai_score >= 4 else '9. 見どころは必ずあらすじに書かれている事実のみから書くこと。推測・補完・創作は絶対禁止。最大2点まで。書ける事実が1点しかなければ1点で完結させること。絶対に3点書かないこと。' + chr(10) + '10. 「こんな人におすすめ」はHTML指定の<ul>タグ内に<li>✅ ...</li>形式で、あらすじから読み取れる対象者のみを書くこと。書けない点数は書かなくてOK。'}
【対象作品情報】
タイトル: {safe_title}
あらすじ: {safe_desc}
{f"公式属性タグ: {original_tags}" if original_tags else ""}
{f"販売形態: {str(target.get('site', '')).split(':')[0]}専売（他サービスでは購入できない限定作品）" if is_exclusive else ""}
アフィリエイトURL: {target["affiliate_url"]}
【出力形式（HTML）】
指示文・説明文は一切出力せず、以下の構成のみを出力してください。

{html_structure}

TAGS: （以下のリストから作品に合うものを最大3つ、カンマ区切りで出力。該当なしは「なし」と出力）
BL系: オメガバース/ヤンデレ/スパダリ/執着/年下攻め/幼なじみ/ケンカップル/主従/サラリーマン/年の差/転生/契約/再会/一途/運命
TL系: 溺愛/身分差/契約結婚/御曹司/騎士/オフィスラブ/腹黒/同居/嫉妬/強引/独占欲/初恋/記憶喪失/歳の差/ハッピーエンド

SEO_META:
seo_title=（32文字以内。**上記【対象作品情報】のあらすじに書かれた具体的な設定・属性・キーワード**を使うこと。存在しない設定・キャラ・展開は絶対に使わないこと。読者の感情を揺さぶる言葉で表現する。末尾に「| Novelove」は付けない。）
meta_desc=（80文字程度。**あらすじに実際に書かれた内容**に基づき、誰のどんな性癖に刺さるかを具体的に断言すること。あらすじにない要素を創作することは絶対禁止。読後にどんな感情が待っているかを約束する一文で締める。）
{("" if not original_tags else _tag_rule_str)}
{FACT_GUARD}{NG_PHRASES}
"""


def _call_deepseek_raw(messages, max_tokens=200, temperature=0.3):
    """
    DeepSeek APIへの共通リクエスト関数。
    戻り値: (text, error_type)
      error_type: "ok" / "rate_limit" / "api_error"
    """
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY が設定されていません")
        return "", "api_error"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    try:
        r = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
        if r.status_code == 429:
            logger.warning("DeepSeek レート制限 (429)")
            return "", "rate_limit"
        if r.status_code != 200:
            logger.warning(f"DeepSeek APIエラー: {r.status_code} {r.text[:200]}")
            return "", "api_error"
        data = r.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        return text, "ok"
    except requests.exceptions.Timeout:
        logger.warning("DeepSeek タイムアウト")
        return "", "api_error"
    except Exception as e:
        logger.warning(f"DeepSeek 例外: {e}")
        return "", "api_error"

def call_deepseek(prompt):
    system_prompt = (
        "あなたはBL・TL・女性向けコンテンツの紹介ブログライターです。"
        "指定されたキャラクターになりきり、HTML形式の記事を執筆してください。"
        "アダルト・官能的な内容も比喩的・文学的表現で執筆することができます。"
    )
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
    for attempt in range(3):
        logger.info(f"  [DeepSeek] 執筆依頼... (試行{attempt+1}/3)")
        t_start = time.time()
        text, error_type = _call_deepseek_raw(messages, max_tokens=2000, temperature=0.8)
        proc_time = round(time.time() - t_start, 1)
        if error_type == "rate_limit":
            logger.warning("  [DeepSeek] レート制限 → 30秒待機")
            time.sleep(30)
            continue
        if error_type != "ok" or not text:
            logger.warning(f"  [DeepSeek] 試行{attempt+1} 失敗 ({error_type})")
            time.sleep(5)
            continue
        stripped = text.strip()
        if stripped in ("0", "1", "2"):
            logger.warning(f"  [DeepSeek] AIスコア{stripped}点 → 投稿スキップ")
            return "", f"ai_score_{stripped}", DEEPSEEK_MODEL, proc_time
        cleaned = re.sub(r'^[3-5]\s*\n+', '', stripped)
        if cleaned != stripped:
            stripped = cleaned.strip()
        if len(stripped) > 50:
            logger.info(f"  [DeepSeek] 執筆完了（{len(stripped)}文字 / {proc_time}秒）")
            return stripped, "ok", DEEPSEEK_MODEL, proc_time
        logger.warning(f"  [DeepSeek] 試行{attempt+1}: 応答が短すぎる（{len(stripped)}文字）")
        time.sleep(5)
    return "", "content_block", DEEPSEEK_MODEL, 0.0

def make_excerpt(description, title, genre, reviewer_name="", ai_tags=None):
    """
    v10.5.0: SEO強化版メタディスクリプション。
    属性タグを自然に組み込み、レビュアー名とジャンルを明記する。
    """
    label = _genre_label(genre, title)
    tag_part = ""
    tags = [t for t in (ai_tags or []) if t]
    if tags:
        tag_part = f"{'・'.join(tags[:2])}などの要素が魅力的な"
    else:
        tag_part = f"注目の"
    if reviewer_name:
        outro = f"Noveloveの{reviewer_name}が、作品の見どころや気になる展開を詳しくお伝えします。"
    else:
        outro = f"Noveloveのライターが、作品の見どころや気になる展開を詳しくお伝えします。"
    text = f"『{title}』のあらすじと魅力を紹介！{tag_part}{label}の紹介記事です。{outro}"
    if len(text) > 160:
        text = text[:158] + "…"
    return text

def generate_article(target, override_reviewer_id=None, override_mood=None):
    if override_reviewer_id:
        from novelove_soul import REVIEWERS
        matched_reviewers = [r for r in REVIEWERS if r["id"] == override_reviewer_id]
        if matched_reviewers:
            reviewer = matched_reviewers[0]
            is_guest = target.get("genre") not in reviewer.get("genres", [])
        else:
            reviewer, is_guest = _get_reviewer_for_genre(target["genre"])
    else:
        reviewer, is_guest = _get_reviewer_for_genre(target["genre"])

    if override_mood:
        mood = override_mood
    else:
        mood = random.choice(MOOD_PATTERNS)

    is_novel = target["genre"] in ("novel_bl", "novel_tl")
    reviewer_name = reviewer["name"]
    # DBから取得したai_tagsがあれば先行パース
    db_ai_tags = []
    if target.get("ai_tags"):
        db_ai_tags = [t.strip() for t in target["ai_tags"].split(",") if t.strip()]

    final_error = "content_block"
    final_model = DEEPSEEK_MODEL
    final_proc_time = 0.0
    for mask_level in [0, 1, 2]:
        level_name = ["フィルターなし", "軽めフィルター", "ガチガチフィルター"][mask_level]
        logger.info(f"  [{level_name}] で執筆試行中...")
        prompt = build_prompt(target, reviewer, mask_level, is_novel=is_novel, is_guest=is_guest, mood=mood, ai_score=target.get("desc_score", 4), original_tags=target.get("original_tags", ""), is_exclusive=bool(target.get("is_exclusive", 0)))
        content, error_type, model_name, proc_time = call_deepseek(prompt)
        final_error = error_type
        final_model = model_name
        final_proc_time = proc_time
        if content:
            # 抽出処理: もうSCORE出力は撤廃したため、SCORE抽出ロジックごと消去
            # 記事本来の ai_score は target["desc_score"] を利用する仕組みに変更
            ai_score = target.get("desc_score", 4)


            # 2. AI生成SEOメタの抽出（SEO_META: セクション）
            ai_seo_title = ""
            ai_meta_desc = ""
            if "SEO_META:" in content:
                parts_seo = content.split("SEO_META:")
                content = parts_seo[0].strip()
                seo_lines = parts_seo[1].strip().splitlines()
                for sline in seo_lines:
                    sline = sline.strip()
                    if sline.startswith("seo_title="):
                        ai_seo_title = sline[len("seo_title="):].strip().strip('「」')
                    elif sline.startswith("meta_desc="):
                        ai_meta_desc = sline[len("meta_desc="):].strip()
                if ai_seo_title:
                    logger.info(f"  [SEO] AI生成タイトル取得: {ai_seo_title[:30]}...")
                if ai_meta_desc:
                    logger.info(f"  [SEO] AI生成抜粋取得: {ai_meta_desc[:30]}...")

            # 3. AI生成タグの抽出 (TAGS: セクション)
            ai_tags_from_ai = []
            if "TAGS:" in content:
                parts_tags = content.split("TAGS:")
                content = parts_tags[0].strip()
                tag_line = parts_tags[1].strip().split("\n")[0].strip()
                if tag_line and tag_line != "なし":
                    # スラッシュ区切りまたはカンマ区切りを想定
                    raw_tags = [t.strip() for t in tag_line.replace("/", ",").split(",") if t.strip()]
                    for t in raw_tags:
                        for allowed in AI_TAG_WHITELIST:
                            if allowed in t or t in allowed:
                                ai_tags_from_ai.append(allowed)
                                break
                    ai_tags_from_ai = list(dict.fromkeys(ai_tags_from_ai))[:3]

            # === スコア3：タグ不足チェック（タグ2以下は情報薄と判定してキャンセル） ===
            if ai_score <= 3 and len(ai_tags_from_ai) <= 2:
                logger.warning(f"  [スコア3 タグ不足] タグ{len(ai_tags_from_ai)}個（2以下）のため執筆キャンセル → thin_score3")
                return None, None, None, None, False, "thin_score3", model_name, level_name, proc_time, 0, "", [], ai_score

            # === スコア3：こんな人におすすめのハイブリッド補完 ===
            if ai_score <= 3 and ai_tags_from_ai:
                content = _inject_score3_osusume(content, ai_tags_from_ai)

            if not _check_image_ok(target["image_url"]):
                logger.warning(f"  [画像NG] 投稿直前チェックで無効: {target['image_url']}")
                return None, None, None, None, False, "image_missing", model_name, level_name, proc_time, 0, "", [], ai_score

            img_html = f'<p style="text-align:center;margin:20px 0;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow"><img src="{target["image_url"]}" alt="{target["title"]}" style="max-width:500px;width:100%;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,0.18);" /></a></p>\n'
            site_raw = target.get("site", "FANZA")
            site_display = site_raw.split(":")[0] if isinstance(site_raw, str) and ":" in site_raw else str(site_raw)
            format_name = _genre_label(target["genre"], target["title"])
            icon = "📖"
            if "ボイス" in format_name: icon = "🎧"
            elif "コミック" in format_name or "漫画" in format_name: icon = "🎨"
            # サイト名・フォーマット名の整形（らぶカル表記へのフォールバック）
            site_display = "らぶカル" if site_display == "Lovecal" else site_display
            badge_html = f'\n<p style="text-align:center; margin-bottom:20px;">\n<span style="background:#fefefe; border:1px solid #ddd; padding:6px 16px; border-radius:25px; font-weight:bold; color:#444; box-shadow:0 2px 4px rgba(0,0,0,0.05); display:inline-block;">{icon} {site_display} {format_name}</span>\n</p>'
            text_link = f'<p style="text-align:center; font-weight:bold; font-size:1.1em; margin-top:5px; margin-bottom:15px;"><a href="{target["affiliate_url"]}" target="_blank" rel="nofollow" style="text-decoration:none; color:#d81b60;">▶ 『{target["title"]}』の試し読み・お得なセール状況をチェック！</a></p>\n'
            button_html = get_affiliate_button_html(target["affiliate_url"], "無料で試し読みする")
            if "FANZA" in site_display or "らぶカル" in site_display:
                credit_html = (
                    f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;">\n'
                    f'<a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/r18_135_17.gif" width="135" height="17" alt="WEB SERVICE BY FANZA" style="border:none;"></a>\n'
                    f'</div>\n'
                )
            elif "DMM" in site_display:
                credit_html = (
                    f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;">\n'
                    f'<a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/com_135_17.gif" width="135" height="17" alt="WEB SERVICE BY DMM.com" style="border:none;"></a>\n'
                    f'</div>\n'
                )
            else:
                credit_html = f'<p style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee; font-size:0.8em; color:#bbb;">\nPRESENTED BY {site_display} / Novelove Affiliate Program\n</p>\n'
            release_display = ""
            if target.get("release_date"):
                try:
                    rd = target["release_date"][:10].replace("-", "/")
                    release_display = f'<p style="text-align:center; color:#666; font-size:0.9em; margin-bottom:10px;">発売日：{rd}</p>\n'
                except Exception as e:
                    logger.warning(f"  [発売日整形失敗] {e}")
            
            # v12.0.0: AI生成SEOタイトル・抜粋の優先利用
            # まずベースとなるAI生成タグリストを確定
            if not ai_tags_from_ai:
                ai_tags_from_ai = list(db_ai_tags)

            # 専売タグをWPタグリストに追加
            _site_raw = str(target.get("site", "")).split(":")[0]
            _is_excl = bool(target.get("is_exclusive", 0))
            if _is_excl:
                if "DLsite" in _site_raw and "DLsite専売" not in ai_tags_from_ai:
                    ai_tags_from_ai.append("DLsite専売")
                elif "FANZA" in _site_raw and "FANZA独占" not in ai_tags_from_ai:
                    ai_tags_from_ai.append("FANZA独占")
                elif "DigiKet" in _site_raw and "DigiKet限定" not in ai_tags_from_ai:
                    ai_tags_from_ai.append("DigiKet限定")
                elif "Lovecal" in _site_raw and "らぶカル独占" not in ai_tags_from_ai:
                    ai_tags_from_ai.append("らぶカル独占")
            
            tags_for_seo = ai_tags_from_ai
            tag_str = "・".join(tags_for_seo[:2]) if tags_for_seo else ""

            # SEOタイトル: AIが生成したものを優先。なければテンプレートフォールバック
            if ai_seo_title:
                # 32文字超過サニタイズ
                seo_title_body = ai_seo_title[:32]
                seo_title = f"『{target['title']}』{seo_title_body}"
            elif tag_str:
                seo_title = f"『{target['title']}』あらすじ紹介！{tag_str}の{format_name}を紹介 | Novelove"
            else:
                seo_title = f"『{target['title']}』あらすじ紹介！注目の{format_name}を詳しく紹介 | Novelove"
            if len(seo_title) > 70:
                seo_title = seo_title[:68] + "… | Novelove"

            # 抜粋: AIが生成したものを優先。なければテンプレートフォールバック
            if ai_meta_desc:
                # 160文字超過サニタイズ
                excerpt = ai_meta_desc[:160]
            else:
                excerpt = make_excerpt(target["description"], target["title"], target["genre"], reviewer_name=reviewer["name"], ai_tags=tags_for_seo)
            wp_title = target["title"]
            # あわせて読みたい（文字リンク）は廃止。YARPPプラグインによる関連記事表示に移行。
            full_content = (
                badge_html + img_html + release_display + text_link +
                content + button_html + credit_html
            )
            word_count = len(content)
            is_r18_val = ":r18=1" in str(target.get("site", ""))
            # 戻り値: (wp_title, full_content, excerpt, seo_title, is_r18, status, model, level, time, words, reviewer, tags, score)
            # ※将来拡張時は NamedTuple 化を検討すること（13要素タプルは保守性リスク）
            return wp_title, full_content, excerpt, seo_title, is_r18_val, "ok", model_name, level_name, proc_time, word_count, reviewer_name, ai_tags_from_ai, ai_score
        if error_type == "rate_limit":
            logger.warning("  レート制限 → フィルター試行を中断")
            break
        logger.warning(f"  [{level_name}] 失敗 → 次のフィルターレベルへ")
    return None, None, None, None, False, final_error, final_model, "None", final_proc_time, 0, "", [], 0


def _inject_score3_osusume(content: str, tags: list) -> str:
    """
    スコア3記事の「こんな人におすすめ」をAI出力 + タグ補完のハイブリッドで完成させる。
    AIが書いたli要素を数え、不足分だけタグから補完して最大3点にする。
    """
    # AIが「こんな人におすすめ」セクションに書いたli要素を数える
    osusume_match = re.search(
        r'<h2>こんな人におすすめ</h2>\s*<ul[^>]*>(.+?)</ul>',
        content, re.DOTALL
    )
    existing_items = []
    if osusume_match:
        ul_inner = osusume_match.group(1)
        existing_items = re.findall(r'<li>.*?</li>', ul_inner, re.DOTALL)

    ai_count = len(existing_items)
    needed = max(0, 3 - ai_count)

    if needed == 0:
        # AIが3点書けていれば補完不要
        return content

    # 補完するタグを選ぶ（AIが既に書いたテキストに含まれているものは除外）
    existing_text = osusume_match.group(0) if osusume_match else ""
    supplement_items = []
    for tag in tags:
        if tag in existing_text:
            continue  # 既にAIが言及済み
        supplement_items.append(
            f'  <li>✅ <strong>{tag}</strong>系の作品が好きな方</li>'
        )
        if len(supplement_items) >= needed:
            break

    if not supplement_items:
        return content

    if osusume_match:
        # 既存のulに補完liを追記
        new_ul = osusume_match.group(0).rstrip()
        if new_ul.endswith('</ul>'):
            new_ul = new_ul[:-5] + '\n' + '\n'.join(supplement_items) + '\n</ul>'
        content = content[:osusume_match.start()] + new_ul + content[osusume_match.end():]
    else:
        # おすすめセクション自体がなければ末尾に追加
        items_html = '\n'.join(supplement_items)
        osusume_html = (
            f'\n<h2>こんな人におすすめ</h2>\n'
            f'<ul style="list-style-type: none; padding-left: 0;">\n'
            f'{items_html}\n</ul>\n'
        )
        # 末尾の吹き出し直前に挿入（speech-bubble-leftの最後の出現位置）
        sb_positions = [m.start() for m in re.finditer(r'<div class="speech-bubble-left">', content)]
        if sb_positions:
            insert_pos = sb_positions[-1]  # 最後の吹き出し（総評）の前に挿入
            content = content[:insert_pos] + osusume_html + content[insert_pos:]
        else:
            content += osusume_html

    logger.info(f"  [スコア3 補完] AI:{ai_count}点 + タグ補完:{len(supplement_items)}点 = 合計{ai_count + len(supplement_items)}点")
    return content

# === A+C方式: サムネURL生成ヘルパー ===
def _get_thumbnail_url(image_url: str) -> str:
    """
    大きい画像URLから、FIFUに設定する軽量サムネURLを生成する。
    確実に存在が確認済みのサイズのみ変換する（404リスク回避）。
    変換できないものはそのまま返す（例: FANZA doujin-assets）。
    """
    if not image_url:
        return image_url
    # DLsite: modpub/_img_main.jpg -> resize/_img_main_300x300.webp (18KB, 確認済み)
    if "img.dlsite.jp/modpub/" in image_url and "_img_main.jpg" in image_url:
        return image_url.replace("/modpub/", "/resize/").replace("_img_main.jpg", "_img_main_300x300.webp")
    # DMM ebook-assets: pl.jpg -> ps.jpg (16KB, 確認済み。doujin等はNOW PRINTINGになるため除外)
    if "ebook-assets.dmm" in image_url and image_url.endswith("pl.jpg"):
        return image_url[:-6] + "ps.jpg"
    # DigiKet: _1.jpg / _2.jpg -> _a_200x150.jpg (10KB, 確認済み)
    if "digiket.net" in image_url:
        return re.sub(r'_\d+\.jpg$', '_a_200x150.jpg', image_url)
    # FANZA doujin-assets 等: 変換しない（NOW PRINTINGリダイレクト対策）
    return image_url

# === WordPress投稿 ===
def get_or_create_term(name, taxonomy):
    auth = (WP_USER, WP_APP_PASSWORD)
    try:
        r = requests.get(f"{WP_SITE_URL}/wp-json/wp/v2/{taxonomy}", auth=auth, params={"search": name}, timeout=15)
        hits = r.json()
        for hit in hits:
            if hit.get("name") == name: return hit["id"]
        r2 = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/{taxonomy}", auth=auth, json={"name": name}, timeout=15)
        return r2.json().get("id")
    except Exception:
        return None

def post_to_wordpress(title, content, genre, image_url, excerpt="", seo_title="", slug="", is_r18=False, site_label=None, ai_tags=None, reviewer=None, thumb_url=None):
    """
    WordPress REST API で投稿。FIFUプラグイン経由で外部リンクをアイキャッチに設定。
    image_url: 記事本文に埋め込む大きい画像URL
    thumb_url: FIFUアイキャッチに設定する軽量サムネURL（省略時はimage_urlをそのまま使用）
    """
    auth = (WP_USER, WP_APP_PASSWORD)
    # FIFUには軽量サムネを使用（A+C方式）
    fifu_url = thumb_url if thumb_url else image_url
    # FIFUプラグイン用メタとCocoon SEOメタ
    meta = {
        "fifu_image_url": fifu_url,
        "fifu_image_alt": title,
    }
    if seo_title: meta["the_page_seo_title"] = seo_title
    if excerpt: meta["the_page_meta_description"] = excerpt

    # === v10.6.0 新カテゴリ・タグ分類ロジック ===
    
    # 形態とジャンルに基づくカテゴリ(大分類)の決定
    # v11.0.2: ジャンル文字列による厳格判定。タイトルキーワードはフォールバックのみ。
    g_lower = str(genre).lower()
    if "novel" in g_lower:
        is_novel = True
    elif any(x in g_lower for x in ("comic", "manga", "doujin")):
        is_novel = False
    else:
        # v11.1.3: キーワード判定を廃止。取得時に公式種別でDBジャンルが確定していることを前提とする。
        is_novel = False

    is_ranking = "ranking" in str(slug).lower() or "ランキング" in title
    
    if is_ranking:
        cat_name = "ランキング"
    else:
        # 小説か漫画かでカテゴリを分ける
        is_bl = "bl" in genre.lower() or "BL" in genre
        if is_novel:
            cat_name = "BL小説" if is_bl else "TL小説"
        else:
            cat_name = "BL漫画" if is_bl else "TL漫画"
            
    cat_id = get_or_create_term(cat_name, "categories")
    categories = [cat_id] if cat_id else [25] # 25は「未分類」の安全なフォールバック

    # タグ(小分類・属性)の構成
    # GENRE_TAGS は廃止されたため空リスト、AIタグとサイト情報のみを利用
    tag_names = []
    site_name = None

    if site_label:
        normalized_labels = {"DMM.com": "DMM", "FANZA": "FANZA", "DLsite": "DLsite", "DigiKet": "DigiKet", "Lovecal": "らぶカル"}
        site_name = normalized_labels.get(site_label, site_label)
        if site_name and site_name not in tag_names: tag_names.append(site_name)

    if ai_tags:
        for t in ai_tags:
            if t and t not in tag_names: tag_names.append(t)

    # 担当者タグの付与
    if reviewer and reviewer not in tag_names:
        tag_names.append(reviewer)

    # ランキング記事の特例処理（サイト名と担当者のみを残す）
    if is_ranking:
        allowed_ranking_tags = []
        if site_name and site_name in tag_names: allowed_ranking_tags.append(site_name)
        if reviewer and reviewer in tag_names: allowed_ranking_tags.append(reviewer)
        tag_names = allowed_ranking_tags


    
    # 完全に廃止された単体タグ・不要な複合タグの徹底排除 (v10.6.0)
    exclude_list = ("BL", "TL", "コミック", "小説", "漫画", "BLコミック", "TLコミック", "BL同人", "TL同人", "商業BL", "同人BL", "商業TL", "同人TL", "商業BL小説", "商業TL小説")
    tag_names = [t for t in tag_names if t not in exclude_list]

    # WordPress側にカテゴリやタグを問い合わせてID化
    tag_ids = [t for t in [get_or_create_term(name, "tags") for name in tag_names] if t]

    post_data = {
        "title": title, "content": content, "excerpt": excerpt,
        "status": "publish", "slug": slug,
        "categories": categories, "tags": tag_ids, "meta": meta,
    }
    try:
        r = requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts", auth=auth, json=post_data, timeout=40)
    except Exception as e:
        logger.error(f"WordPress投稿接続エラー: {e}")
        return None

    if r.status_code in (200, 201):
        data = r.json()
        wp_post_id = data.get("id")
        link = data.get("link")
        if wp_post_id:
            # WP-CLIを使用してメタデータを確実に更新する (v11.1.2)
            # 環境依存パスは novelove_core.py で一元管理。移転時は .env を更新するだけでOK。
            php_path = WP_PHP_PATH
            wp_path  = WP_CLI_PATH
            doc_root = f"--path={WP_DOC_ROOT}"
            
            # 1. アイキャッチ画像の設定 (v13.2.3: A+C方式 — 軽量サムネURLを使用)
            if fifu_url:
                try:
                    subprocess.run([php_path, wp_path, "eval", f'fifu_dev_set_image({wp_post_id}, "{fifu_url}");', doc_root, "--allow-root"], capture_output=True, text=True, timeout=60, check=True)
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                    std_err_msg = e.stderr if hasattr(e, "stderr") else str(e)
                    logger.error(f"  [WP-CLI] 画像設定失敗 (タイムアウトまたはエラー): {std_err_msg}")
                    # 中途半端な記事を残さないためのロールバック (v11.4.14 強化)
                    try:
                        res_del = requests.delete(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}", auth=auth, params={"force": "true"}, timeout=15)
                        if res_del.status_code not in (200, 201):
                            logger.warning(f"  [ROLLBACK] 投稿削除リクエストが失敗しました。ステータスを下書きに変更します。 (status={res_del.status_code})")
                            requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}", auth=auth, json={"status": "draft"}, timeout=15)
                        else:
                            logger.warning(f"  [ROLLBACK] 画像設定失敗のため投稿を削除しました: ID={wp_post_id}")
                    except Exception as rollback_err:
                        logger.error(f"  [ROLLBACK] 致命的失敗: {rollback_err}")
                        # 最終手段として下書き変更を試行
                        try: requests.post(f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}", auth=auth, json={"status": "draft"}, timeout=10)
                        except: pass
                    return None # 呼び出し元で wp_post_failed として処理される
            
            # 2. SEOタイトルの設定
            if seo_title:
                try:
                    subprocess.run([php_path, wp_path, "post", "meta", "update", str(wp_post_id), "the_page_seo_title", seo_title, doc_root, "--allow-root"], capture_output=True, timeout=30)
                except Exception as e:
                    logger.warning(f"  [WP-CLI] SEOタイトル設定失敗: {e}")
                
            # 3. メタディスクリプション（抜粋）の設定
            if excerpt:
                try:
                    subprocess.run([php_path, wp_path, "post", "meta", "update", str(wp_post_id), "the_page_meta_description", excerpt, doc_root, "--allow-root"], capture_output=True, timeout=30)
                except Exception as e:
                    logger.warning(f"  [WP-CLI] メタディスクリプション設定失敗: {e}")
                
        return link
    
    logger.error(f"WordPress投稿失敗: status={r.status_code}, body={r.text[:500]}")
    return None

# === メインロジック ===
# --- [削除] 旧 main() 定義 (v11.4.14 にて統合・削除) ---
def _check_global_cooldown(cooldown_minutes=55, post_type='regular'):
    """
    全DB横断で最新の投稿時刻をチェックし、指定分数が経過しているか返す。
    経過していれば True、クールダウン中なら False を返す。
    """
    latest_pub = None
    for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
        if not os.path.exists(db_p): continue
        tmp_conn = db_connect(db_p)
        # post_type でフィルタリング (v11.4.13 修正)
        row = tmp_conn.execute(
            "SELECT published_at FROM novelove_posts WHERE status='published' AND post_type=? ORDER BY published_at DESC LIMIT 1",
            (post_type,)
        ).fetchone()
        tmp_conn.close()
        if row and row[0]:
            try:
                # v11.4.11: 常にJST（localtime）としてパース
                dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                if latest_pub is None or dt > latest_pub:
                    latest_pub = dt
            except Exception as e:
                logger.warning(f"  [クールダウン] published_atのパース失敗: {row[0]} / {e}")
    
    jst = timezone(timedelta(hours=9))
    now_jst = datetime.now(jst).replace(tzinfo=None)
    if latest_pub:
        # v11.4.14: 常にJST（UTC+9）で比較
        diff = (now_jst - latest_pub).total_seconds() / 60
        if diff < cooldown_minutes:
            return False, diff
    return True, 0

def _run_main_logic():
    """
    v11.4.12: メイン処理。
    1. クールダウンチェック（55分）<- 重い処理の前に移動
    2. 新着取得
    3. 在庫クリーンアップ
    4. 投稿実行
    """
    # ★ 緊急停止チェック
    if is_emergency_stop():
        logger.info("🚨 緊急停止中のためスキップ。解除: rm emergency_stop.lock")
        return

    # クールダウンチェック (通常投稿: 55分)
    # v11.4.12: 何よりも先に判定を行い、負荷をゼロにする
    is_ready, elapsed = _check_global_cooldown(55)
    if not is_ready:
        logger.info(f"🕒 クールダウン中（{elapsed:.1f}分経過）。0.1秒で終了します。")
        return

    # 処理開始時刻（5分タイムアウト用）
    start_time = time.time()

    fetch_and_stock_all()
    try:
        fetch_digiket_items()
    except Exception as e:
        logger.error(f"DigiKet取得エラー: {e}")

    # --- 在庫クリーンアップ ---
    for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
        if not os.path.exists(db_p): continue
        conn = db_connect(db_p)
        c = conn.cursor()
        # ① 7日以上経過したpendingをexcludedへ (JST)
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE novelove_posts SET status='excluded', last_error='expired' WHERE status='pending' AND inserted_at < ?", (seven_days_ago,))
        
        # ② ジャンルごとにスコア上位かつ最新の20件を残して、他をexcludedへ
        # FETCH_TARGETS を使わず、DBに実際に存在するジャンルを直接取得することで重複クエリを防ぐ
        genres_in_db = [row[0] for row in c.execute(
            "SELECT DISTINCT genre FROM novelove_posts WHERE status='pending'"
        ).fetchall()]
        for genre in genres_in_db:
            rows = c.execute(
                "SELECT product_id FROM novelove_posts WHERE status='pending' AND genre=? ORDER BY desc_score DESC, inserted_at DESC",
                (genre,)
            ).fetchall()
            if len(rows) > 20:
                to_exclude = [r[0] for r in rows[20:]]
                placeholders = ",".join(["?"] * len(to_exclude))
                c.execute(
                    f"UPDATE novelove_posts SET status='excluded', last_error='inventory_full' WHERE product_id IN ({placeholders})",
                    to_exclude
                )
        conn.commit()
        conn.close()
    # pendingから1件投稿（ジャンルラウンドロビン）
    g_idx_base = get_genre_index()
    posted = False
    tried_details = []
    error_count = 0  # ★ 連続失敗カウンター

    for i in range(len(FETCH_TARGETS)):
        # ★ 5分タイムアウトチェック
        if time.time() - start_time > 300:
            trigger_emergency_stop("処理が5分を超過しました（タイムアウト）")
            break

        target_info = FETCH_TARGETS[(g_idx_base + i) % len(FETCH_TARGETS)]
        db_path = get_db_path(target_info.get("site", "FANZA"))
        genre = target_info["genre"]
        conn = db_connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # v11.4.7: SELECT * を廃止し、カラム名を明示的に指定 (v13.2.3: original_tags, is_exclusive 追加)
        row = c.execute(
            "SELECT product_id, title, author, genre, site, status, description, affiliate_url, image_url, product_url, release_date, post_type, desc_score, ai_tags, reviewer, original_tags, is_exclusive FROM novelove_posts WHERE status='pending' AND genre=? ORDER BY inserted_at DESC LIMIT 1",
            (genre,)
        ).fetchone()
        if row:
            try:
                # ★ 全体をtry-exceptで囲む（想定外の例外も捕捉）
                success, reason = _execute_posting_flow(row, c, conn)
            except Exception as e:
                logger.error(f"  [想定外エラー] {e}")
                # pendingのまま放置されないようにexcludedに変更
                try:
                    c.execute("UPDATE novelove_posts SET status='excluded', last_error='unexpected_error' WHERE product_id=?", (row['product_id'],))
                    conn.commit()
                except Exception:
                    pass
                success = False
                reason = "unexpected_error"

            label = ERROR_LABELS.get(reason, reason) if reason else "成功"
            tried_details.append(f"・{row['title'][:30]}... ({target_info['label']}) ➔ {label}")
            if success:
                posted = True
                error_count = 0  # 成功したらリセット
            else:
                # 正常な選別処理（品質フィルタ）の結果はサーキットブレーカー対象外
                NORMAL_FILTER_REASONS = ("low_score", "duplicate_fuzzy", "excluded_foreign", "image_missing", "no_desc_or_image", "thin_score3", "excluded_by_pre_filter")
                is_normal_filter = any(reason and reason.startswith(r) for r in NORMAL_FILTER_REASONS)
                if is_normal_filter:
                    logger.info(f"  [フィルタ除外] {reason} — サーキットブレーカー対象外")
                else:
                    error_count += 1
                # ★ 3回連続「異常系」失敗でサーキットブレーカー発動
                if error_count >= 3:
                    trigger_emergency_stop(f"投稿が3回連続失敗しました（最後の理由: {reason}）")
                    break
        else:
            logger.info(f"  -> {target_info['label']} にpendingなし。次へ...")
        conn.close()
        if posted:
            save_genre_index(g_idx_base + i + 1)
            logger.info(f"✅ {target_info['label']} にて投稿成功。")
            break
    if not posted:
        # 在庫統計レポート
        inventory_list = []
        if os.path.exists(DB_FILE_FANZA):
            _c = db_connect(DB_FILE_FANZA)
            c_fanza = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site NOT LIKE '%ebook%' AND site NOT LIKE '%digital_doujin_bl%' AND site NOT LIKE '%digital_doujin_tl%'").fetchone()[0]
            c_lovecal = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND (site LIKE '%digital_doujin_bl%' OR site LIKE '%digital_doujin_tl%')").fetchone()[0]
            c_dmm = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site LIKE '%ebook%'").fetchone()[0]
            inventory_list.extend([f"FANZA {c_fanza}", f"DMM {c_dmm}", f"らぶカル {c_lovecal}"])
            _c.close()
        if os.path.exists(DB_FILE_DLSITE):
            _c = db_connect(DB_FILE_DLSITE)
            c_dl = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
            inventory_list.append(f"DLsite {c_dl}")
            _c.close()
        if os.path.exists(DB_FILE_DIGIKET):
            _c = db_connect(DB_FILE_DIGIKET)
            c_dk = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
            inventory_list.append(f"DigiKet {c_dk}")
            _c.close()
        inventory_str = " / ".join(inventory_list) + " 件"

        attempts_str = "\n".join(tried_details) if tried_details else "（なし：全在庫切れ）"
        
        # 24hエラー統計
        err_stats = {}
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        summary_stats = {"total": 0, "accepted": 0, "excluded": 0}
        for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
            if not os.path.exists(db_p): continue
            _c = db_connect(db_p)
            rows = _c.execute("SELECT last_error, count(*) FROM novelove_posts WHERE status='excluded' AND inserted_at > ? GROUP BY last_error", (yesterday,)).fetchall()
            for r in rows: err_stats[r[0]] = err_stats.get(r[0], 0) + r[1]
            summary_stats["total"]    += _c.execute("SELECT count(*) FROM novelove_posts WHERE inserted_at > ?", (yesterday,)).fetchone()[0]
            summary_stats["accepted"] += _c.execute("SELECT count(*) FROM novelove_posts WHERE status IN ('pending','published') AND inserted_at > ?", (yesterday,)).fetchone()[0]
            summary_stats["excluded"] += _c.execute("SELECT count(*) FROM novelove_posts WHERE status='excluded' AND inserted_at > ?", (yesterday,)).fetchone()[0]
            _c.close()

        display_errs = []
        for k, v in err_stats.items():
            kb = ERROR_LABELS.get(k, k)
            display_errs.append(f"  ・{kb}: {v}件")
        err_msg = "\n".join(display_errs) if display_errs else "なし"

        notify_discord(
            f"⚠️ **投稿対象なし**\n今回の実行では投稿が行われませんでした。\n\n"
            f"**【今回の試行】**\n{attempts_str}\n\n"
            f"**【現在の投稿待ち在庫】**\n{inventory_str}\n\n"
            f"**【本日の新着統計 (24h)】**\n"
            f"・全件数: {summary_stats['total']}件\n"
            f"  ┣ 採用: {summary_stats['accepted']}件\n"
            f"  ┗ 除外: {summary_stats['excluded']}件\n{err_msg}",
            username="ノベラブ通知くん"
        )
    logger.info("=" * 60)

# === [v12.2.0] クロスDB重複排除（Fuzzy Matching）===
def normalize_title(title):
    """タイトルから装飾（括弧とその中身）とスペースを除去し、スッピン文字列を返す。"""
    t = re.sub(r'[\[\(（【〈《「『].*?[\]\)）】〉》」』]', '', str(title))
    t = re.sub(r'[\s　]+', '', t)
    return t.strip()

def is_cross_db_duplicate(new_title, current_pid, threshold=0.90):
    """全DBを横断し、スッピンタイトルの類似度が閾値以上の published 記事があるか判定する。"""
    norm_new = normalize_title(new_title)
    if not norm_new:
        return False, "", 0.0
    for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
        if not os.path.exists(db_p):
            continue
        try:
            c2 = db_connect(db_p)
            c2.row_factory = sqlite3.Row
            rows = c2.execute(
                "SELECT product_id, title FROM novelove_posts WHERE status='published' AND product_id!=?",
                (current_pid,)
            ).fetchall()
            c2.close()
            for r in rows:
                norm_existing = normalize_title(r['title'])
                if not norm_existing:
                    continue
                ratio = difflib.SequenceMatcher(None, norm_new, norm_existing).ratio()
                if ratio >= threshold:
                    return True, r['title'], ratio
        except Exception as e:
            logger.warning(f"  [重複チェック] DB読み込みエラー ({db_p}): {e}")
    return False, "", 0.0

def _execute_posting_flow(row, cursor, conn):
    """v11.4.0: 執筆・タグ抽出・投稿・通知フロー。"""
    pid = row["product_id"]
    title = row["title"]
    site_raw = row["site"]
    # 🌟 NEW: らぶカルの場合はサイト自体を「Lovecal」として完全に分離独立させる
    # URLに lovecul.dmm.co.jp が含まれる作品は本来FANZAとして保存されているが、ここでLovecalに強制置換
    if "product_url" in row.keys() and "lovecul.dmm.co.jp" in str(row["product_url"]):
        site_raw = str(site_raw).replace("FANZA", "Lovecal")
        
    site_label = site_raw.split(":")[0] if isinstance(site_raw, str) and ":" in site_raw else str(site_raw)
    
    logger.info(f"--- [投稿実行] {site_label} | {title[:40]} ---")
    
    # --- [v11.4.14] AI審査前のコスト最適化（事前キーワードフィルタ） ---
    title_str = str(row['title'])
    desc_str = str(row['description']) if 'description' in row.keys() else ""
    ng_patterns = ["動画", "ボイス", "シチュエーションCD", "ASMR", "English", "Chinese", "サンプル", "【ボイス】", "【動画】"]
    if any(p in title_str for p in ng_patterns) or any(p in desc_str for p in ng_patterns):
        logger.info(f"  [Pre-Filter] 不採用キーワード、または不適合形式を検知したため除外します: {title_str[:30]}...")
        cursor.execute("UPDATE novelove_posts SET status='excluded', last_error='excluded_by_pre_filter' WHERE product_id=?", (row['product_id'],))
        conn.commit()
        return False, "excluded_by_pre_filter"

    # 🌟 NEW: AI事前評価スキップロジック
    logger.info(f"  [{row['genre']}] 事前品質審査開始: {title[:30]}...")
    _orig_tags_for_eval = row["original_tags"] if "original_tags" in row.keys() else ""
    eval_score = _evaluate_article_potential(title, desc_str, original_tags=_orig_tags_for_eval)
    logger.info(f"  -> AI品質スコア: {eval_score}/5点")
    
    # スコア2以下は破棄（中身がスッカスカ、ノイズのみ）
    if eval_score <= 2:
        logger.warning(f"  -> 内容が不十分（スコア{eval_score}点）のため執筆スキップ")
        cursor.execute("UPDATE novelove_posts SET status='excluded', last_error='low_score' WHERE product_id=?", (pid,))
        conn.commit()
        return False, f"low_score: {eval_score}"
        
    logger.info(f"  ✅ スコア基準クリア ({eval_score}点)。執筆を開始します。")

    # DigiKet高解像度化を一元処理
    img_url = row["image_url"] or ""
    if img_url and "img.digiket.net" in img_url and "_2.jpg" in img_url:
        img_url = img_url.replace("_2.jpg", "_1.jpg")
    # A+C方式: FIFUには軽量サムネ、記事本文には大きいURLを使う
    thumb_url = _get_thumbnail_url(img_url)

    target = {
        "product_id":    pid,
        "title":         row["title"],
        "author":        row["author"] or "",
        "genre":         row["genre"],
        "site":          site_raw,
        "description":   desc_str,
        "affiliate_url": row["affiliate_url"],
        "image_url":     img_url,
        "thumb_url":     thumb_url,
        "release_date":  row["release_date"],
        "ai_tags":       row["ai_tags"],
        "desc_score":    eval_score,  # スコアを渡す
        "original_tags": row["original_tags"] if "original_tags" in row.keys() else "",
        "is_exclusive":  row["is_exclusive"] if "is_exclusive" in row.keys() else 0,
    }

    # v12.2.0: 全DB横断・Fuzzy Matching重複チェック (旧24hガードレールを完全置換)
    is_dup, dup_title, dup_ratio = is_cross_db_duplicate(title, pid)
    if is_dup:
        logger.warning(f"  [重複ブロック] スッピンタイトル '{normalize_title(title)}' は '{normalize_title(dup_title)}' と類似度 {dup_ratio:.0%} のためスキップ (元: {dup_title[:40]})")
        cursor.execute("UPDATE novelove_posts SET status='excluded', last_error='duplicate_fuzzy' WHERE product_id=?", (pid,))
        conn.commit()
        return False, "duplicate_fuzzy"

    # 記事生成 (v11.4.0: 12要素対応)
    res = generate_article(target)
    if not res or not res[0] or not res[1]:
        err = "ai_failed"
        if res and len(res) >= 6 and res[5]: err = res[5]
        cursor.execute("UPDATE novelove_posts SET status='excluded', last_error=? WHERE product_id=?", (err, pid))
        conn.commit()
        return False, err

    wp_title, content, excerpt, seo_title, is_r18, status, model, level, ptime, words, rev_name, ai_tags_from_ai, ai_score = res

    # AI執筆完了時に、取得できたあらすじの文字数をログに出力（スクレイピング品質の検証証明）
    desc_c_len = len(str(target.get("description", "")))
    logger.info(f"  [完了] AI執筆完了！(抽出あらすじ文字数: {desc_c_len}文字)")

    # AIスコア安全弁（通常は事前審査済みなのでここには来ないが、万一のフェイルセーフ）
    if ai_score == 0:
        ai_score = eval_score  # 事前審査でのスコアを使用

    # タグ: generate_article内で既にDB既存タグへのフォールバック＋専売タグ付与済み
    final_ai_tags = ai_tags_from_ai

    # v13.5.1: 専売タグの付与（DBの is_exclusive フラグに基づく厳密なDOM判定結果）
    is_exclusive = (row["is_exclusive"] if "is_exclusive" in row.keys() else 0) == 1
    if is_exclusive:
        _normalized = {"DMM.com": "DMM", "FANZA": "FANZA", "DLsite": "DLsite", "DigiKet": "DigiKet"}
        _sn = _normalized.get(site_label, site_label)
        excl_tag = {"DLsite": "DLsite専売", "FANZA": "FANZA独占", "DMM": "FANZA独占", "DigiKet": "DigiKet限定"}.get(_sn, "")
        if not excl_tag and "らぶカル" in str(site_label):
            excl_tag = "らぶカル独占"
        if excl_tag and excl_tag not in final_ai_tags:
            final_ai_tags.append(excl_tag)
    link = post_to_wordpress(
        wp_title, content, row["genre"], img_url,
        excerpt=excerpt, seo_title=seo_title, slug=pid, is_r18=is_r18,
        site_label=site_label, ai_tags=final_ai_tags, reviewer=rev_name,
        thumb_url=thumb_url
    )
    
    if link:
        ai_tags_str = ",".join(final_ai_tags)
        # v12.8.0: wp_tags（WPへ実際に送信した完成品タグ一覧）を構築してDBへ書き戻す
        # ※ post_to_wordpress() 内のタグ構築ロジック(L746-778)と完全一致させること
        _normalized_labels = {"DMM.com": "DMM", "FANZA": "FANZA", "DLsite": "DLsite", "DigiKet": "DigiKet"}
        _site_name_for_wp = _normalized_labels.get(site_label, site_label)
        _wp_tags_parts = []
        if _site_name_for_wp:
            _wp_tags_parts.append(_site_name_for_wp)
        for _t in final_ai_tags:
            if _t and _t not in _wp_tags_parts:
                _wp_tags_parts.append(_t)
        if rev_name and rev_name not in _wp_tags_parts:
            _wp_tags_parts.append(rev_name)
        # ランキング記事特例（post_to_wordpress L763-767 と同一）
        _is_ranking = "ranking" in str(pid).lower() or "ランキング" in str(row["title"])
        if _is_ranking:
            _allowed = []
            if _site_name_for_wp and _site_name_for_wp in _wp_tags_parts:
                _allowed.append(_site_name_for_wp)
            if rev_name and rev_name in _wp_tags_parts:
                _allowed.append(rev_name)
            _wp_tags_parts = _allowed
        # exclude_list フィルタ（post_to_wordpress L777-778 と同一）
        _exclude = ("BL", "TL", "コミック", "小説", "漫画", "BLコミック", "TLコミック", "BL同人", "TL同人", "商業BL", "同人BL", "商業TL", "同人TL", "商業BL小説", "商業TL小説")
        _wp_tags_parts = [_t for _t in _wp_tags_parts if _t not in _exclude]
        wp_tags_str = ",".join(_wp_tags_parts)
        # v11.4.0: ai_tags も最新版で上書き保存, 過去のエラー履歴（last_error）もクリア, desc_scoreも保存
        # v12.8.0: wp_tags も同時保存
        cursor.execute(
            "UPDATE novelove_posts SET status='published', wp_post_url=?, published_at=datetime('now', 'localtime'), reviewer=?, ai_tags=?, wp_tags=?, last_error=NULL, desc_score=? WHERE product_id=?",
            (link, rev_name, ai_tags_str, wp_tags_str, ai_score, pid)
        )
        conn.commit()

        
        # 統計取得
        total_daily = 0
        for db_p in [DB_FILE_FANZA, DB_FILE_DLSITE, DB_FILE_DIGIKET]:
            if not os.path.exists(db_p): continue
            _conn = db_connect(db_p)
            count = _conn.execute("SELECT COUNT(*) FROM novelove_posts WHERE status='published' AND date(published_at) = date('now', 'localtime')").fetchone()[0]
            total_daily += count
            _conn.close()

        inventory_list = []
        if os.path.exists(DB_FILE_FANZA):
            _c = db_connect(DB_FILE_FANZA)
            c_lovecal = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND product_url LIKE '%lovecul.dmm.co.jp%'").fetchone()[0]
            c_dmm     = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site LIKE '%ebook%' AND product_url NOT LIKE '%lovecul.dmm.co.jp%'").fetchone()[0]
            c_fanza   = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending' AND site NOT LIKE '%ebook%' AND product_url NOT LIKE '%lovecul.dmm.co.jp%'").fetchone()[0]
            inventory_list.extend([f"FANZA {c_fanza}", f"DMM {c_dmm}", f"らぶカル {c_lovecal}"])
            _c.close()
        if os.path.exists(DB_FILE_DLSITE):
            _c = db_connect(DB_FILE_DLSITE)
            c_dl = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
            inventory_list.append(f"DLsite {c_dl}")
            _c.close()
        if os.path.exists(DB_FILE_DIGIKET):
            _c = db_connect(DB_FILE_DIGIKET)
            c_dk = _c.execute("SELECT count(*) FROM novelove_posts WHERE status='pending'").fetchone()[0]
            inventory_list.append(f"DigiKet {c_dk}")
            _c.close()
        inventory_str = " / ".join(inventory_list) + " 件"

        notify_discord(
            f"✅ **[{site_label}] [{_genre_label(row['genre'])}] 投稿成功！**\n"
            f"**タイトル**: {wp_title}\n"
            f"**統計**: 今日 {total_daily}件目 / スコア{ai_score} / あらすじ{desc_c_len}文字 / 記事{words}文字 / ライター: {rev_name}\n"
            f"**投稿待ち在庫**: {inventory_str}\n"
            f"**URL**: {link}",
            username="ノベラブ通知くん"
        )
        logger.info(f"✅ 投稿成功！ URL: {link}")
        return True, None
    else:
        cursor.execute("UPDATE novelove_posts SET status='excluded', last_error='wp_post_failed' WHERE product_id=?", (pid,))
        conn.commit()
        logger.warning(f"⚠️ WP投稿失敗: {pid} (status='excluded' に変更)")
        return False, "wp_post_failed"

# === ランキング記事 ===
def fetch_ranking_dmm_fanza(site, genre):
    """v11.2.0: 漫画(comic)と小説(novel)を統合して取得"""
    results = []
    is_bl = (genre == "BL")
    
    # 漫画と小説の両方を取得してマージ
    for dtype in ["comic", "novel"]:
        items = []
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
                    items.append(item)
        except Exception as e:
            logger.error(f"DMM API Fetch Error ({site}/{genre}/{dtype}): {e}")
        
        results.append(items)

    # インターリーブ（交互）または単純マージして上位5件
    final_items = []
    # 漫画上位、小説上位を混ぜて各サイトの「総合ランキング」を再構築
    for i in range(10):
        for sub_list in results:
            if i < len(sub_list):
                item = sub_list[i]
                title = item.get("title", "")
                base_url = item.get("URL", "")
                encoded_url = urllib.parse.quote(base_url, safe="")
                af_id = DMM_AFFILIATE_LINK_ID or "novelove-001"
                ch_params = "&ch=toolbar&ch_id=text"
                aff_url = (f"https://al.fanza.co.jp/?lurl={encoded_url}&af_id={af_id}{ch_params}"
                           if site == "FANZA" else
                           f"https://al.dmm.com/?lurl={encoded_url}&af_id={af_id}{ch_params}")
                desc, _ = scrape_description(item.get("URL", ""), site=site, genre=genre)
                if _is_noise_content(title, desc): continue
                
                final_items.append({
                    "title": title, "url": aff_url,
                    "image_url": item.get("imageURL", {}).get("large", ""),
                    "description": desc,
                    "content_id": item.get("content_id", "")
                })
                if len(final_items) >= 5: return final_items
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
    """
    items_xml = ""
    for idx, item in enumerate(items):
        desc = mask_input(item.get("description", ""), level=1)[:300]
        items_xml += f'\n<item rank="{idx+1}">\n  <title>{item["title"]}</title>\n  <description>{desc}...</description>\n</item>\n'

    # MC（左）の吹き出し
    mc_open  = f'<div class="speech-bubble-left"><img src="/wp-content/uploads/icons/{reviewer["face_image"]}.png" alt="{reviewer["name"]}" /><div class="speech-text">'
    mc_close = '</div></div>'

    if guest is None:
        # フォールバック: 旧来の1名形式
        return f'''あなたは「{reviewer["name"]}」として、今週の{site_name}における{genre}総合人気ランキング（漫画＋小説）TOP5を紹介するアフィリエイト記事を執筆してください。
【キャラクター設定: {reviewer["name"]}】
・性格: {reviewer["personality"]}
・文体: {reviewer["tone"]}
・挨拶: {reviewer["greeting"]}
【執筆ルール】HTML形式で出力してください。
※当サイトは漫画・小説専門です。「聴く」「イヤホン」などの音声表現は避け、「読む・見る」体験として紹介してください。
1. 冒頭キャラコメント
{mc_open}（{reviewer["name"]}の口調による挨拶と期待感。60〜80字以内）{mc_close}
2. ランキングTOP5（各作品につき紹介文＋推しポイント吹き出し）
[IMAGE_{{rank}}] [RANK_BADGE_{{rank}}] [TITLE_{{rank}}] [REVIEW_LINK_{{rank}}]
3. 締めキャラコメント
{mc_open}（振り返りと読者への呼びかけ。100〜120字以内）{mc_close}
【ランキングデータ】
{items_xml}
'''

    # ゲスト（右）の吹き出し
    guest_open  = f'<div class="speech-bubble-right"><img src="/wp-content/uploads/icons/{guest["face_image"]}.png" alt="{guest["name"]}" /><div class="speech-text">'
    guest_close = '</div></div>'

    # 2人の関係性テキストを取得
    relationship = get_relationship(reviewer["id"], guest["id"])

    return f'''今回は「{reviewer["name"]}」（メインMC）と「{guest["name"]}」（ゲスト）の2人の対話形式で、今週の{site_name}における{genre}総合人気ランキング（漫画＋小説）TOP5を紹介するアフィリエイト記事を執筆してください。
【メインMC: {reviewer["name"]}】
・性格: {reviewer["personality"]}
・文体: {reviewer["tone"]}
・挨拶: {reviewer["greeting"]}
【ゲスト: {guest["name"]}】
・性格: {guest["personality"]}
・文体: {guest["tone"]}
・挨拶: {guest["greeting"]}
【2人の関係性】
{relationship}
【執筆の最重要ルール（必ず守ること）】
1. 地の文は一切書かないこと。すべての文章を以下のどちらかの吹き出しHTMLで表現すること。
2. {reviewer["name"]}の発言には必ず「メインMC吹き出し」を使用すること:
{mc_open}（セリフ）{mc_close}
3. {guest["name"]}の発言には必ず「ゲスト吹き出し」を使用すること:
{guest_open}（セリフ）{guest_close}
4. 2人の性格の違いと関係性に基づいた自然なテンポで会話を進めること。
5. raw HTMLのみを出力。```やコードブロックは使わないこと。
6. ※当サイトは漫画・小説専門です。「聴く」「イヤホン」などの音声表現は避け、「読む・見る」体験として紹介してください。
【記事の構成】
- 冒頭：2人のオープニングトーク（お互いに挨拶し、今週のランキングへの期待を語る。合計4〜6往復。）
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
'''

def _post_ranking_article_to_wordpress(title, content, genre, site_name, top_image_url="", excerpt="", reviewer_name="", guest_name=""):
    now = datetime.now()
    week = str((now.day - 1) // 7 + 1)
    slug = f"{site_name.lower()}-{genre.lower()}-ranking-{now.strftime('%Y')}-{now.strftime('%m')}-w{week}"
    
    tags_to_add = []
    if guest_name:
        tags_to_add.append(guest_name)
        
    wp_url = post_to_wordpress(
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
            c.execute("""INSERT OR REPLACE INTO novelove_posts
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
    # ★ 緊急停止チェック
    if is_emergency_stop():
        return

    logger.info("=" * 60)
    logger.info("ランキング記事自動生成モード開始")
    
    # クールダウンチェック (ランキング投稿: 12時間 = 720分)
    # v11.4.13: post_type='ranking' を指定して独立判定
    is_ready, elapsed = _check_global_cooldown(720, post_type='ranking')
    if not is_ready:
        logger.info(f"🕒 ランキングクールダウン中（{elapsed:.1f}分経過/720分）。終了します。")
        return

    try:
        with open(RANK_LOCK_FILE, "w") as f:
            f.write(datetime.now().isoformat())
    except Exception as e:
        logger.error(f"ランキングロック作成失敗: {e}")
        return
    try:
        # 曜日判定 (0=月, 1=火, 2=水, ... 6=日)
        weekday = datetime.now().weekday()
        # スケジュール: 日=FANZA, 月=DLsite, 火=DMM, 水=DigiKet
        schedule = {6: "FANZA", 0: "DLsite", 1: "DMM", 2: "DigiKet"}
        
        target_site = schedule.get(weekday)
        if not target_site:
            logger.info(f"今日はランキング投稿日ではありません (曜日コード: {weekday})")
            return

        sites = [target_site]
        medals = {1: "🥇 1位", 2: "🥈 2位", 3: "🥉 3位", 4: "4位", 5: "5位"}
        site_labels = {"FANZA": "FANZA", "DMM": "DMM.com", "DLsite": "DLsite", "DigiKet": "DigiKet"}
        
        for i, site in enumerate(sites):
            logger.info(f"--- ランキング処理: {site} ---")
            for genre in ["BL", "TL"]:
                logger.info(f"  [{genre}総合] 取得開始...")
                if site in ("FANZA", "DMM"):
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
                    html_text, err = _call_deepseek_raw(messages, max_tokens=6000, temperature=0.7)
                    if err == "ok" and html_text:
                        content_html = html_text
                        break
                    elif err == "rate_limit":
                        logger.warning(f"  [ランキング] DeepSeek レート制限 → 30秒待機")
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
                post_title = f"【{site_labels[site]}】今週の{genre_full}ランキング TOP5！（{title_date}）"
                meta_desc = f"【{site_labels[site]}】今週の{genre_full}総合ランキング（漫画＋小説）TOP5を{reviewer['name']}が熱く紹介！"
                
                final_content = content_html
                disp_site = site_labels.get(site, site)
                
                # 相互リンク (BL <=> TL)
                other_genre = "TL" if genre == "BL" else "BL"
                other_slug = get_ranking_slug(site, other_genre)
                other_url = f"{WP_SITE_URL}/{other_slug}/"
                _now2 = datetime.now()
                _wk2 = (_now2.day - 1) // 7 + 1
                cross_link = (
                    f'<div style="border:1px solid #f0c0c0; border-radius:8px; padding:15px; margin:20px 0; background:#fff8f8;">\n'
                    f'<p style="margin:0 0 8px; font-weight:bold; color:#c0607f;">📚 あわせて読みたい</p>\n'
                    f'<p><a href="{other_url}">【{disp_site}】{other_genre}総合ランキング（{_now2.year}年{_now2.month}月第{_wk2}週）はこちら</a></p>\n'
                    f'</div>\n'
                )
                final_content += cross_link
                
                # クレジット
                if "FANZA" in disp_site:
                    ranking_credit = f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;"><a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/r18_135_17.gif" width="135" height="17" alt="WEB SERVICE BY FANZA" style="border:none;"></a></div>'
                elif "DMM" in disp_site:
                    ranking_credit = f'<div class="novelove-credit" style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee;"><a href="https://affiliate.dmm.com/api/"><img src="https://pics.dmm.com/af/web_service/com_135_17.gif" width="135" height="17" alt="WEB SERVICE BY DMM.com" style="border:none;"></a></div>'
                else:
                    ranking_credit = f'<p style="text-align:center; margin-top:40px; padding-top:15px; border-top:1px solid #eee; font-size:0.8em; color:#bbb;">PRESENTED BY {disp_site} / Novelove Affiliate Program</p>'
                
                final_content += ranking_credit
                
                logger.info(f"  -> {genre} 投稿実行中...")
                guest_n = guest["name"] if guest else ""
                _post_ranking_article_to_wordpress(post_title, final_content, genre, site, top_image_url, excerpt=meta_desc, reviewer_name=reviewer["name"], guest_name=guest_n)
                
                # 記事間の待機 (30分)
                if genre == "BL":
                    logger.info("次のカテゴリ投稿まで30分待機します...")
                    time.sleep(1800)
    finally:
        if os.path.exists(RANK_LOCK_FILE):
            try:
                os.remove(RANK_LOCK_FILE)
            except Exception as e:
                logger.error(f"ランキングロック削除失敗: {e}")
    logger.info("ランキング記事自動生成モード終了")
    logger.info("=" * 60)

def main():
    # ★ 緊急停止チェック（最頂部）
    if is_emergency_stop():
        logger.info("🚨 緊急停止中のためスキップ。解除: rm emergency_stop.lock")
        return

    logger.info("Novelove エンジン v13.8.0 起動")
    init_db()
    # メインロックチェック
    if os.path.exists(MAIN_LOCK_FILE):
        mtime = os.path.getmtime(MAIN_LOCK_FILE)
        if time.time() - mtime > 7200:
            logger.warning("🚨 メインロックが2時間を超えています。強制解除して続行します。")
            try:
                os.remove(MAIN_LOCK_FILE)
            except Exception as e:
                logger.error(f"ロック解除失敗: {e}")
                return
        else:
            logger.info("🕒 メイン処理は既に実行中です。終了します。")
            return

    # ランキングロックチェック
    if os.path.exists(RANK_LOCK_FILE):
        logger.info("🕒 ランキング処理が実行中です。通常投稿はスキップします。")
        return

    try:
        with open(MAIN_LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logger.error(f"🚨 メインロック作成失敗: {e}")
        return

    try:
        _run_main_logic()
    finally:
        try:
            if os.path.exists(MAIN_LOCK_FILE):
                os.remove(MAIN_LOCK_FILE)
        except Exception as e:
            logger.error(f"🚨 メインロック解除失敗: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Novelove Auto Posting Tool")
    parser.add_argument("--ranking", action="store_true", help="Run the ranking generation workflow")
    args = parser.parse_args()
    if args.ranking:
        process_ranking_articles()
    else:
        main()
