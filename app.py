"""
Streamlit エントリーポイント。

このファイルは UI 層のみを担当する:
- ファイルアップロード受付
- 過去データ読み込み(data_loader)の呼び出し
- 予想ロジック(prediction_logic)の呼び出し
- 結果のテーブル/エクスパンダー表示・CSVダウンロード

データ処理ロジックは data_loader.py / prediction_logic.py に分離してある。

起動方法:
    streamlit run app.py
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from data_loader import (
    HistoricalData,
    load_historical_data,
    load_race_card,
    summarize_race_card,
    validate_race_card,
)
from prediction_logic import HorsePrediction, predict_all_races

# =====================================================================
# 画面全体の設定
# =====================================================================
st.set_page_config(
    page_title="競馬予想アプリ",
    page_icon="🏇",
    layout="wide",
)


# =====================================================================
# 過去データの読み込み
# =====================================================================
# Streamlit のキャッシュ: @st.cache_data を付けた関数は、引数が同じなら
# 結果を再利用するため、ファイル読み込みを毎回やり直さない(初回起動の高速化)
@st.cache_data(show_spinner="過去データを読み込み中…")
def get_historical() -> HistoricalData:
    """過去データの読み込み(キャッシュ済み)"""
    return load_historical_data()


# =====================================================================
# サイドバー: アプリ説明 + 過去データの統計
# =====================================================================
with st.sidebar:
    st.title("🏇 競馬予想アプリ")
    st.caption("JRA中央競馬・個人利用専用")

    st.markdown(
        """
        ### 使い方
        1. 当日の出馬表 CSV をアップロード
        2. 「予想実行」ボタンを押す
        3. レースごとに ◎○▲△ を確認
        4. 必要なら結果を CSV でダウンロード
        """
    )

    st.divider()
    st.subheader("📊 過去データ")

    # 過去データの読み込み(失敗してもアプリは続行)
    try:
        historical = get_historical()
        # データの出所を明示(本番Parquet or 開発用CSVサンプル)
        source_label = "本番(Parquet)" if historical.source == "parquet" else "サンプル(CSV)"
        st.metric("データ種別", source_label)
        st.metric("過去レース数", f"{historical.races['race_id'].nunique():,} レース")
        st.metric("登録馬数", f"{len(historical.horses):,} 頭")
    except FileNotFoundError as e:
        historical = None
        st.error(str(e))


# =====================================================================
# メイン領域
# =====================================================================
st.title("🏇 競馬予想アプリ")
st.caption("当日の出馬表 CSV をアップロードして「予想実行」を押してください。")

# --- 出馬表アップロード ------------------------------------------------
uploaded = st.file_uploader(
    "当日出馬表 CSV をアップロード",
    type=["csv"],
    accept_multiple_files=False,
    help="JV-Link または TARGET frontier JV からエクスポートした CSV を想定。",
)

# 開発用: サンプルファイルを使ってお試しできるトグル
use_sample = st.toggle(
    "サンプル出馬表で試す(開発用)",
    value=False,
    help="data/samples/sample_race_card.csv を使って動作確認します。",
)

# DataFrame に変換するソース(ユーザのアップロード優先、無ければサンプル)
race_card_df: pd.DataFrame | None = None
source_name: str | None = None
if uploaded is not None:
    try:
        race_card_df = load_race_card(uploaded)
        source_name = uploaded.name
    except Exception as e:
        st.error(f"CSV の読み込みに失敗しました: {e}")
elif use_sample:
    try:
        race_card_df = load_race_card("data/samples/sample_race_card.csv")
        source_name = "sample_race_card.csv"
    except Exception as e:
        st.error(f"サンプル CSV の読み込みに失敗しました: {e}")


# =====================================================================
# 出馬表のプレビュー & バリデーション
# =====================================================================
if race_card_df is not None:
    st.success(f"読み込み完了: {source_name}")

    # 列構成チェック
    validation = validate_race_card(race_card_df)
    if not validation.ok:
        st.error(validation.message)
        st.stop()  # 列が揃っていなければ予想実行に進ませない

    # 概要メトリクス
    summary = summarize_race_card(race_card_df)
    col1, col2 = st.columns(2)
    col1.metric("レース数", f"{summary['race_count']} レース")
    col2.metric("出走頭数", f"{summary['horse_count']} 頭")

    # 出馬表のプレビュー表(全件表示は重いので先頭のみ)
    with st.expander("出馬表プレビュー(先頭20行)"):
        st.dataframe(race_card_df.head(20), use_container_width=True)


# =====================================================================
# 予想実行
# =====================================================================
if race_card_df is not None and historical is not None:
    st.divider()
    if st.button("🎯 予想実行", type="primary", use_container_width=True):
        with st.spinner("予想計算中…"):
            # 全レース分の予想を一気に計算
            results = predict_all_races(race_card_df, historical)

        st.success(f"予想完了({len(results)} レース)")

        # ダウンロード用フラットDataFrameを構築
        download_rows: list[dict] = []
        for race_id, preds in results.items():
            # race_id ごとの基本情報をマージしておく(レース名・距離・コース等)
            race_info_row = race_card_df[race_card_df["race_id"] == race_id].iloc[0]
            for pred in preds:
                download_rows.append({
                    "race_id": race_id,
                    "racecourse": race_info_row.get("racecourse", ""),
                    "race_number": race_info_row.get("race_number", ""),
                    "race_name": race_info_row.get("race_name", ""),
                    "distance": race_info_row.get("distance", ""),
                    "surface": race_info_row.get("surface", ""),
                    "印": pred.mark,
                    "horse_id": pred.horse_id,
                    "horse_name": pred.horse_name,
                    "jockey": pred.jockey,
                    "score": pred.score,
                    "reasons": " | ".join(pred.reasons),
                })
        download_df = pd.DataFrame(download_rows)

        # ===== CSVダウンロードボタン =====
        csv_buffer = io.StringIO()
        # Excel で開いた時に文字化けしないよう BOM 付き UTF-8 で出力
        download_df.to_csv(csv_buffer, index=False)
        st.download_button(
            label="📥 予想結果を CSV でダウンロード",
            data=("﻿" + csv_buffer.getvalue()).encode("utf-8"),
            file_name="prediction_results.csv",
            mime="text/csv",
        )

        # ===== レースごとの結果表示 =====
        st.subheader("レースごとの予想")
        for race_id, preds in results.items():
            race_info_row = race_card_df[race_card_df["race_id"] == race_id].iloc[0]
            # エクスパンダのタイトルにレース概要を入れる
            title = (
                f"【{race_info_row.get('racecourse', '')} "
                f"{race_info_row.get('race_number', '')}R】 "
                f"{race_info_row.get('race_name', '')} "
                f"{race_info_row.get('distance', '')}m "
                f"{race_info_row.get('surface', '')}"
            )
            with st.expander(title, expanded=True):
                # 推奨馬テーブル(印付き = 上位4頭)を上部に表示
                top_rows = [
                    {
                        "印": p.mark,
                        "馬名": p.horse_name,
                        "騎手": p.jockey,
                        "スコア": p.score,
                    }
                    for p in preds if p.mark
                ]
                if top_rows:
                    st.markdown("**推奨馬(上位4頭)**")
                    st.dataframe(pd.DataFrame(top_rows), hide_index=True, use_container_width=True)

                # 各馬の理由(クリックで展開可能)
                st.markdown("**全頭の評価詳細**")
                for p in preds:
                    label = (f"{p.mark} " if p.mark else "　 ") + f"{p.horse_name}({p.jockey})  スコア {p.score}"
                    with st.expander(label, expanded=False):
                        if p.reasons:
                            for r in p.reasons:
                                st.write(f"- {r}")
                        else:
                            st.write("(理由情報なし)")

elif race_card_df is None:
    st.info("👆 出馬表 CSV をアップロードしてください。")
