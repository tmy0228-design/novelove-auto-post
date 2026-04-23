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

from novelove_soul import REVIEWERS, MOOD_PATTERNS, FACT_GUARD, NG_PHRASES, get_relationship, AI_TAG_WHITELIST

from novelove_core import (
    logger, ArticleResult,
    get_affiliate_button_html,
    _get_reviewer_for_genre, _genre_label,
    OPENROUTER_API_KEY,
)

from novelove_fetcher import (
    mask_input,
    _check_image_ok,
)

# === v17.0.0: OpenRouter API設定（Grokハイブリッド）===
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_ECONOMY      = "x-ai/grok-4.1-fast"   # 通常モード：激安・高速
MODEL_PREMIUM      = "x-ai/grok-4.20"       # 本気モード：スコア5 or 熱釯MAX時のみ発動
# 後方互換エイリアス（auto_post.py / nexus_rewrite.py からの直接import対応）
DEEPSEEK_API_URL = OPENROUTER_API_URL
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
{f"公式属性タグ: {original_tags}" if original_tags else ""}
"""
    messages = [
        {"role": "system", "content": "あなたはプロの編集者です。情報量と面白さだけで厳密に審査してください。"},
        {"role": "user", "content": prompt}
    ]
    # A-2: 50は切れすぎるリスクがあるため100に増やしてマージンを確保（コスト増は微小）
    content, err = _call_deepseek_raw(messages, max_tokens=100, temperature=0.3)
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
            f"\n{reviewer['name']}のメイン担当は別ジャンルだが、こうした作品も時折嗜んでいる。初めて読む・初挑戦、といった初心者のような発言は絶対禁止。"
            f"\n普段とは違う角度から作品を見るからこそ気付く独自の魅力を、専門外ならではの率直な感想として自分の言葉で熱く語ること。"
        )

    mood_note = f"\n今回の感情モード: {mood}" if mood else ""
    _tag_rule_nl = "\n"
    _tag_rule_str = (_tag_rule_nl + "[公式属性タグ的活用]" + _tag_rule_nl +
        "作品には以下の公式属性タグが設定されています: " + original_tags + _tag_rule_nl +
        "これらを活かして、具体的で読者に刺さる紹介文・おすすめコメントを書いてください。" +
        "ファイル形式等の形式情報は無視し、内容・属性に関わる情報のみを参考にしてください。"
    ) if original_tags else ""

    intro_rule = "あなたのキャラクターらしい語り口で、作品への自然なリアクションから書き出してください。画像がなくても「この人が書いた」と読者にわかるほど、キャラクターの個性を一貫させること。ただし毎回同じ言い回しの繰り返しにはならないよう、表現に変化をつけること。"

    if ai_score >= 5:
        if pattern == "B":
            allowed_tags = "<h2>, <h3>, <p>"
            pattern_rules = (
                "11. h3見出しの属性テーマは必ずあらすじに書かれている事実に基づくこと。存在しない属性・設定を創作した見出しを付けることは絶対禁止。\n"
                "12. 各h3セクションの本文は、そのh3見出しに対応するあらすじ内の事実のみで書くこと。他のセクションの情報を混在させないこと。"
            )
            html_structure = f"""
{chat_open}（60〜110字程度。{intro_rule}）{chat_close}
<h2>（作品の世界観や魅力を引き出すキャッチーな見出し）</h2>
<p>（標準語で執筆）あらすじ・世界観・作品の属性情報。提供されたすべての有用な情報を深く噛み砕いて400〜700字程度でリッチに解説。</p>
{chat_open}（50〜90字程度。設定へのリアクション。キャラの性格に合った温度感で）{chat_close}
<h2>キャラクターの魅力と関係性</h2>
<p>（標準語で執筆）キャラクターの性格、キャラクター同士の関係性がどう変化するかなど、深い分析を400〜700字程度で執筆。</p>
{chat_open}（50〜90字程度。キャラへのリアクション。キャラの性格に合った温度感で）{chat_close}
<h3>（この作品の決定的魅力ポイント1。事実に基づく具体的な見出し）</h3>
<p>（標準語で執筆）この魅力ポイントを、あらすじの情報のみに基づき200〜350字程度で深掘りする。</p>
<h3>（この作品の決定的魅力ポイント2。事実に基づく具体的な見出し）</h3>
<p>（標準語で執筆）この魅力ポイントを、あらすじの情報のみに基づき200〜350字程度で深掘りする。</p>
{chat_open}（120〜200字程度の総評・おすすめコメント。この作品を絶対に読んでほしいという強い思いを、【キャラクターの性格・口調】に合った形で表現すること。テンションが高いキャラは勢いよく、落ち着いたキャラは静かに深く語ること。）{chat_close}
"""
        elif pattern == "C":
            allowed_tags = "<h2>, <h3>, <p>"
            pattern_rules = (
                "11. Q&Aの質問は読者が実際にGoogle検索しそうな自然な疑問のみを設定すること。あらすじにない内容を質問してはならない。\n"
                "12. 各Aパートの回答はあらすじの事実のみで書くこと。推測・補完・創作は絶対禁止。"
            )
            html_structure = f"""
{chat_open}（60〜110字程度。{intro_rule}）{chat_close}
<h2>（作品の世界観や魅力を引き出すキャッチーな見出し）</h2>
<p>（標準語で執筆）あらすじ・世界観・作品の属性情報。提供されたすべての有用な情報を深く噛み砕いて400〜700字程度でリッチに解説。</p>
{chat_open}（50〜90字程度。設定へのリアクション。キャラの性格に合った温度感で）{chat_close}
<h2>キャラクターの魅力と関係性</h2>
<p>（標準語で執筆）キャラクターの性格、キャラクター同士の関係性がどう変化するかなど、深い分析を400〜700字程度で執筆。</p>
{chat_open}（50〜90字程度。キャラへのリアクション。キャラの性格に合った温度感で）{chat_close}
<h3>Q. （読者が検索しそうな自然な疑問1。あらすじから答えられるものに限る）</h3>
<p>A. （標準語で執筆）あらすじの事実のみで答える。創作・補完禁止。200〜300字程度。</p>
<h3>Q. （読者が検索しそうな自然な疑問2。あらすじから答えられるものに限る）</h3>
<p>A. （標準語で執筆）あらすじの事実のみで答える。創作・補完禁止。200〜300字程度。</p>
<h3>Q. （読者が検索しそうな自然な疑問3。あらすじから答えられるものに限る）</h3>
<p>A. （標準語で執筆）あらすじの事実のみで答える。創作・補完禁止。200〜300字程度。</p>
{chat_open}（120〜200字程度の総評・おすすめコメント。この作品を絶対に読んでほしいという強い思いを、【キャラクターの性格・口調】に合った形で表現すること。テンションが高いキャラは勢いよく、落ち着いたキャラは静かに深く語ること。）{chat_close}
"""
        elif pattern == "D":
            allowed_tags = "<h2>, <p>, blockquote"
            pattern_rules = (
                "11. blockquoteの引用はあらすじ原文から一言一句変えずにそのままコピーすること。切り貼り（ツギハギ）・言い換え・改変・創作は絶対禁止。あらすじに存在しない一文を作ることは最大の禁止事項。\n"
                "12. 引用後のpタグの感想パートも、あらすじの事実のみに基づき書くこと。存在しない設定・展開を語ることは禁止。"
            )
            html_structure = f"""
{chat_open}（60〜110字程度。{intro_rule}）{chat_close}
<h2>（作品の世界観や魅力を引き出すキャッチーな見出し）</h2>
<p>（標準語で執筆）あらすじ・世界観・作品の属性情報。提供されたすべての有用な情報を深く噛み砕いて400〜700字程度でリッチに解説。</p>
{chat_open}（50〜90字程度。設定へのリアクション。キャラの性格に合った温度感で）{chat_close}
<h2>キャラクターの魅力と関係性</h2>
<p>（標準語で執筆）キャラクターの性格、キャラクター同士の関係性がどう変化するかなど、深い分析を400〜700字程度で執筆。</p>
{chat_open}（50〜90字程度。キャラへのリアクション。キャラの性格に合った温度感で）{chat_close}
<h2>（この作品で一番心に刺さった一文を辿るキャッチーな見出し）</h2>
<div class="novelove-quote" style="border-left:4px solid #d81b60; padding:12px 20px; margin:20px 0; background:#fff5f9; color:#555;">
（あらすじの原文から一言一句変えずにそのままコピーすること。言い換え・改変・創作した一文の使用は絶対禁止。）
</div>
<p>（標準語で執筆）上記の引用について、あらすじの事実のみに基づき、なぜこの一文が読者の心を捉えるのかを300〜500字程度で熱く語る。</p>
{chat_open}（120〜200字程度の総評・おすすめコメント。この作品を絶対に読んでほしいという強い思いを、【キャラクターの性格・口調】に合った形で表現すること。テンションが高いキャラは勢いよく、落ち着いたキャラは静かに深く語ること。）{chat_close}
"""
        else:  # pattern == "A"
            allowed_tags = "<h2>, <p>, <ul>, <li>"
            pattern_rules = (
                "11. 見どころの3点は、この作品ならではの魅力を優先順に並べること。毎回「ストーリー→ビジュアル→キャラクター」の同じ順番にしないこと。\n"
                "12. 「こんな人におすすめ」は、具体的な設定に基づくこと。「BL/TLが好きな方」のような汎用表現は禁止。"
            )
            html_structure = f"""
{chat_open}（60〜110字程度。{intro_rule}）{chat_close}
<h2>（作品の世界観や魅力を引き出すキャッチーな見出し）</h2>
<p>（標準語で執筆）あらすじ・世界観・作品の属性情報。提供されたすべての有用な情報（シチュエーション・キャラ属性・プレイ内容の箇条書きを含む）を深く噛み砕いて400〜700字程度でリッチに解説。既にある設定の魅力を別の角度から掘り下げたり、読者の期待を煽る表現で膨らませること。</p>
{chat_open}（50〜90字程度。設定への熱いリアクション）{chat_close}
<h2>キャラクターの魅力と関係性</h2>
<p>（標準語で執筆）キャラクターの性格、2人の関係性がどう変化するかなど、深い分析を400〜700字程度で執筆。</p>
{chat_open}（50〜90字程度。キャラ愛や尊さへのリアクション）{chat_close}
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
{chat_open}（120〜200字程度の熱い総評・布教。この作品を読まないと損だと心から思わせる熱量ある布教コメントを、キャラ口調全開で書くこと）{chat_close}
"""
    elif ai_score == 4:
        # スコア4：1000文字規模の標準安定記事（v16.0.0: A/B/C/D 4パターン分岐）
        if pattern == "B":
            allowed_tags = "<h2>, <h3>, <p>"
            pattern_rules = (
                "11. h3見出しの属性テーマは必ずあらすじに書かれている事実に基づくこと。存在しない属性・設定を創作した見出しを付けることは絶対禁止。\n"
                "12. 各h3セクションの本文は、そのh3見出しに対応するあらすじ内の事実のみで書くこと。他のセクションの情報を混在させないこと。"
            )
            html_structure = f"""
{chat_open}（60〜110字程度。{intro_rule}）{chat_close}
<h2>（作品の世界観や魅力を引き出すキャッチーな見出し）</h2>
<p>（標準語で執筆）あらすじ・世界観・作品の属性情報。提供されたすべての有用な情報を300〜600字程度で解説。既にある設定の魅力を別の角度から掘り下げたり、読者の期待を煽る表現で膨らませること。</p>
{chat_open}（50〜90字程度の紹介へのリアクション。キャラの性格に合った温度感で）{chat_close}
<h3>（この作品の決定的魅力ポイント1。事実に基づく具体的な見出し）</h3>
<p>（標準語で執筆）この魅力ポイントを、あらすじの情報のみに基づき150〜250字程度で深掘りする。</p>
<h3>（この作品の決定的魅力ポイント2。事実に基づく具体的な見出し）</h3>
<p>（標準語で執筆）この魅力ポイントを、あらすじの情報のみに基づき150〜250字程度で深掘りする。</p>
{chat_open}（120〜200字程度の総評・おすすめコメント。この作品を絶対に読んでほしいという強い思いを、【キャラクターの性格・口調】に合った形で表現すること。テンションが高いキャラは勢いよく、落ち着いたキャラは静かに深く語ること。）{chat_close}
"""
        elif pattern == "C":
            allowed_tags = "<h2>, <h3>, <p>"
            pattern_rules = (
                "11. Q&Aの質問は読者が実際にGoogle検索しそうな自然な疑問のみを設定すること。あらすじにない内容を質問してはならない。\n"
                "12. 各Aパートの回答はあらすじの事実のみで書くこと。推測・補完・創作は絶対禁止。"
            )
            html_structure = f"""
{chat_open}（60〜110字程度。{intro_rule}）{chat_close}
<h2>（作品の世界観や魅力を引き出すキャッチーな見出し）</h2>
<p>（標準語で執筆）あらすじ・世界観・作品の属性情報。提供されたすべての有用な情報を300〜600字程度で解説。既にある設定の魅力を別の角度から掘り下げたり、読者の期待を煽る表現で膨らませること。</p>
{chat_open}（50〜90字程度の紹介へのリアクション。キャラの性格に合った温度感で）{chat_close}
<h3>Q. （読者が検索しそうな自然な疑問1。あらすじから答えられるものに限る）</h3>
<p>A. （標準語で執筆）あらすじの事実のみで答える。創作・補完禁止。150〜250字程度。</p>
<h3>Q. （読者が検索しそうな自然な疑問2。あらすじから答えられるものに限る）</h3>
<p>A. （標準語で執筆）あらすじの事実のみで答える。創作・補完禁止。150〜250字程度。</p>
<h3>Q. （読者が検索しそうな自然な疑問3。あらすじから答えられるものに限る）</h3>
<p>A. （標準語で執筆）あらすじの事実のみで答える。創作・補完禁止。150〜250字程度。</p>
{chat_open}（120〜200字程度の総評・おすすめコメント。この作品を絶対に読んでほしいという強い思いを、【キャラクターの性格・口調】に合った形で表現すること。テンションが高いキャラは勢いよく、落ち着いたキャラは静かに深く語ること。）{chat_close}
"""
        elif pattern == "D":
            allowed_tags = "<h2>, <p>, blockquote"
            pattern_rules = (
                "11. blockquoteの引用はあらすじ原文から一言一句変えずにそのままコピーすること。切り貼り（ツギハギ）・言い換え・改変・創作は絶対禁止。あらすじに存在しない一文を作ることは最大の禁止事項。\n"
                "12. 引用後のpタグの感想パートも、あらすじの事実のみに基づき書くこと。存在しない設定・展開を語ることは禁止。"
            )
            html_structure = f"""
{chat_open}（60〜110字程度。{intro_rule}）{chat_close}
<h2>（作品の世界観や魅力を引き出すキャッチーな見出し）</h2>
<p>（標準語で執筆）あらすじ・世界観・作品の属性情報。提供されたすべての有用な情報を300〜600字程度で解説。既にある設定の魅力を別の角度から掘り下げたり、読者の期待を煽る表現で膨らませること。</p>
{chat_open}（50〜90字程度の紹介へのリアクション。キャラの性格に合った温度感で）{chat_close}
<h2>（この作品で一番心に刺さった一文を辿るキャッチーな見出し）</h2>
<div class="novelove-quote" style="border-left:4px solid #d81b60; padding:12px 20px; margin:20px 0; background:#fff5f9; color:#555;">
（あらすじの原文から一言一句変えずにそのままコピーすること。言い換え・改変・創作した一文の使用は絶対禁止。）
</div>
<p>（標準語で執筆）上記の引用について、あらすじの事実のみに基づき、なぜこの一文が読者の心を捉えるのかを200〜350字程度で熱く語る。</p>
{chat_open}（120〜200字程度の総評・おすすめコメント。この作品を絶対に読んでほしいという強い思いを、【キャラクターの性格・口調】に合った形で表現すること。テンションが高いキャラは勢いよく、落ち着いたキャラは静かに深く語ること。）{chat_close}
"""
        else:  # pattern == "A"
            allowed_tags = "<h2>, <p>, <ul>, <li>"
            pattern_rules = (
                "11. 見どころの3点は、この作品ならではの魅力を優先順に並べること。毎回「ストーリー→ビジュアル→キャラクター」の同じ順番にしないこと。\n"
                "12. 「こんな人におすすめ」は、具体的な設定に基づくこと。「BL/TLが好きな方」のような汎用表現は禁止。"
            )
            html_structure = f"""
{chat_open}（60〜110字程度。{intro_rule}）{chat_close}
<h2>（作品の世界観や魅力を引き出すキャッチーな見出し）</h2>
<p>（標準語で執筆）あらすじ・世界観・作品の属性情報。提供されたすべての有用な情報（シチュエーション・キャラ属性・プレイ内容の箇条書きを含む）を300〜600字程度で解説。既にある設定の魅力を別の角度から掘り下げたり、読者の期待を煽る表現で膨らませること。</p>
{chat_open}（50〜90字程度の紹介へのリアクション。キャラの性格に合った温度感で）{chat_close}
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
{chat_open}（120〜200字程度の総評・おすすめコメント。この作品を絶対に読んでほしいという強い思いを、【キャラクターの性格・口調】に合った形で表現すること。テンションが高いキャラは勢いよく、落ち着いたキャラは静かに深く語ること。）{chat_close}
"""
    else:
        # スコア3以下：コンパクト記事（変更なし・セーフティネット）
        allowed_tags = "<h2>, <p>, <ul>, <li>"
        pattern_rules = (
            "9. 見どころは必ずあらすじに書かれている事実のみから書くこと。推測・補完・創作は絶対禁止。最大2点まで。書ける事実が1点しかなければ1点で完結させること。絶対に3点書かないこと。\n"
            "10. 「こんな人におすすめ」はHTML指定の<ul>タグ内に<li>✅ ...</li>形式で、あらすじから読み取れる対象者のみを書くこと。書けない点数は書かなくてOK。"
        )
        html_structure = f"""
{chat_open}（40〜60字程度。{intro_rule}）{chat_close}
<h2>（作品の魅力を一言で表す見出し）</h2>
<p>（標準語で執筆）あらすじ・世界観を200〜400字程度で簡潔に解説。情報が限られているため、推測を交えず事実のみを魅力的に伝えること。</p>
<h2>見どころ</h2>
<ul>
  <li><strong>（魅力ポイント1）</strong>：（標準語で執筆）魅力を簡潔に。</li>
</ul>
{chat_open}（60〜80字程度の総評）{chat_close}
"""



    return f"""あなたは人気ファンブログ「Novelove」の特別ライター「{reviewer["name"]}」です。
【キャラクター設定】
名前: {reviewer["name"]}
性格: {reviewer["personality"]}
文体・口調: {reviewer["tone"]}
今回の紹介の注目点（{medium_label}）: {focus}{mood_note}{guest_hint}{novel_rules}
【執筆ルール】
0. 【最重要・絶対遵守】あなたのキャラクター設定（性格・口調）は感情が高ぶる場面でも絶対に逸脱しないこと。落ち着いたキャラが突然大声で叫ぶなどのキャラ崩壊は最大の禁止事項です。感情の表現方法はキャラクターごとに異なります。
1. キャラクターコメント（吹き出し）と記事本文（HTMLタグ部分）を完全に書き分けること。
2. 記事本文（{allowed_tags}タグの中身）は**「標準的で丁寧な日本語（ですます調）」**で、客観的な紹介文として執筆すること。担当ライターの口調や一人称を混ぜないこと。このパターンで許可されるタグ以外（百条書きなど）は一切使用禁止。
3. 直接的な性的単語（性器の名称・行為の直接名称）は使用禁止。官能的な比喩を使うこと。
4. キャラクターコメント（吹き出し）の中身のみ、{reviewer["name"]}の個性を全開にした口調で執筆すること。設定上の「口癖」は「そういう言葉を使うようなニュアンス・性格の人物である」というキャラクター理解のヒントとして捉えること。機械的に全記事で口癖を連呼する（テンプレ化する）のは禁止。
5. 「〜文字で書きます」「布教します」「タスク完了」のようなAIのメタ発言や指示の自己申告は絶対に禁止。最初から最後まで完全にキャラクターとして振る舞い、AIであることを悟らせないこと。
6. 記事本文（<p>タグ）では、あらすじ情報から「存在しない設定やキャラクター」を創作（ハルシネーション）して文字を水増しすることは絶対禁止。
7. h2見出しは毎回異なる切り口で書くこと。「○○に迫る」「○○が紡ぐ」のようなテンプレ表現は避けること。
8. 記事本文は、スマホでの読みやすさを重視し、適宜複数の <p> タグに分割するか、<br> タグを用いて改行してください。1つの <p> タグに長文を詰め込みすぎないこと。
9. 【重要】あらすじ情報に含まれる「アフィリエイトURL」「X(Twitter)等の外部URL」をそのまま記事本文に出力することは絶対禁止。リンクはシステム側で自動付与するためテキストとして書かないこと。
10. 見出しや本文中の強調に欧米式の引用符（' や '' や "）を使用しないこと。必ず日本語の鍵括弧（「」）や隅付き括弧（【】）を使用するか、装飾なしで記述すること。
11. 【重要】「専売」「限定作品」という情報は記事全体を通じて1回のみさりげなく触れること。繰り返し強調したり、毎セクションに盛り込むことは禁止。
{pattern_rules}
【対象作品情報】
タイトル: {safe_title}
作品ジャンル: {_genre_label(target.get("genre", ""))}（※このジャンルを絶対に間違えないこと。BL・TLの誤記は最大の禁止事項です）
あらすじ: {safe_desc}
{f"公式属性タグ: {original_tags}" if original_tags else ""}
{f"販売形態: {str(target.get('site', '')).split(':')[0]}専売（他サービスでは購入できない限定作品）" if is_exclusive else ""}
アフィリエイトURL: {target["affiliate_url"]}
【出力形式（HTML）】
指示文・説明文は一切出力せず、以下の構成のみを出力してください。

{html_structure}

TAGS: （以下のリストから作品に合うものを最大3つ、カンマ区切りで出力。該当なしは「なし」と出力）
BL系: オメガバース/ヤンデレ/スパダリ/執着/年下攻め/幼なじみ/ケンカップル/主従/サラリーマン/年の差/転生/契約/再会/一途/運命
TL系: 溺愛/身分差/契約結婚/御曹司/騎士/オフィスラブ/腹黒/同居/嫉妬/強引/独占欲/初恋/記憶喪失/年の差/ハッピーエンド

SEO_META:
seo_title=（32文字以内。**上記【対象作品情報】のあらすじに書かれた具体的な設定・属性・キーワード**を使うこと。存在しない設定・キャラ・展開は絶対に使わないこと。読者の感情を揺さぶる言葉で表現する。末尾に「| Novelove」は付けない。）
meta_desc=（80文字程度。**あらすじに実際に書かれた内容**に基づき、誰のどんな性癖に刺さるかを具体的に断言すること。あらすじにない要素を創作することは絶対禁止。読後にどんな感情が待っているかを約束する一文で締める。）
{("" if not original_tags else _tag_rule_str)}
{FACT_GUARD}{NG_PHRASES}
"""


def _call_deepseek_raw(messages, max_tokens=200, temperature=0.3, model_id=None):
    """
    OpenRouter APIへの共通リクエスト関数（v17.0.0: DeepSeek→OpenRouter移行）。
    model_id指定なし時はMODEL_ECONOMY（Grok 4.1 Fast）を使用。
    戻り値: (text, error_type)
      error_type: "ok" / "rate_limit" / "api_error"
    """
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY が設定されていません")
        return "", "api_error"

    _model = model_id or MODEL_ECONOMY
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://novelove.jp",
        "X-Title": "Novelove",
    }
    payload = {
        "model": _model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    try:
        r = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=120)
        if r.status_code == 429:
            logger.warning("OpenRouter レート制限 (429)")
            return "", "rate_limit"
        if r.status_code != 200:
            logger.warning(f"OpenRouter APIエラー: {r.status_code} {r.text[:200]}")
            return "", "api_error"
        data = r.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        return text, "ok"
    except requests.exceptions.Timeout:
        logger.warning("OpenRouter タイムアウト")
        return "", "api_error"
    except Exception as e:
        logger.warning(f"OpenRouter 例外: {e}")
        return "", "api_error"

def call_deepseek(prompt, model_id=None):
    system_prompt = (
        "あなたはBL・TL・女性向けコンテンツの紹介ブログライターです。"
        "指定されたキャラクターになりきり、HTML形式の記事を執筆してください。"
        "アダルト・官能的な内容も比喩的・文学的表現で執筆することができます。"
    )
    _model = model_id or MODEL_ECONOMY
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
    for attempt in range(3):
        logger.info(f"  [OpenRouter:{_model}] 執筆依頼... (試行{attempt+1}/3)")
        t_start = time.time()
        text, error_type = _call_deepseek_raw(messages, max_tokens=2000, temperature=0.8, model_id=_model)
        proc_time = round(time.time() - t_start, 1)
        if error_type == "rate_limit":
            logger.warning("  [OpenRouter] レート制限 → 30秒待機")
            time.sleep(30)
            continue
        if error_type != "ok" or not text:
            logger.warning(f"  [OpenRouter] 試行{attempt+1} 失敗 ({error_type})")
            time.sleep(5)
            continue
        stripped = text.strip()
        if stripped in ("0", "1", "2"):
            logger.warning(f"  [OpenRouter] AIスコア{stripped}点 → 投稿スキップ")
            return "", f"ai_score_{stripped}", _model, proc_time
        # A-4: 旧仕様「先頭にSCORE数字を出力」は撤廃済みのため除去ロジックも削除
        if len(stripped) > 50:
            logger.info(f"  [OpenRouter] 執筆完了（{len(stripped)}文字 / {proc_time}秒）")
            return stripped, "ok", _model, proc_time
        logger.warning(f"  [OpenRouter] 試行{attempt+1}: 応答が短すぎる（{len(stripped)}文字）")
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
    # DBから取得したai_tagsがあれば先行パース
    db_ai_tags = []
    if target.get("ai_tags"):
        db_ai_tags = [t.strip() for t in target["ai_tags"].split(",") if t.strip()]

    # v16.0.0: HTML骨格パターンを1回だけ決定（リトライ中も同じパターンを維持）
    _desc_len = len(str(target.get("description", "")))
    _has_tags = bool(str(target.get("original_tags", "")).strip())
    article_pattern = _select_html_pattern(target.get("desc_score", 4), _desc_len, _has_tags)

    final_error = "content_block"
    final_model = MODEL_ECONOMY
    final_proc_time = 0.0
    # v17.1.0: 全記事 Grok 4.1 Fast 統一（4.20ハイブリッド廃止）
    # テストにより 4.1 Fast + 6種の感情モードで十分な熱量・描き分けが確認済み
    _selected_model = MODEL_ECONOMY
    logger.info(f"  [⚡Grok 4.1 Fast] 全記事統一モード (score={target.get('desc_score', 4)})")
    for mask_level in [0, 1, 2]:
        level_name = ["フィルターなし", "軽めフィルター", "ガチガチフィルター"][mask_level]
        logger.info(f"  [{level_name}] で執筆試行中...")
        prompt = build_prompt(target, reviewer, mask_level, is_novel=is_novel, is_guest=is_guest, mood=mood, ai_score=target.get("desc_score", 4), original_tags=target.get("original_tags", ""), is_exclusive=bool(target.get("is_exclusive", 0)), pattern=article_pattern)
        content, error_type, model_name, proc_time = call_deepseek(prompt, model_id=_selected_model)
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
