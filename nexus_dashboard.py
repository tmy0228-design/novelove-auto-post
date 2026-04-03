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
    for col in ["inserted_at", "published_at", "last_revived_at"]:
        if col in combined.columns:
            combined[col] = pd.to_datetime(combined[col], errors="coerce")

    return combined


# =====================================================================
# 2. UI ヘルパー
# =====================================================================
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
            lambda v: "updated" if int(v or 0) == 1 else "-"
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
def main():
    st.set_page_config(
        page_title="Nexus Dashboard",
        page_icon="🌌",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # ─── ヘッダー ───
    st.markdown(
        """
        <div style='background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
                    padding: 24px 32px; border-radius: 12px; margin-bottom: 24px;'>
            <h1 style='color: #e94560; margin: 0; font-size: 2em;'>🌌 Nexus Dashboard</h1>
            <p style='color: #a0aec0; margin: 4px 0 0 0;'>
                Novelove 記事データ統合ビューワー（Read-Only）
            </p>
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
    # サイドバー：フィルターパネル
    # =====================================================================
    with st.sidebar:
        st.markdown("### 🔍 フィルター & 検索")
        st.markdown("---")

        # キーワード検索
        keyword = st.text_input("タイトル・作品IDで検索", placeholder="例: 騎士団長")

        st.markdown("---")

        # ステータスフィルター
        all_statuses = sorted(df["status"].dropna().unique().tolist()) if "status" in df.columns else []
        selected_statuses = st.multiselect(
            "ステータス",
            options=all_statuses,
            default=all_statuses,
            format_func=status_badge,
        )

        st.markdown("---")

        # DBフィルター
        db_options = sorted(df["_source_db"].dropna().unique().tolist()) if "_source_db" in df.columns else list(DB_SOURCES.keys())
        selected_dbs = st.multiselect(
            "プラットフォーム",
            options=db_options,
            default=db_options,
        )

        st.markdown("---")

        # ジャンルフィルター
        all_genres = sorted(df["genre"].dropna().unique().tolist()) if "genre" in df.columns else []
        selected_genres = st.multiselect(
            "ジャンル",
            options=all_genres,
            default=all_genres,
        )

        st.markdown("---")

        # 記事種別フィルター
        if "post_type" in df.columns:
            all_types = sorted(df["post_type"].dropna().unique().tolist())
            type_labels = {"regular": "通常記事", "ranking": "ランキング"}
            selected_types = st.multiselect(
                "記事種別",
                options=all_types,
                default=all_types,
                format_func=lambda x: type_labels.get(x, x)
            )
        else:
            selected_types = []

        st.markdown("---")

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

        st.markdown("---")

        # セール中のみ
        only_sale = st.checkbox("🔥 セール中のみ")

        st.markdown("---")
        if st.button("🔄 データを再読み込み"):
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
    # サマリーカード
    # =====================================================================
    pub_count  = len(filtered[filtered["status"] == "published"]) if "status" in filtered.columns else 0
    pend_count = len(filtered[filtered["status"] == "pending"])   if "status" in filtered.columns else 0
    excl_count = len(filtered[filtered["status"] == "excluded"])  if "status" in filtered.columns else 0
    sale_count = len(filtered[filtered["sale_discount_rate"] > 0]) if "sale_discount_rate" in filtered.columns else 0

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("📦 総件数", f"{total:,}件")
    col2.metric("🔍 絞り込み後", f"{len(filtered):,}件")
    col3.metric("🟢 公開済み", f"{pub_count:,}件")
    col4.metric("🟡 在庫中", f"{pend_count:,}件")
    col5.metric("🔥 セール中", f"{sale_count:,}件")

    st.markdown("---")

    # =====================================================================
    # メインテーブル
    # =====================================================================
    st.markdown(f"#### 📋 記事一覧（{len(filtered):,}件）")

    if filtered.empty:
        st.info("条件に一致するデータがありません。")
        return

    # 表示用に整形
    display_df = format_display_df(filtered)

    # 表示するカラムを選定（存在するもののみ）
    show_cols_priority = [
        "サムネイル", "ステータス", "記事種別", "DB", "タイトル", "📝", "ノベラブ", "販売元", "発売日",
        "ジャンル", "担当", "スコア", "タグ", "セール", "公開日", "取得日", "エラー",
    ]
    show_cols = [c for c in show_cols_priority if c in display_df.columns]

    st.dataframe(
        display_df[show_cols],
        use_container_width=True,
        height=600,
        column_config={
            "サムネイル": st.column_config.ImageColumn(
                "🖼", width="small",
            ),
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
            "スコア":   st.column_config.ProgressColumn(
                "スコア", min_value=0, max_value=5, format="%d", width="small",
            ),
            "タグ":     st.column_config.TextColumn(width="medium"),
            "セール":   st.column_config.TextColumn(width="small"),
            "公開日":   st.column_config.TextColumn(width="small"),
            "取得日":   st.column_config.TextColumn(width="small"),
            "エラー":   st.column_config.TextColumn(width="medium"),
        },
    )

    # =====================================================================
    # 詳細ビュー ＋ リライト操作パネル
    # =====================================================================
    st.markdown("---")
    st.markdown("#### 🔎 作品IDで詳細確認・リライト")
    detail_pid = st.text_input("作品ID（例: RJ012345 / d_12345 / ITM12345）", key="detail_pid")

    if detail_pid:
        match = df[df["product_id"].str.lower() == detail_pid.lower()]
        if not match.empty:
            row = match.iloc[0]

            # ── 上段: 記事情報 ──
            c1, c2 = st.columns([1, 2])
            with c1:
                if pd.notna(row.get("image_url")) and row.get("image_url"):
                    st.image(row["image_url"], width=200, caption=row.get("title", ""))
            with c2:
                st.markdown(f"**タイトル**: {row.get('title', '-')}")
                st.markdown(f"**作品ID**: `{row.get('product_id', '-')}`")
                st.markdown(f"**ステータス**: {status_badge(row.get('status', '-'))}")
                st.markdown(f"**あらすじスコア**: {row.get('desc_score', '-')}")
                st.markdown(f"**担当ライター**: {row.get('reviewer', '-')}")
                st.markdown(f"**タグ**: {row.get('ai_tags', '-')}")
                if row.get("wp_post_url"):
                    st.markdown(f"**WP記事**: [{row['wp_post_url']}]({row['wp_post_url']})")
                if row.get("sale_discount_rate", 0) > 0:
                    st.success(f"🔥 現在 {row['sale_discount_rate']}% セール中！")

            # ── あらすじ差分ビュー（更新検知時のみ表示） ──
            if int(row.get("is_desc_updated") or 0) == 1:
                st.markdown("---")
                prev_desc = row.get("prev_description") or ""
                new_desc  = row.get("description") or ""
                st.warning("📝 **あらすじが更新されました！** リライトを検討してください。")
                col_prev, col_new = st.columns(2)
                with col_prev:
                    st.caption(f"旧あらすじ（{len(prev_desc)}文字）")
                    st.text_area("旧あらすじ", value=prev_desc, height=200, disabled=True, key="prev_desc_view", label_visibility="collapsed")
                with col_new:
                    st.caption(f"新あらすじ（{len(new_desc)}文字）")
                    st.text_area("新あらすじ", value=new_desc, height=200, disabled=True, key="new_desc_view", label_visibility="collapsed")

                # 変化量の表示
                import difflib
                ratio = difflib.SequenceMatcher(None, prev_desc, new_desc).ratio()
                char_diff = len(new_desc) - len(prev_desc)
                diff_sign = "+" if char_diff >= 0 else ""
                st.caption(f"内容一致率: {ratio:.1%} | 文字数差: {diff_sign}{char_diff}文字")

                if st.button("✅ 確認済み（フラグをリセット）", key="btn_confirm_desc"):
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
                        key="sel_reviewer",
                    )
                with rc2:
                    selected_mood_label = st.selectbox(
                        "感情モード",
                        options=list(MOOD_OPTIONS.keys()),
                        key="sel_mood",
                    )

                reviewer_id = REVIEWER_OPTIONS[selected_reviewer_label]
                mood_str    = MOOD_OPTIONS[selected_mood_label]

                # ── DRY-RUN ボタン ──
                st.info(
                    "💡 **まず DRY-RUN で確認** → 問題なければサーバーで `--execute` を付けて実行してください。\n\n"
                    "DRY-RUN は WP・DB に一切書き込みません。生成内容の確認のみです。"
                )

                if st.button("🧪 DRY-RUN で内容を確認", key="btn_dryrun"):
                    st.session_state["rw_pid"]      = str(row["product_id"])
                    st.session_state["rw_reviewer"] = reviewer_id
                    st.session_state["rw_mood"]     = mood_str
                    st.session_state["rw_phase"]    = "running_dryrun"

                # ── DRY-RUN 実行 ──
                if st.session_state.get("rw_phase") == "running_dryrun" and \
                   st.session_state.get("rw_pid") == str(row["product_id"]):

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
                                    product_id=st.session_state["rw_pid"],
                                    reviewer_id=st.session_state["rw_reviewer"],
                                    mood=st.session_state["rw_mood"],
                                    execute=False,
                                )
                            finally:
                                _nv_logger.removeHandler(_capture_handler)

                            st.session_state["rw_log"] = log_buffer.getvalue()
                            st.session_state["rw_dryrun_success"] = success
                            st.session_state["rw_phase"] = "dryrun_done"
                        except Exception as e:
                            st.error(f"❌ DRY-RUN 実行中にエラーが発生しました: {e}")
                            st.session_state["rw_phase"] = None

                # ── DRY-RUN 結果表示 ──
                if st.session_state.get("rw_phase") == "dryrun_done" and \
                   st.session_state.get("rw_pid") == str(row["product_id"]):

                    if st.session_state.get("rw_dryrun_success"):
                        st.success("✅ DRY-RUN 完了！")
                        # キャプチャしたログを表示
                        captured_log = st.session_state.get("rw_log", "")
                        if captured_log:
                            with st.expander("📋 実行ログ（クリックで展開）", expanded=True):
                                st.text(captured_log)
                        st.markdown(
                            "**本番実行するには、サーバーで以下のコマンドを実行してください:**"
                        )
                        pid_val      = st.session_state["rw_pid"]
                        rev_val      = st.session_state.get("rw_reviewer") or ""
                        mood_val     = st.session_state.get("rw_mood") or ""
                        cmd_parts    = [f"python nexus_rewrite.py --product-id {pid_val}"]
                        if rev_val:
                            cmd_parts.append(f'--reviewer {rev_val}')
                        if mood_val:
                            cmd_parts.append(f'--mood "{mood_val}"')
                        cmd_parts.append("--execute")
                        st.code(" ".join(cmd_parts), language="bash")

                        if st.button("🔄 別の設定で再度 DRY-RUN", key="btn_reset_dryrun"):
                            st.session_state["rw_phase"] = None
                            st.rerun()
                    else:
                        st.error("❌ DRY-RUN が失敗しました。ログを確認してください。")
                        captured_log = st.session_state.get("rw_log", "")
                        if captured_log:
                            with st.expander("📋 実行ログ", expanded=True):
                                st.text(captured_log)
                        if st.button("🔄 やり直す", key="btn_retry"):
                            st.session_state["rw_phase"] = None
                            st.rerun()
            else:
                st.caption(f"⚠️ この記事はステータスが `{row.get('status', '-')}` のためリライトできません（published のみ対象）")
        else:
            st.warning("該当する作品IDが見つかりませんでした。")

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
                    st.dataframe(
                        dead_display,
                        use_container_width=True,
                        height=min(300, 35 * len(level_df) + 38),
                        column_config={
                            "記事URL": st.column_config.LinkColumn(
                                "記事URL", display_text="📝 開く", width="small"
                            ),
                        },
                    )

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
