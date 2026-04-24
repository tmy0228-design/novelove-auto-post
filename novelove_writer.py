#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novelove_writer.py — Novelove AI執筆エンジン
プロンプト構築・DeepSeek API通信・記事生成を担当
"""
import random
import re
import time
import requests

from novelove_soul import REVIEWERS, MOOD_PATTERNS, get_relationship, AI_TAG_WHITELIST

from novelove_core import (
    logger, ArticleResult,
    get_affiliate_button_html,
    _get_reviewer_for_genre, _genre_label,
    DEEPSEEK_API_KEY, OPENROUTER_API_KEY,
)

from novelove_fetcher import (
    mask_input,
    _check_image_ok,
)

# === v17.5.0: DeepSeek-V4 API完全移行 ===
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
MODEL_ECONOMY      = "deepseek-v4-flash"   # 本日発表のV4 Flashへ明示的アップデート
MODEL_PREMIUM      = "deepseek-chat"   # 本気モード：現在V4-Flashで統合
DEEPSEEK_MODEL   = MODEL_ECONOMY

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
{f"作品の属性・シチュエーション要素: {original_tags}" if original_tags else ""}
"""
    messages = [
        {"role": "system", "content": "あなたはプロの編集者です。情報量と面白さだけで厳密に審査してください。"},
        {"role": "user", "content": prompt}
    ]
    # A-2: 50は切れすぎるリスクがあるため100に増やしてマージンを確保（コスト増は微小）
    # v17.6.0: スコア審査は数字1文字だけ返せばよいので推論(Thinking)を無効化する
    # 推論ONだとmax_tokens=100がすべて推論に消費され、contentが空→スコア0になる致命的バグの修正
    content, err = _call_deepseek_raw(messages, max_tokens=100, temperature=0.3, thinking_disabled=True)
    if err != "ok" or not content:
        return 0

    match = re.search(r"[1-5]", content)
    if match:
        return int(match.group())
    return 0

# === v16.0.0: HTML骨格パターン選択関数 ===
def _select_html_pattern(ai_score, desc_length, has_tags):
    """
    記事のHTML骨格パターンを重み付きランダムで選択する。
    A: リスト型（橙来のノベラブ基本形式）
    B: 深掴り型（h3見出し＋段落）
    C: Q&A型（問答形式）
    D: 引用型（blockquote＋熱いリアクション）
    """
    # タグなしの場合はBを除外（属性タグがないと深掴り多觓的に書きにくい）
    if ai_score >= 5:
        if has_tags:
            pattern = random.choices(["A", "B", "C", "D"], weights=[15, 25, 25, 35])[0]
        else:
            pattern = random.choices(["A", "C", "D"], weights=[20, 35, 45])[0]
    elif desc_length >= 300:
        # スコイ4 + あらすじ300字以上: Dも使用可
        if has_tags:
            pattern = random.choices(["A", "B", "C", "D"], weights=[35, 25, 25, 15])[0]
        else:
            pattern = random.choices(["A", "C", "D"], weights=[40, 30, 30])[0]
    else:
        # スコイ4 + あらすじ300字未満: Dは封印
        if has_tags:
            pattern = random.choices(["A", "B", "C"], weights=[40, 30, 30])[0]
        else:
            pattern = random.choices(["A", "C"], weights=[50, 50])[0]
    logger.info(f"  [Pattern] {pattern} selected (score={ai_score}, desc={desc_length}字, tags={'yes' if has_tags else 'no'})")
    return pattern

# === AI執筆 ===
def build_prompt(target, reviewer, mask_level=0, is_novel=False, is_guest=False, mood="", ai_score=4, original_tags="", is_exclusive=False, pattern="A"):
    """
    v17.8.0: プロンプト大幅圧縮。ルール13個→4個、プロンプト文字数約半減。
    システム由来の用語をプロンプトから完全排除。
    NGフレーズ/事実性ルールはプロンプトから削除（コード側で対処 or 不要）。
    """
    safe_title = mask_input(target["title"], mask_level)
    safe_desc  = mask_input(target["description"], mask_level)
    chat_open  = f'<div class="speech-bubble-left"><img src="https://novelove.jp/wp-content/uploads/icons/{reviewer["face_image"]}.png" alt="{reviewer["name"]}" /><div class="speech-text">'
    chat_close = '</div></div>'

    focus = reviewer.get("novel_focus", "") if is_novel else reviewer.get("manga_focus", "")
    medium_label = "小説" if is_novel else "漫画"

    novel_rules = ""
    if is_novel:
        novel_rules = (
            "\n※小説作品のため「コマ」「見開き」「絵」「描画」等の漫画表現は使わず、"
            "「文章」「心理描写」「行間」「文体」「語り口」等の活字特有の視点で語ること。"
        )

    guest_hint = ""
    if is_guest:
        guest_hint = (
            f"\n※{reviewer['name']}のメイン担当は別ジャンルだが、こうした作品も時折嗜んでいる。"
            f"初心者のような発言は禁止。専門外ならではの率直な感想として熱く語ること。"
        )

    mood_note = f"\n今回のモード: {mood}" if mood else ""
    tag_line = f"\nテーマ傾向（※参考情報。記事への転記・列挙は禁止）: {original_tags}" if original_tags else ""

    exclusive_note = ""
    if is_exclusive:
        site_name = str(target.get('site', '')).split(':')[0]
        exclusive_note = f"\n販売形態: {site_name}専売"

    # === パターン別 HTML構成 & パターン固有ルール ===
    if ai_score >= 5:
        if pattern == "B":
            pattern_rules = "4. h3見出しはあらすじの事実に基づくこと。存在しない設定の見出しは禁止。各セクションは対応する事実のみで書く。"
            html_structure = f"""
{chat_open}（60〜110字。作品への自然なリアクションで書き出す）{chat_close}
<h2>（作品の魅力を引き出す見出し）</h2>
<p>あらすじ・世界観・テーマ要素を400〜700字で解説。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h2>キャラクターの魅力と関係性</h2>
<p>キャラクターの性格や関係性の変化を400〜700字で分析。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h3>（魅力ポイント1。事実に基づく見出し）</h3>
<p>あらすじの情報に基づき200〜350字で深掘り。</p>
<h3>（魅力ポイント2。事実に基づく見出し）</h3>
<p>あらすじの情報に基づき200〜350字で深掘り。</p>
{chat_open}（120〜200字。熱い総評）{chat_close}
"""
        elif pattern == "C":
            pattern_rules = "4. Q&Aの質問はあらすじから答えられる自然な疑問のみ。回答もあらすじの事実のみで書く。"
            html_structure = f"""
{chat_open}（60〜110字。作品への自然なリアクションで書き出す）{chat_close}
<h2>（作品の魅力を引き出す見出し）</h2>
<p>あらすじ・世界観・テーマ要素を400〜700字で解説。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h2>キャラクターの魅力と関係性</h2>
<p>キャラクターの性格や関係性の変化を400〜700字で分析。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h3>Q. （あらすじから答えられる疑問1）</h3>
<p>A. あらすじの事実のみで回答。200〜300字。</p>
<h3>Q. （あらすじから答えられる疑問2）</h3>
<p>A. あらすじの事実のみで回答。200〜300字。</p>
<h3>Q. （あらすじから答えられる疑問3）</h3>
<p>A. あらすじの事実のみで回答。200〜300字。</p>
{chat_open}（120〜200字。熱い総評）{chat_close}
"""
        elif pattern == "D":
            pattern_rules = "4. 引用はあらすじ原文から一言一句変えずにコピー。改変・創作した引用は禁止。"
            html_structure = f"""
{chat_open}（60〜110字。作品への自然なリアクションで書き出す）{chat_close}
<h2>（作品の魅力を引き出す見出し）</h2>
<p>あらすじ・世界観・テーマ要素を400〜700字で解説。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h2>キャラクターの魅力と関係性</h2>
<p>キャラクターの性格や関係性の変化を400〜700字で分析。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h2>（心に刺さった一文を辿る見出し）</h2>
<div class="novelove-quote" style="border-left:4px solid #d81b60; padding:12px 20px; margin:20px 0; background:#fff5f9; color:#555;">
（あらすじの原文をそのまま引用。改変禁止。）
</div>
<p>この引用がなぜ読者の心を捉えるのか300〜500字で語る。</p>
{chat_open}（120〜200字。熱い総評）{chat_close}
"""
        else:  # pattern == "A"
            pattern_rules = "4. 見どころはこの作品固有の魅力を優先順に。「こんな人におすすめ」は具体的な設定に基づく。"
            html_structure = f"""
{chat_open}（60〜110字。作品への自然なリアクションで書き出す）{chat_close}
<h2>（作品の魅力を引き出す見出し）</h2>
<p>あらすじ・世界観・テーマ要素を400〜700字で解説。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h2>キャラクターの魅力と関係性</h2>
<p>キャラクターの性格や関係性の変化を400〜700字で分析。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h2>見どころ</h2>
<ul>
  <li><strong>（魅力1）</strong>：具体的に。</li>
  <li><strong>（魅力2）</strong>：具体的に。</li>
  <li><strong>（魅力3）</strong>：具体的に。</li>
</ul>
<h2>こんな人におすすめ</h2>
<ul style="list-style-type: none; padding-left: 0;">
  <li>✅ おすすめの層1</li>
  <li>✅ おすすめの層2</li>
  <li>✅ おすすめの層3</li>
</ul>
{chat_open}（120〜200字。熱い総評・布教）{chat_close}
"""
    elif ai_score == 4:
        if pattern == "B":
            pattern_rules = "4. h3見出しはあらすじの事実に基づくこと。存在しない設定の見出しは禁止。各セクションは対応する事実のみで書く。"
            html_structure = f"""
{chat_open}（60〜110字。作品への自然なリアクションで書き出す）{chat_close}
<h2>（作品の魅力を引き出す見出し）</h2>
<p>あらすじ・世界観・テーマ要素を300〜600字で解説。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h3>（魅力ポイント1。事実に基づく見出し）</h3>
<p>あらすじの情報に基づき150〜250字で深掘り。</p>
<h3>（魅力ポイント2。事実に基づく見出し）</h3>
<p>あらすじの情報に基づき150〜250字で深掘り。</p>
{chat_open}（120〜200字。熱い総評）{chat_close}
"""
        elif pattern == "C":
            pattern_rules = "4. Q&Aの質問はあらすじから答えられる自然な疑問のみ。回答もあらすじの事実のみで書く。"
            html_structure = f"""
{chat_open}（60〜110字。作品への自然なリアクションで書き出す）{chat_close}
<h2>（作品の魅力を引き出す見出し）</h2>
<p>あらすじ・世界観・テーマ要素を300〜600字で解説。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h3>Q. （あらすじから答えられる疑問1）</h3>
<p>A. あらすじの事実のみで回答。150〜250字。</p>
<h3>Q. （あらすじから答えられる疑問2）</h3>
<p>A. あらすじの事実のみで回答。150〜250字。</p>
<h3>Q. （あらすじから答えられる疑問3）</h3>
<p>A. あらすじの事実のみで回答。150〜250字。</p>
{chat_open}（120〜200字。熱い総評）{chat_close}
"""
        elif pattern == "D":
            pattern_rules = "4. 引用はあらすじ原文から一言一句変えずにコピー。改変・創作した引用は禁止。"
            html_structure = f"""
{chat_open}（60〜110字。作品への自然なリアクションで書き出す）{chat_close}
<h2>（作品の魅力を引き出す見出し）</h2>
<p>あらすじ・世界観・テーマ要素を300〜600字で解説。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h2>（心に刺さった一文を辿る見出し）</h2>
<div class="novelove-quote" style="border-left:4px solid #d81b60; padding:12px 20px; margin:20px 0; background:#fff5f9; color:#555;">
（あらすじの原文をそのまま引用。改変禁止。）
</div>
<p>この引用がなぜ読者の心を捉えるのか200〜350字で語る。</p>
{chat_open}（120〜200字。熱い総評）{chat_close}
"""
        else:  # pattern == "A"
            pattern_rules = "4. 見どころはこの作品固有の魅力を優先順に。「こんな人におすすめ」は具体的な設定に基づく。"
            html_structure = f"""
{chat_open}（60〜110字。作品への自然なリアクションで書き出す）{chat_close}
<h2>（作品の魅力を引き出す見出し）</h2>
<p>あらすじ・世界観・テーマ要素を300〜600字で解説。</p>
{chat_open}（50〜90字。リアクション）{chat_close}
<h2>見どころ</h2>
<ul>
  <li><strong>（魅力1）</strong>：具体的に。</li>
  <li><strong>（魅力2）</strong>：具体的に。</li>
  <li><strong>（魅力3）</strong>：具体的に。</li>
</ul>
<h2>こんな人におすすめ</h2>
<ul style="list-style-type: none; padding-left: 0;">
  <li>✅ おすすめの層1</li>
  <li>✅ おすすめの層2</li>
  <li>✅ おすすめの層3</li>
</ul>
{chat_open}（120〜200字。熱い総評）{chat_close}
"""
    else:
        # スコア3以下：コンパクト記事
        pattern_rules = "4. 見どころはあらすじの事実のみ。最大2点。書ける事実が1点なら1点で完結。"
        html_structure = f"""
{chat_open}（40〜60字。作品への自然なリアクション）{chat_close}
<h2>（作品の魅力を一言で表す見出し）</h2>
<p>あらすじ・世界観を200〜400字で簡潔に解説。事実のみ。</p>
<h2>見どころ</h2>
<ul>
  <li><strong>（魅力1）</strong>：簡潔に。</li>
</ul>
{chat_open}（60〜80字の総評）{chat_close}
"""

    # キャッチフレーズ指示（20%確率）
    catchphrase_instruction = ""
    cp = reviewer.get("catchphrases")
    if cp and isinstance(cp, dict) and random.random() < 0.20:
        catchphrase = random.choices(
            [cp["main"], cp["sub"]],
            weights=[7, 3]
        )[0]
        catchphrase_instruction = (
            f"\n\n[特別指示]\n"
            f"今回の吹き出しコメントのどこかで、自然な流れの中で「{catchphrase}」という表現を1回だけ使ってください。\n"
        )

    return f"""あなたは「Novelove」のライター「{reviewer["name"]}」です。

【あなたのキャラクター】
性格: {reviewer["personality"]}
口調: {reviewer["tone"]}
注目点（{medium_label}）: {focus}{mood_note}{guest_hint}{novel_rules}

【ルール】
1. 吹き出しコメント＝あなたの口調全開。本文（h2/p/ul/liタグ内）＝標準語・ですます調で客観的に。この2つは絶対に混ぜない。
2. あらすじに書かれていない設定・キャラ名・展開を創作しない。あらすじの事実は自信を持って断定してよい。「読み終えて」等の読了済み表現は禁止。
3. 直接的な性的単語は官能的な比喩に。テーマ傾向は「○○な関係性が堪らない」のように自然な文脈で語る（リスト列挙・メタ言及は禁止）。
{pattern_rules}

【作品】
タイトル: {safe_title}
ジャンル: {_genre_label(target.get("genre", ""))}（※BL・TLの誤記は厳禁）
あらすじ: {safe_desc}{tag_line}{exclusive_note}

【出力（HTMLのみ。説明文や指示の復唱は出力しない）】

{html_structure}

TAGS: （作品に合うものを最大3つ、カンマ区切り。該当なしは「なし」）
BL系: オメガバース/ヤンデレ/スパダリ/執着/年下攻め/幼なじみ/ケンカップル/主従/サラリーマン/年の差/転生/契約/再会/一途/運命
TL系: 溺愛/身分差/契約結婚/御曹司/騎士/オフィスラブ/腹黒/同居/嫉妬/強引/独占欲/初恋/記憶喪失/年の差/ハッピーエンド

SEO_META:
seo_title=（32文字以内。あらすじのキーワードで読者の感情を揺さぶる）
meta_desc=（80字程度。あらすじに基づき誰に刺さるか断言する）
{catchphrase_instruction}
"""

# === v17.5.0: OpenRouter フォールバック設定 ===
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_FALLBACK_MODEL = "deepseek/deepseek-chat"  # OpenRouter経由のDeepSeek
_fallback_notified = False  # Discord通知の重複防止フラグ

def _call_deepseek_raw(messages, max_tokens=200, temperature=0.3, model_id=None, thinking_disabled=False):
    """
    DeepSeek V4 APIへの共通リクエスト関数（v17.5.0）。
    DeepSeek直接APIが失敗した場合、OpenRouter経由に自動フォールバックする。
    thinking_disabled: Trueの場合、推論(Thinking)を無効化する。
                       スコア審査など数字1文字だけ返す用途では必須。
    戻り値: (text, error_type)
      error_type: "ok" / "rate_limit" / "api_error"
    """
    global _fallback_notified
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY が設定されていません")
        return "", "api_error"

    _model = model_id or DEEPSEEK_MODEL

    # === Phase 1: DeepSeek 直接API ===
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": _model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    # v17.6.0: スコア審査等の短い応答では推論を無効化（トークン枯渇防止）
    if thinking_disabled:
        payload["thinking"] = {"type": "disabled"}
    try:
        r = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
        if r.status_code == 200:
            data = r.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return text, "ok"
        if r.status_code == 429:
            logger.warning("DeepSeek レート制限 (429) → OpenRouterフォールバックへ")
        elif r.status_code in (402, 500, 502, 503):
            logger.warning(f"DeepSeek APIエラー ({r.status_code}) → OpenRouterフォールバックへ")
        else:
            logger.warning(f"DeepSeek APIエラー: {r.status_code} {r.text[:200]}")
            return "", "api_error"
    except requests.exceptions.Timeout:
        logger.warning("DeepSeek タイムアウト → OpenRouterフォールバックへ")
    except Exception as e:
        logger.warning(f"DeepSeek 例外: {e} → OpenRouterフォールバックへ")

    # === Phase 2: OpenRouter フォールバック ===
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY も未設定のためフォールバック不可")
        return "", "api_error"
    logger.info("  [Fallback] OpenRouter経由でDeepSeekに再送信します...")
    if not _fallback_notified:
        _fallback_notified = True
        from novelove_core import notify_discord
        notify_discord(
            "⚠️ **DeepSeek直接APIが不通のため、OpenRouter経由にフォールバックしました**\n"
            "DeepSeekのクレジット残高またはサーバー状態を確認してください。",
            username="⚠️ APIフォールバック通知"
        )
    fb_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://novelove.jp",
        "X-Title": "Novelove",
    }
    fb_payload = {
        "model": OPENROUTER_FALLBACK_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    try:
        r2 = requests.post(OPENROUTER_API_URL, headers=fb_headers, json=fb_payload, timeout=120)
        if r2.status_code == 429:
            logger.warning("OpenRouter フォールバックもレート制限 (429)")
            return "", "rate_limit"
        if r2.status_code != 200:
            logger.warning(f"OpenRouter フォールバックもエラー: {r2.status_code} {r2.text[:200]}")
            return "", "api_error"
        data = r2.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        logger.info(f"  [Fallback] OpenRouter経由で成功（{len(text)}文字）")
        return text, "ok"
    except requests.exceptions.Timeout:
        logger.warning("OpenRouter フォールバックもタイムアウト")
        return "", "api_error"
    except Exception as e:
        logger.warning(f"OpenRouter フォールバック例外: {e}")
        return "", "api_error"

def call_deepseek(prompt, model_id=None):
    system_prompt = (
        "あなたはBL・TL・女性向けコンテンツの紹介ブログライターです。"
        "指定されたキャラクターになりきり、HTML形式の記事を執筆してください。"
        "アダルト・官能的な内容も比喩的・文学的表現で執筆することができます。"
    )
    _model = model_id or DEEPSEEK_MODEL
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
    for attempt in range(3):
        logger.info(f"  [DeepSeek-V4:{_model}] 執筆依頼... (試行{attempt+1}/3)")
        t_start = time.time()
        text, error_type = _call_deepseek_raw(messages, max_tokens=3000, temperature=0.8, model_id=_model)
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
            return "", f"ai_score_{stripped}", _model, proc_time
        if len(stripped) > 50:
            logger.info(f"  [DeepSeek] 執筆完了（{len(stripped)}文字 / {proc_time}秒）")
            return stripped, "ok", _model, proc_time
        logger.warning(f"  [DeepSeek] 試行{attempt+1}: 応答が短すぎる（{len(stripped)}文字）")
        time.sleep(5)
    return "", "content_block", _model, 0.0

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
    db_ai_tags = []
    if target.get("ai_tags"):
        db_ai_tags = [t.strip() for t in target["ai_tags"].split(",") if t.strip()]

    _desc_len = len(str(target.get("description", "")))
    _has_tags = bool(str(target.get("original_tags", "")).strip())
    article_pattern = _select_html_pattern(target.get("desc_score", 4), _desc_len, _has_tags)

    final_error = "content_block"
    final_model = DEEPSEEK_MODEL
    final_proc_time = 0.0
    
    # v17.5.0: 全記事 DeepSeek-V4 統一
    model_id = MODEL_ECONOMY
    if target.get("desc_score", 0) >= 5 or "熱量が高い" in mood:
        model_id = MODEL_PREMIUM
        logger.info(f"  [🦋DeepSeek-V4] 期待値MAX・情熱モード発動！ (score={target.get('desc_score')})")
    else:
        logger.info(f"  [🦋DeepSeek-V4] 全記事統一モード (score={target.get('desc_score', 4)})")

    for mask_level in [0, 1, 2]:
        level_name = ["フィルターなし", "軽めフィルター", "ガチガチフィルター"][mask_level]
        logger.info(f"  [{level_name}] で執筆試行中...")
        prompt = build_prompt(target, reviewer, mask_level, is_novel=is_novel, is_guest=is_guest, mood=mood, ai_score=target.get("desc_score", 4), original_tags=target.get("original_tags", ""), is_exclusive=bool(target.get("is_exclusive", 0)), pattern=article_pattern)
        content, error_type, model_name, proc_time = call_deepseek(prompt, model_id=model_id)
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
                            if allowed in t or (len(t) >= 2 and t in allowed):
                                ai_tags_from_ai.append(allowed)
                                break
                    ai_tags_from_ai = list(dict.fromkeys(ai_tags_from_ai))[:3]

            # 4. マークダウンのコードブロック除去 (```html 等のゴミ文字対策)
            import re
            content = re.sub(r'^```(?:html|xml)?\s*', '', content, flags=re.IGNORECASE)
            content = re.sub(r'\s*```$', '', content)
            # 万が一AIが「アフィリエイトURL:」や「作者のXは〜」などを出力した場合の強制サニタイズ
            content = re.sub(r'アフィリエイトURL[：:].*?(?=<|$)', '', content)
            content = re.sub(r'作者のX[：は].*?(?=<|$)', '', content)
            content = re.sub(r'https?://[^\s<"]+', '', content)
            content = content.strip()

            # === v17.8.2: 自動整形のwpautop対策 ===
            # AIが吹き出しHTMLに改行を含めた場合、WordPressが<p>を自動挿入しCSSのflexレイアウトが崩壊するのを防ぐ
            def _wrap_html_block(m):
                t = m.group(0).strip()
                return f"<!-- wp:html -->\n{t}\n<!-- /wp:html -->"
            content = re.sub(r'<div class="speech-bubble-(?:left|right)".*?</div>\s*</div>', _wrap_html_block, content, flags=re.DOTALL)

            # === v17.7.0: speech-bubble 閉じ漏れ検出 → 投稿中止 + Discord通知 ===
            # トークン切れ等でAIが末尾の </div></div> を出力し損ねた場合、
            # 記事が不完全な状態のまま投稿することを防ぐ。
            # 黙って補完するのではなく、異常として検出・通知して次のmask_levelに回す。
            import re as _re
            _open_count  = len(_re.findall(r'class="speech-bubble-left"', content))
            _close_count = content.count('</div></div>')
            if _open_count > 0 and _close_count < _open_count:
                _missing = _open_count - _close_count
                logger.warning(
                    f"  [v17.7.0] speech-bubble 閉じ漏れ検出: {_missing}箇所不足 "
                    f"(open={_open_count}, close={_close_count}) → この試行をスキップ"
                )
                from novelove_core import notify_discord
                notify_discord(
                    f"⚠️ **トークン切れ検出** [{target.get('product_id', '?')}]\n"
                    f"speech-bubble の閉じタグが {_missing}箇所不足しています。\n"
                    f"記事が途中で切れているため投稿をスキップし、次の試行に移ります。\n"
                    f"（max_tokens不足またはAPIの応答切断が原因）",
                    username="⚠️ 構造異常通知"
                )
                continue  # 次のmask_levelで再試行

            if not _check_image_ok(target["image_url"]):
                logger.warning(f"  [画像NG] 投稿直前チェックで無効: {target['image_url']}")
                return ArticleResult(status="image_missing", model=model_name, level=level_name, proc_time=proc_time)

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

            # 専売タグの付与は auto_post.py 側で安全かつ完全に処理される。
            # (v15.4.1: 二重実装削除時に残存した未使用変数も同時に整理済み)
            
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
            return ArticleResult(
                wp_title=wp_title,
                content=full_content,
                excerpt=excerpt,
                seo_title=seo_title,
                is_r18=is_r18_val,
                status="ok",
                model=model_name,
                level=level_name,
                proc_time=proc_time,
                word_count=word_count,
                reviewer_name=reviewer_name,
                ai_tags=ai_tags_from_ai,
                ai_score=ai_score,
                article_pattern=article_pattern,  # v16.0.0
            )
        if error_type == "rate_limit":
            logger.warning("  レート制限 → フィルター試行を中断")
            break
        if error_type == "api_error":
            # A-1: サーバーエラー/タイムアウトはマスクレベルと無関係のため即中断（無駄なAPI課金を防止）
            logger.warning("  APIエラー（サーバー側障害） → フィルター試行を中断")
            break
        logger.warning(f"  [{level_name}] 失敗 → 次のフィルターレベルへ")
    return ArticleResult(status=final_error, model=final_model, level="None", proc_time=final_proc_time)


# === A+C方式: サムネURL生成ヘルパー ===
