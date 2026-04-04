#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================
nexus_dashboard.py — Nexus Dashboard (Phase 2 / Step 1)
==========================================================
【役割】
  3つのSQLite DBを統合し、記事データを一画面で閲覧・検索・
  フィルタリングできる「完全Read-Only」のコントロールパネル。
  この画面からはデータの変更・削除は一切できません。

【起動方法】
  streamlit run nexus_dashboard.py
==========================================================
"""

import os
import sqlite3
import pandas as pd
import streamlit as st

# =====================================================================
# 0. 設定
# =====================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DB_SOURCES = {
    "FANZA":   os.path.join(SCRIPT_DIR, "novelove.db"),
    "DLsite":  os.path.join(SCRIPT_DIR, "novelove_dlsite.db"),
    "DigiKet": os.path.join(SCRIPT_DIR, "novelove_digiket.db"),
}

COLUMNS_TO_LOAD = [
    "product_id",
    "title",
    "author",
    "genre",
    "site",
    "status",
    "desc_score",
    "ai_tags",
    "reviewer",
    "sale_discount_rate",
    "last_revived_at",
    "inserted_at",
    "published_at",
    "last_error",
    "wp_post_url",
    "image_url",
    "release_date",
    "post_type",
    "product_url",
    "affiliate_url",
    "is_desc_updated",
    "description",
    "prev_description",
    "rewrite_count",
    # === GSC (S5) ===
    "gsc_indexed",
    "gsc_impressions",
    "gsc_clicks",
    "gsc_last_checked",
]

STATUS_MAP = {
    "published": "🟢 公開済",
    "pending":   "🟡 執筆待",
    "excluded":  "🔴 除外済",
    "failed":    "❌ 取得エラー",
    "failed_ai": "❌ AIエラー",
}

# =====================================================================
# 1. データ取得（Read-Only / SELECT のみ）
# =====================================================================
@st.cache_data(ttl=60)
def load_all_data() -> pd.DataFrame:
    """3つのDBを統合してDataFrameを返す。60秒キャッシュ。"""
    frames = []
    for label, db_path in DB_SOURCES.items():
        if not os.path.exists(db_path):
            continue
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            # 存在するカラムだけ取得（DBごとにカラムが異なる可能性を考慮）
            existing_cols = [
                row[1]
                for row in conn.execute("PRAGMA table_info(novelove_posts)").fetchall()
            ]
            cols_to_query = [c for c in COLUMNS_TO_LOAD if c in existing_cols]
            if not cols_to_query:
                conn.close()
                continue
            query = f"SELECT {', '.join(cols_to_query)} FROM novelove_posts"
            df = pd.read_sql_query(query, conn)
            
            # novelove.db(FANZA/DMM統合DB)の場合は、siteカラムの中身を見て表示を切り替える
            if label == "FANZA" and "site" in df.columns:
                df["_source_db"] = df["site"].apply(lambda x: "DMM" if x and "DMM" in str(x) else "FANZA")
            else:
                df["_source_db"] = label
            
            conn.close()
            frames.append(df)
        except Exception as e:
            st.warning(f"⚠️ {label} の読み込みに失敗しました: {e}")

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False)

    # 型の整理
    for col in ["desc_score", "sale_discount_rate", "is_desc_updated", "rewrite_count",
                 "gsc_indexed", "gsc_impressions", "gsc_clicks"]:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors="coerce").fillna(0).astype(int)
    for col in ["inserted_at", "published_at", "last_revived_at", "gsc_last_checked"]:
        if col in combined.columns:
            combined[col] = pd.to_datetime(combined[col], errors="coerce")

    return combined


# =====================================================================
# 2. UI ヘルパー
# =====================================================================
def safe_str(val, default="-"):
    return str(val) if pd.notna(val) and str(val).strip() else default

def status_badge(status: str) -> str:
    return STATUS_MAP.get(str(status), f"⚪ {status}")


def format_display_df(df: pd.DataFrame) -> pd.DataFrame:
    """表示用にカラムを整形する。"""
    display = df.copy()

    # サムネイル列（ImageColumn用にそのまま渡す）
    if "image_url" in display.columns:
        display["サムネイル"] = display["image_url"].fillna("")

    # ステータスにアイコンを付与
    if "status" in display.columns:
        display["ステータス"] = display["status"].apply(status_badge)

    # 日付を見やすく（日付だけ表示、時刻は省略してスリムに）
    for col, label in [("published_at", "公開日"), ("inserted_at", "取得日"), ("release_date", "発売日")]:
        if col in display.columns:
            # DBによって日時フォーマット（時分秒の有無）が異なるため、強制的に最初の10文字(YYYY-MM-DD)だけを抽出してパース
            safe_dates = display[col].astype(str).str[:10]
            display[label] = pd.to_datetime(safe_dates, errors="coerce").dt.strftime("%Y/%m/%d").fillna("-")

    # 記事種別（post_type）を日本語化
    if "post_type" in display.columns:
        display["記事種別"] = display["post_type"].map({"regular": "通常", "ranking": "ランキング"}).fillna(display["post_type"])

    # セール中表示
    if "sale_discount_rate" in display.columns:
        display["セール"] = display["sale_discount_rate"].apply(
            lambda v: f"🔥{v}%" if v > 0 else "-"
        )

    # リンク列の整理
    if "wp_post_url" in display.columns:
        display["ノベラブ"] = display["wp_post_url"]
    
    if "affiliate_url" in display.columns and "product_url" in display.columns:
        display["販売元"] = display["affiliate_url"].fillna(display["product_url"])

    # スコアをバー表示用に整形
    if "desc_score" in display.columns:
        display["スコア"] = display["desc_score"]

    # タグを短く整形（カンマ区切りを改行なしで短縮表示）
    if "ai_tags" in display.columns:
        def shorten_tags(t):
            if not t or str(t) in ("", "None", "nan"):
                return "-"
            tags = [x.strip() for x in str(t).split(",") if x.strip()]
            return " ".join([f"#{tag}" for tag in tags[:3]])  # 最大3つ
        display["タグ"] = display["ai_tags"].apply(shorten_tags)

    # あらすじ更新バッジ
    if "is_desc_updated" in display.columns:
        display["📝"] = display["is_desc_updated"].apply(
            lambda v: "updated" if (pd.notna(v) and int(v) == 1) else "-"
        )

    # 表示カラムを整理
    rename_map = {
        "product_id":  "作品ID",
        "title":       "タイトル",
        "author":      "作者",
        "genre":       "ジャンル",
        "_source_db":  "DB",
        "reviewer":    "担当",
        "last_error":  "エラー",
    }
    display = display.rename(columns={k: v for k, v in rename_map.items() if k in display.columns})

    return display


# =====================================================================
# 3. Streamlit メイン画面
# =====================================================================
# =====================================================================
# 3. Streamlit メイン画面
# =====================================================================

# =====================================================================
# 詳細ビュー＆リライト操作パネル (共通)
# =====================================================================
def render_detail_panel(selected_pid_str, df, key_prefix="list"):
    detail_pid = st.text_input(
            "📝 作品ID（手動入力も可）", 
            value=selected_pid_str, 
            key=f"{key_prefix}_detail_pid_input"
        )

    if detail_pid:
        match = df[df["product_id"].str.lower() == detail_pid.lower()]
        if not match.empty:
            row = match.iloc[0]

            # ── 上段: 記事情報 ──
            c1, c2 = st.columns([1, 2])
            with c1:
                img_url = safe_str(row.get("image_url"), "")
                if img_url:
                    st.image(img_url, width=200, caption=safe_str(row.get("title"), ""))
            with c2:
                st.markdown(f"**タイトル**: {safe_str(row.get('title'))}")
                st.markdown(f"**作品ID**: `{safe_str(row.get('product_id'))}`")
                st.markdown(f"**ステータス**: {status_badge(row.get('status'))}")
                st.markdown(f"**あらすじスコア**: {safe_str(row.get('desc_score'))}")
                st.markdown(f"**担当ライター**: {safe_str(row.get('reviewer'))}")
                st.markdown(f"**タグ**: {safe_str(row.get('ai_tags'))}")
                wp_url = safe_str(row.get("wp_post_url"), "")
                if wp_url:
                    st.markdown(f"**WP記事**: [{wp_url}]({wp_url})")
                if pd.notna(row.get("sale_discount_rate")) and int(row.get("sale_discount_rate", 0)) > 0:
                    st.success(f"🔥 現在 {int(row['sale_discount_rate'])}% セール中！")

            # ── あらすじ差分ビュー（更新検知時のみ表示） ──
            if pd.notna(row.get("is_desc_updated")) and int(row.get("is_desc_updated")) == 1:
                st.markdown("---")
                prev_raw = row.get("prev_description")
                new_raw  = row.get("description")
                prev_desc = str(prev_raw) if pd.notna(prev_raw) else ""
                new_desc  = str(new_raw) if pd.notna(new_raw) else ""
                st.warning("📝 **あらすじが更新されました！** リライトを検討してください。")
                col_prev, col_new = st.columns(2)
                with col_prev:
                    st.caption(f"旧あらすじ（{len(prev_desc)}文字）")
                    st.text_area("旧あらすじ", value=prev_desc, height=200, disabled=True,
                                 key=f"prev_desc_view_{row['product_id']}", label_visibility="collapsed")
                with col_new:
                    st.caption(f"新あらすじ（{len(new_desc)}文字）")
                    st.text_area("新あらすじ", value=new_desc, height=200, disabled=True,
                                 key=f"new_desc_view_{row['product_id']}", label_visibility="collapsed")

                # 変化量の表示
                import difflib
                ratio = difflib.SequenceMatcher(None, prev_desc, new_desc).ratio()
                char_diff = len(new_desc) - len(prev_desc)
                diff_sign = "+" if char_diff >= 0 else ""
                st.caption(f"内容一致率: {ratio:.1%} | 文字数差: {diff_sign}{char_diff}文字")

                if st.button("✅ 確認済み（フラグをリセット）", key=f"{key_prefix}_btn_confirm_desc"):
                    try:
                        from novelove_core import get_db_path, db_connect as _dbc
                        _db = _dbc(get_db_path(row.get("site", "")))
                        _db.execute(
                            "UPDATE novelove_posts SET is_desc_updated = 0 WHERE product_id = ?",
                            (row["product_id"],)
                        )
                        _db.commit()
                        _db.close()
                        st.success("フラグをリセットしました。データを再読み込みしてください。")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"❌ リセット失敗: {e}")

            # ── 下段: リライトパネル（published のみ表示） ──
            if row.get("status") == "published":
                st.markdown("---")
                st.markdown("##### 🔄 リライト設定")

                REVIEWER_OPTIONS = {
                    "ランダム（ジャンル対応）": None,
                    "紫苑 (shion) — クールなBL腐女子OL":   "shion",
                    "葵 (aoi) — 限界BLオタク大学生":         "aoi",
                    "蓮 (ren) — インテリBL大学院生":          "ren",
                    "茉莉花 (marika) — ポップなTLカフェ店員": "marika",
                    "桃香 (momoka) — 大人なTL主婦":           "momoka",
                }
                MOOD_OPTIONS = {
                    "ランダム": None,
                    "熱量高め（心をつかまれた）":         "今回は特にこの作品に心をつかまれている。いつもより熱量が高い。",
                    "冷静分析（本音が漏れる）":           "今回は冷静に分析しつつも、総評で本音が漏れてしまう。",
                    "布教欲（名作を発掘した興奮）":       "今回は「まだ知られていない名作」を発掘した興奮がある。布教欲が強い。",
                    "じわじわ感動（噛みしめる）":         "今回はじわじわと効いてくるタイプの感動を受けている。噛みしめるように語る。",
                    "衝撃（勢いのまま語る）":             "今回は開始数ページで心を持っていかれた衝撃がある。勢いのまま語る。",
                    "運命的な出会い":                     "今回は「こういう作品を待っていた」という運命的な出会いを感じている。",
                }

                rc1, rc2 = st.columns(2)
                with rc1:
                    selected_reviewer_label = st.selectbox(
                        "担当ライター",
                        options=list(REVIEWER_OPTIONS.keys()),
                        key=f"{key_prefix}_sel_reviewer",
                    )
                with rc2:
                    selected_mood_label = st.selectbox(
                        "感情モード",
                        options=list(MOOD_OPTIONS.keys()),
                        key=f"{key_prefix}_sel_mood",
                    )

                reviewer_id = REVIEWER_OPTIONS[selected_reviewer_label]
                mood_str    = MOOD_OPTIONS[selected_mood_label]

                # ── DRY-RUN ボタン ──
                st.info(
                    "💡 **まず DRY-RUN で確認** → 問題なければサーバーで `--execute` を付けて実行してください。\n\n"
                    "DRY-RUN は WP・DB に一切書き込みません。生成内容の確認のみです。"
                )

                if st.button("🧪 DRY-RUN で内容を確認", key=f"{key_prefix}_btn_dryrun"):
                    st.session_state[f"{key_prefix}_rw_pid"]      = str(row["product_id"])
                    st.session_state[f"{key_prefix}_rw_reviewer"] = reviewer_id
                    st.session_state[f"{key_prefix}_rw_mood"]     = mood_str
                    st.session_state[f"{key_prefix}_rw_phase"]    = "running_dryrun"

                # ── DRY-RUN 実行 ──
                if st.session_state.get(f"{key_prefix}_rw_phase") == "running_dryrun" and \
                   st.session_state.get(f"{key_prefix}_rw_pid") == str(row["product_id"]):

                    with st.spinner("🤖 AI執筆中（DRY-RUN）..."):
                        try:
                            from nexus_rewrite import run_rewrite
                            import io, logging

                            # loggerの出力をキャプチャするハンドラを一時追加
                            log_buffer = io.StringIO()
                            _capture_handler = logging.StreamHandler(log_buffer)
                            _capture_handler.setFormatter(logging.Formatter("%(message)s"))
                            _nv_logger = logging.getLogger("novelove")
                            _nv_logger.addHandler(_capture_handler)
                            try:
                                success = run_rewrite(
                                    product_id=st.session_state[f"{key_prefix}_rw_pid"],
                                    reviewer_id=st.session_state[f"{key_prefix}_rw_reviewer"],
                                    mood=st.session_state[f"{key_prefix}_rw_mood"],
                                    execute=False,
                                )
                            finally:
                                _nv_logger.removeHandler(_capture_handler)

                            st.session_state[f"{key_prefix}_rw_log"] = log_buffer.getvalue()
                            st.session_state[f"{key_prefix}_rw_dryrun_success"] = success
                            st.session_state[f"{key_prefix}_rw_phase"] = "dryrun_done"
                        except Exception as e:
                            st.error(f"❌ DRY-RUN 実行中にエラーが発生しました: {e}")
                            st.session_state[f"{key_prefix}_rw_phase"] = None

                # ── DRY-RUN 結果表示 ──
                if st.session_state.get(f"{key_prefix}_rw_phase") == "dryrun_done" and \
                   st.session_state.get(f"{key_prefix}_rw_pid") == str(row["product_id"]):

                    if st.session_state.get(f"{key_prefix}_rw_dryrun_success"):
                        st.success("✅ DRY-RUN 完了！")
                        # キャプチャしたログを表示
                        captured_log = st.session_state.get(f"{key_prefix}_rw_log", "")
                        if captured_log:
                            with st.expander("📋 実行ログ（クリックで展開）", expanded=True):
                                st.text(captured_log)
                        st.markdown("---")
                        st.warning("⚠️ **本番環境の WordPress 記事 と データベース が実際に書き換わります。** ログに問題がなければ以下のボタンで実行してください。")

                        if st.button("🚀 この内容で本番環境に上書き保存する！", type="primary"):
                            with st.spinner("本番環境への書き込みを実行しています..."):
                                try:
                                    import io
                                    import logging
                                    from novelove_core import logger as _nv_logger_exec
                                    from nexus_rewrite import run_rewrite as exec_run_rewrite

                                    log_buffer_exec = io.StringIO()
                                    _capture_handler_exec = logging.StreamHandler(log_buffer_exec)
                                    _capture_handler_exec.setLevel(logging.INFO)
                                    _capture_handler_exec.setFormatter(logging.Formatter('%(message)s'))
                                    _nv_logger_exec.addHandler(_capture_handler_exec)

                                    try:
                                        exec_success = exec_run_rewrite(
                                            product_id=st.session_state[f"{key_prefix}_rw_pid"],
                                            reviewer_id=st.session_state[f"{key_prefix}_rw_reviewer"],
                                            mood=st.session_state[f"{key_prefix}_rw_mood"],
                                            execute=True,
                                        )
                                    finally:
                                        _nv_logger_exec.removeHandler(_capture_handler_exec)

                                    if exec_success:
                                        st.success("🎉 **本番実行が完了しました！WordPressが正常に更新されました！**")
                                        st.balloons()
                                        st.info("ℹ️ データを再読み込みすると、最新の状態がデータフレームに反映されます。")
                                    else:
                                        st.error("❌ 本番実行でエラーが発生しました。ログを確認してください。")
                                        with st.expander("📝 エラー詳細", expanded=True):
                                            st.text(log_buffer_exec.getvalue())
                                except Exception as e:
                                    st.error(f"❌ 深刻なエラーが発生しました: {e}")

                        if st.button("🔄 別の設定で再度 DRY-RUN", key=f"{key_prefix}_btn_reset_dryrun"):
                            st.session_state[f"{key_prefix}_rw_phase"] = None
                            st.rerun()
                    else:
                        st.error("❌ DRY-RUN が失敗しました。ログを確認してください。")
                        captured_log = st.session_state.get(f"{key_prefix}_rw_log", "")
                        if captured_log:
                            with st.expander("📋 実行ログ", expanded=True):
                                st.text(captured_log)
                        if st.button("🔄 やり直す", key=f"{key_prefix}_btn_retry"):
                            st.session_state[f"{key_prefix}_rw_phase"] = None
                            st.rerun()
            else:
                st.caption(f"⚠️ この記事はステータスが `{row.get('status', '-')}` のためリライトできません（published のみ対象）")
        else:
            st.warning("該当する作品IDが見つかりませんでした。")


def main():
    st.set_page_config(
        page_title="Nexus Dashboard",
        page_icon="🌌",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # ─── Vercel/Linear風 ダーク・グラスモーフィズム CSS ───
    st.markdown(
        """
        <style>
        /* デフォルトUIの非表示化 */
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}

        /* 背景とフォント（全体） */
        .stApp {
            background-color: #0f172a;
            color: #f8fafc;
        }

        /* ヘッダー・コックピットデザイン */
        .premium-header {
            background: rgba(30, 41, 59, 0.7);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.05);
            padding: 24px 32px; 
            border-radius: 16px; 
            margin-bottom: 24px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }
        
        .premium-header h1 {
            background: linear-gradient(to right, #ec4899, #8b5cf6, #06b6d4);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin: 0; 
            font-size: 2.2em;
            font-weight: 800;
            letter-spacing: -0.02em;
        }
        
        .premium-header p {
            color: #94a3b8; 
            margin: 8px 0 0 0;
            font-size: 1em;
        }

        /* -------------------------------------
           メトリクス（上部ステータス）のデザイン変更
        ------------------------------------- */
        [data-testid="stMetricValue"] {
            font-size: 2rem !important;
            color: #f8fafc !important;
            font-weight: 700 !important;
        }
        [data-testid="stMetricLabel"] {
            font-size: 1rem !important;
            color: #94a3b8 !important;
        }
        [data-testid="metric-container"] {
            background: rgba(30, 41, 59, 0.5);
            border: 1px solid rgba(255, 255, 255, 0.05);
            padding: 16px;
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        }

        /* タブのスタイル調整 */
        .stTabs [data-baseweb="tab-list"] {
            gap: 24px;
        }
        .stTabs [data-baseweb="tab"] {
            height: 50px;
            white-space: pre-wrap;
            background-color: transparent;
            border-radius: 4px 4px 0px 0px;
            gap: 1px;
            padding-top: 10px;
            padding-bottom: 10px;
            color: #94a3b8;
        }
        .stTabs [aria-selected="true"] {
            color: #f8fafc !important;
            border-bottom-color: #ec4899 !important;
        }

        /* データフレーム（表）の背景と枠線調整 */
        [data-testid="stDataFrame"] {
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 8px;
        }

        </style>
        <div class="premium-header">
            <h1>🌌 Nexus Dashboard</h1>
            <p>Novelove 記事データ統合ビューワー（Read-Only）</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ─── データ読み込み ───
    with st.spinner("データを読み込んでいます..."):
        df = load_all_data()

    if df.empty:
        st.error("データが読み込めませんでした。DBファイルのパスを確認してください。")
        st.info(f"検索先: {', '.join(DB_SOURCES.values())}")
        return

    total = len(df)

    # =====================================================================
    # 左サイドバー：フィルターパネル
    # =====================================================================
    with st.sidebar:
        st.markdown("### 🔍 フィルター & 検索")
        
        # キーワード検索
        keyword = st.text_input("タイトル・作品IDで検索", placeholder="例: 騎士団長")

        st.markdown("<br>", unsafe_allow_html=True)

        # ステータスフィルター
        all_statuses = sorted(df["status"].dropna().unique().tolist()) if "status" in df.columns else []
        selected_statuses = st.pills(
            "ステータス (無選択で全表示)",
            options=all_statuses,
            default=[],
            format_func=status_badge,
            selection_mode="multi",
        )

        st.markdown("<br>", unsafe_allow_html=True)

        # DBフィルター
        db_options = sorted(df["_source_db"].dropna().unique().tolist()) if "_source_db" in df.columns else list(DB_SOURCES.keys())
        selected_dbs = st.pills(
            "プラットフォーム (無選択で全表示)",
            options=db_options,
            default=[],
            selection_mode="multi",
        )

        st.markdown("<br>", unsafe_allow_html=True)

        # ジャンルフィルター
        all_genres = sorted(df["genre"].dropna().unique().tolist()) if "genre" in df.columns else []
        selected_genres = st.pills(
            "ジャンル (無選択で全表示)",
            options=all_genres,
            default=[],
            selection_mode="multi",
        )

        st.markdown("<br>", unsafe_allow_html=True)

        # 記事種別フィルター
        if "post_type" in df.columns:
            all_types = sorted(df["post_type"].dropna().unique().tolist())
            type_labels = {"regular": "通常記事", "ranking": "ランキング"}
            selected_types = st.pills(
                "記事種別 (無選択で全表示)",
                options=all_types,
                default=[],
                format_func=lambda x: type_labels.get(x, x),
                selection_mode="multi",
            )
        else:
            selected_types = []

        st.markdown("<br>", unsafe_allow_html=True)

        # スコアフィルター
        if "desc_score" in df.columns:
            min_score = int(df["desc_score"].min())
            max_score = int(df["desc_score"].max())
            if min_score < max_score:
                score_range = st.slider(
                    "AIスコア範囲",
                    min_value=min_score,
                    max_value=max_score,
                    value=(min_score, max_score),
                )
            else:
                score_range = (min_score, max_score)
        else:
            score_range = (0, 99)

        st.markdown("<br>", unsafe_allow_html=True)

        # セール中のみ
        only_sale = st.checkbox("🔥 セール中のみ")

        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 データを再読み込み", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # =====================================================================
    # フィルタリング処理
    # =====================================================================
    filtered = df.copy()

    if keyword:
        mask = (
            filtered["title"].str.contains(keyword, case=False, na=False)
            | filtered["product_id"].str.contains(keyword, case=False, na=False)
        )
        filtered = filtered[mask]

    if selected_statuses and "status" in filtered.columns:
        filtered = filtered[filtered["status"].isin(selected_statuses)]

    if selected_dbs:
        filtered = filtered[filtered["_source_db"].isin(selected_dbs)]

    if selected_genres and "genre" in filtered.columns:
        filtered = filtered[filtered["genre"].isin(selected_genres)]

    if selected_types and "post_type" in filtered.columns:
        filtered = filtered[filtered["post_type"].isin(selected_types)]

    if "desc_score" in filtered.columns:
        filtered = filtered[
            (filtered["desc_score"] >= score_range[0]) &
            (filtered["desc_score"] <= score_range[1])
        ]

    if only_sale and "sale_discount_rate" in filtered.columns:
        filtered = filtered[filtered["sale_discount_rate"] > 0]

    # =====================================================================
    # サマリーカード (トップメトリクス領域)
    # =====================================================================
    pub_count  = len(filtered[filtered["status"] == "published"]) if "status" in filtered.columns else 0
    pend_count = len(filtered[filtered["status"] == "pending"])   if "status" in filtered.columns else 0
    excl_count = len(filtered[filtered["status"] == "excluded"])  if "status" in filtered.columns else 0
    sale_count = len(filtered[filtered["sale_discount_rate"] > 0]) if "sale_discount_rate" in filtered.columns else 0

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("📦 総件数", f"{total:,}")
    col2.metric("🔍 絞り込み後", f"{len(filtered):,}")
    col3.metric("🟢 公開済み", f"{pub_count:,}")
    col4.metric("🟡 在庫中", f"{pend_count:,}")
    col5.metric("🔥 セール中", f"{sale_count:,}")

    st.markdown("<br>", unsafe_allow_html=True)

    # =====================================================================
    # TABS: メインエリアの分割 (リスト一覧 と 詳細・リライト)
    # =====================================================================
    tab_kpi, tab_list = st.tabs(["📊 KPIサマリー ＆ GSC", "📋 データ一覧"])

    with tab_kpi:
        # =====================================================================
        # GSC 死に記事アラートパネル
        # =====================================================================
        if any(c in df.columns for c in ["gsc_indexed", "gsc_impressions", "gsc_clicks"]):
            st.markdown("---")
            st.markdown("#### ⚠️ GSC 死に記事アラート")

            published = df[df["status"] == "published"].copy() if "status" in df.columns else df.copy()
            gsc_checked = published[published["gsc_last_checked"].notna()] if "gsc_last_checked" in published.columns else pd.DataFrame()

            if gsc_checked.empty:
                st.info(
                    "📡 まだ GSC データがありません。\n"
                    "サーバーで `python nexus_gsc.py` を実行してください。"
                )
            else:
                # 死に記事 3レベルに分類
                lv1 = gsc_checked[gsc_checked["gsc_indexed"] == 0]
                lv2 = gsc_checked[
                    (gsc_checked["gsc_indexed"] == 1) &
                    (gsc_checked["gsc_impressions"] == 0)
                ]
                lv3 = gsc_checked[
                    (gsc_checked["gsc_indexed"] == 1) &
                    (gsc_checked["gsc_impressions"] > 0) &
                    (gsc_checked["gsc_clicks"] == 0)
                ]

                # サマリーカード
                gc1, gc2, gc3, gc4 = st.columns(4)
                gc1.metric("📡 GSC確認済み", f"{len(gsc_checked):,}件")
                gc2.metric("🔴 Lv1 未インデックス", f"{len(lv1):,}件")
                gc3.metric("🟡 Lv2 表示0",         f"{len(lv2):,}件")
                gc4.metric("🟠 Lv3 クリック0",      f"{len(lv3):,}件")

                # 各レベルの詳細テーブル
                for level_name, level_df, color_emoji in [
                    ("🔴 Lv1: 未インデックス（公開後30日以上・Googleに登録されていない）", lv1, "🔴"),
                    ("🟡 Lv2: 表示0（インデックス済みだが30日間表示なし）",             lv2, "🟡"),
                    ("🟠 Lv3: クリック0（表示はあるがクリックされていない）",            lv3, "🟠"),
                ]:
                    if level_df.empty:
                        continue
                    with st.expander(f"{level_name} — {len(level_df)}件", expanded=(color_emoji == "🔴")):
                        show_dead_cols = [c for c in [
                            "product_id", "title", "gsc_impressions", "gsc_clicks",
                            "gsc_last_checked", "wp_post_url", "published_at"
                        ] if c in level_df.columns]
                        dead_display = level_df[show_dead_cols].copy()
                        dead_display.columns = [
                            c.replace("product_id", "作品ID")
                             .replace("title", "タイトル")
                             .replace("gsc_impressions", "表示回数")
                             .replace("gsc_clicks", "クリック数")
                             .replace("gsc_last_checked", "GSC最終確認")
                             .replace("wp_post_url", "記事URL")
                             .replace("published_at", "公開日")
                            for c in show_dead_cols
                        ]
                        event_gsc = st.dataframe(
                            dead_display,
                            use_container_width=True,
                            height=min(300, 35 * len(level_df) + 38),
                            selection_mode="single-row",
                            on_select="rerun",
                            key=f"df_gsc_{level_name[:3]}",
                            column_config={
                                "記事URL": st.column_config.LinkColumn(
                                    "記事URL", display_text="📝 開く", width="small"
                                ),
                            },
                        )
                        selected_gsc_pid = ""
                        if hasattr(event_gsc, 'selection') and isinstance(event_gsc.selection, dict) and event_gsc.selection.get("rows"):
                            try:
                                sel_idx = event_gsc.selection["rows"][0]
                                selected_gsc_pid = str(level_df.iloc[sel_idx]["product_id"])
                            except Exception:
                                pass
                    
                        if selected_gsc_pid:
                            st.markdown("---")
                            st.markdown(f"#### 🔎 {level_name[:3]} 選択作品の詳細・リライト")
                            render_detail_panel(selected_gsc_pid, df, key_prefix=f"gsc_{level_name[:3]}")



    with tab_list:
        if filtered.empty:
            st.info("条件に一致するデータがありません。")
        else:
            # 表示用に整形
            display_df = format_display_df(filtered)

            # 表示するカラムを選定（存在するもののみ）
            show_cols_priority = [
                "サムネイル", "ステータス", "記事種別", "DB", "タイトル", "📝", "ノベラブ", "販売元", "発売日",
                "ジャンル", "担当", "スコア", "タグ", "セール", "公開日", "取得日", "エラー",
            ]
            show_cols = [c for c in show_cols_priority if c in display_df.columns]

            st.info("💡 **一覧表の【一番左端にあるチェックボックス】をクリック**すると、画面最下部の「🔎 ダイレクト リライト」パネルに作品IDが自動入力されます。")

            event = st.dataframe(
                display_df[show_cols],
                use_container_width=True,
                height=600,
                selection_mode="single-row",
                on_select="rerun",
                column_config={
                    "サムネイル": st.column_config.ImageColumn("🖼", width="small"),
                    "ステータス": st.column_config.TextColumn("状態", width="small"),
                    "記事種別":  st.column_config.TextColumn("種別", width="small"),
                    "DB":       st.column_config.TextColumn("DB", width="small"),
                    "タイトル": st.column_config.TextColumn(width="large"),
                    "📝":       st.column_config.TextColumn("あらすじ", width="small"),
                    "ノベラブ": st.column_config.LinkColumn("ノベラブ", display_text="📝 開く", width="small"),
                    "販売元":   st.column_config.LinkColumn("販売元", display_text="🛒 開く", width="small"),
                    "発売日":   st.column_config.TextColumn(width="small"),
                    "ジャンル": st.column_config.TextColumn(width="small"),
                    "担当":     st.column_config.TextColumn(width="small"),
                    "スコア":   st.column_config.ProgressColumn("スコア", min_value=0, max_value=5, format="%d", width="small"),
                    "タグ":     st.column_config.TextColumn(width="medium"),
                    "セール":   st.column_config.TextColumn(width="small"),
                    "公開日":   st.column_config.TextColumn(width="small"),
                    "取得日":   st.column_config.TextColumn(width="small"),
                    "エラー":   st.column_config.TextColumn(width="medium"),
                },
            )

            # データ一覧テーブルで選択された行の作品IDを取得
            if hasattr(event, 'selection') and isinstance(event.selection, dict) and event.selection.get("rows"):
                try:
                    sel_row_idx = event.selection["rows"][0]
                    st.session_state["_selected_pid_from_list"] = str(filtered.iloc[sel_row_idx]["product_id"])
                except Exception:
                    pass


    
    # =====================================================================
    # 🔎 ダイレクト リライト・詳細パネル (完全分離・常時表示)
    # =====================================================================
    st.markdown("---")
    st.markdown("## 🔎 ダイレクト 作品詳細 ＆ リライト")
    st.info("💡 上の「データ一覧」や「GSCアラート」で選択した作品のIDが自動入力されます。または手動で直接IDを入力して検索も可能です。")

    # session_state から選択済みIDを取得（テーブル選択 or 手動入力）
    auto_pid = st.session_state.get("_selected_pid_from_list", "")

    target_pid = st.text_input(
        "📝 リライト対象の作品ID (例: RJ012345)",
        value=auto_pid,
        placeholder="例: RJ012345",
        key="global_rewrite_pid_input",
    )

    if target_pid:
        render_detail_panel(target_pid, df, key_prefix="global")
    else:
        st.caption("⬆️ 上のテキストボックスに作品IDを入力するか、データ一覧 / GSCアラートで行を選択してください。")

    # =====================================================================
    # フッター
    # =====================================================================
    st.markdown("---")
    st.caption(
        "🌌 Nexus Dashboard (Phase 2 / Step 3) | "
        "リライト・あらすじ更新検知・GSC死に記事アラート"
    )


if __name__ == "__main__":
    main()
