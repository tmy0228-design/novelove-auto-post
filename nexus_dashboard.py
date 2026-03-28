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
]

STATUS_COLORS = {
    "published": "🟢",
    "pending":   "🟡",
    "excluded":  "🔴",
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
            df["_source_db"] = label  # どのDBから来たか記録
            conn.close()
            frames.append(df)
        except Exception as e:
            st.warning(f"⚠️ {label} の読み込みに失敗しました: {e}")

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False)

    # 型の整理
    for col in ["desc_score", "sale_discount_rate"]:
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
    icon = STATUS_COLORS.get(str(status), "⚪")
    return f"{icon} {status}"


def format_display_df(df: pd.DataFrame) -> pd.DataFrame:
    """表示用にカラムを整形する。"""
    display = df.copy()

    # ステータスにアイコンを付与
    if "status" in display.columns:
        display["ステータス"] = display["status"].apply(status_badge)

    # 日付を見やすく
    for col, label in [("published_at", "公開日時"), ("inserted_at", "取得日時")]:
        if col in display.columns:
            display[label] = display[col].dt.strftime("%Y-%m-%d %H:%M").fillna("-")

    # セール中表示
    if "sale_discount_rate" in display.columns:
        display["セール"] = display["sale_discount_rate"].apply(
            lambda v: f"🔥 {v}%OFF" if v > 0 else "-"
        )

    # スコアをバー表示用に整形
    if "desc_score" in display.columns:
        display["スコア"] = display["desc_score"]

    # 表示カラムを整理
    rename_map = {
        "product_id":  "作品ID",
        "title":       "タイトル",
        "author":      "作者",
        "genre":       "ジャンル",
        "_source_db":  "DB",
        "reviewer":    "担当",
        "ai_tags":     "タグ",
        "last_error":  "最終エラー",
        "wp_post_url": "WP URL",
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
        selected_dbs = st.multiselect(
            "プラットフォーム",
            options=list(DB_SOURCES.keys()),
            default=list(DB_SOURCES.keys()),
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
        "ステータス", "DB", "タイトル", "ジャンル", "担当",
        "スコア", "セール", "公開日時", "取得日時", "最終エラー", "タグ",
    ]
    show_cols = [c for c in show_cols_priority if c in display_df.columns]

    st.dataframe(
        display_df[show_cols],
        use_container_width=True,
        height=600,
        column_config={
            "タイトル": st.column_config.TextColumn(width="large"),
            "スコア":   st.column_config.ProgressColumn(
                "スコア", min_value=0, max_value=5, format="%d"
            ),
            "セール":   st.column_config.TextColumn(width="small"),
        },
    )

    # =====================================================================
    # 詳細ビュー（行クリックで展開）
    # =====================================================================
    st.markdown("---")
    st.markdown("#### 🔎 作品IDで詳細確認")
    detail_pid = st.text_input("作品ID（例: RJ012345 / d_12345 / ITM12345）", key="detail_pid")
    if detail_pid:
        match = df[df["product_id"].str.lower() == detail_pid.lower()]
        if not match.empty:
            row = match.iloc[0]
            c1, c2 = st.columns([1, 2])
            with c1:
                if pd.notna(row.get("image_url")) and row.get("image_url"):
                    st.image(row["image_url"], width=200, caption=row.get("title", ""))
            with c2:
                st.markdown(f"**タイトル**: {row.get('title', '-')}")
                st.markdown(f"**作品ID**: `{row.get('product_id', '-')}`")
                st.markdown(f"**ステータス**: {status_badge(row.get('status', '-'))}")
                st.markdown(f"**スコア**: {row.get('desc_score', '-')}")
                st.markdown(f"**担当**: {row.get('reviewer', '-')}")
                st.markdown(f"**タグ**: {row.get('ai_tags', '-')}")
                if row.get("wp_post_url"):
                    st.markdown(f"**WP記事**: [{row['wp_post_url']}]({row['wp_post_url']})")
                if row.get("sale_discount_rate", 0) > 0:
                    st.success(f"🔥 現在 {row['sale_discount_rate']}% セール中！")
        else:
            st.warning("該当する作品IDが見つかりませんでした。")

    # =====================================================================
    # フッター
    # =====================================================================
    st.markdown("---")
    st.caption(
        "🌌 Nexus Dashboard (Phase 2 / Step 1) | "
        "Read-Only Viewer — このダッシュボードからデータを変更・削除することはできません。"
    )


if __name__ == "__main__":
    main()
