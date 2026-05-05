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
#
# _schema_version は HistoricalData の構造を変えた時にキャッシュを
# 強制的に作り直すための「キャッシュ世代」バージョン。
# Streamlit Cloud の再デプロイでは pickle キャッシュが残り、旧スキーマの
# 物体(.source(str)を持ち .sources(dict)を持たない)が返ってくる事故が
# 起きるため、スキーマを変えたらここの値を v2 → v3 のように手で上げる。
HISTORICAL_DATA_SCHEMA_VERSION = "v2-per-table-sources"


@st.cache_data(show_spinner="過去データを読み込み中…")
def get_historical(_schema_version: str = HISTORICAL_DATA_SCHEMA_VERSION) -> HistoricalData:
    """過去データの読み込み(キャッシュ済み)。
    _schema_version はキャッシュキーを世代管理するためだけの引数で、
    値を変えると同名関数でも別キャッシュとして扱われる。"""
    return load_historical_data()


# =====================================================================
# メイン領域 上部: タイトル + 出馬表アップロード
# =====================================================================
# 出馬表を先に読み込んでおく。サイドバーの「競馬場フィルタ」が出馬表に含まれる
# 場の集合に依存して動的に選択肢を出すため、サイドバー描画前に race_card_df を
# 確定させる必要がある。
st.title("🏇 競馬予想アプリ")
st.caption("当日の出馬表 CSV をアップロードして「予想実行」を押してください。")

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
# サイドバー: アプリ説明 + 競馬場フィルタ + 過去データ統計
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

    # ----- 競馬場フィルタ -----
    # 出馬表がアップロード済みのときだけ表示。当日 CSV に登場する場のみを
    # 動的に選択肢にする(他場の枠は出さない)。
    if race_card_df is not None and "racecourse" in race_card_df.columns:
        st.divider()
        st.subheader("📍 競馬場フィルタ")
        course_options = ["全場"] + sorted(race_card_df["racecourse"].dropna().unique().tolist())
        selected_course = st.radio(
            "表示する競馬場",
            course_options,
            index=0,
            key="course_filter",
        )
    else:
        # 出馬表が無い時のデフォルト(後段で「全場」相当として扱う)
        selected_course = "全場"

    # ----- 過去データ統計 -----
    st.divider()
    st.subheader("📊 過去データ")

    # データ出所ラベルの日本語化テーブル
    SOURCE_LABEL = {
        "parquet": "本番(Parquet)",
        "csv_sample": "サンプル(CSV)",
    }

    # 過去データの読み込み(失敗してもアプリは続行)
    try:
        historical = get_historical()
        # テーブルごとにデータ出所を表示(混在運用に対応)
        for table_name, src in historical.sources.items():
            st.metric(table_name, SOURCE_LABEL.get(src, src))
        st.divider()
        st.metric("過去レース数", f"{historical.races['race_id'].nunique():,} レース")
        st.metric("登録馬数", f"{len(historical.horses):,} 頭")
    except FileNotFoundError as e:
        historical = None
        st.error(str(e))


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

        # 馬番(horse_number)は出馬表側にあるので、horse_id → 馬番 の引きを作る
        # 出馬表に horse_number 列が無ければ空 dict にして、後段で "—" 表示にフォールバック
        if "horse_number" in race_card_df.columns:
            hn_map = dict(zip(
                race_card_df["horse_id"].astype(str),
                race_card_df["horse_number"],
            ))
        else:
            hn_map = {}

        def _fmt_hn(horse_id: str) -> str:
            """馬番をテーブル表示用に整形(欠損は '—')"""
            v = hn_map.get(str(horse_id))
            if v is None or pd.isna(v) or v == "":
                return "—"
            try:
                return str(int(v))
            except (ValueError, TypeError):
                return str(v)

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
                    "馬番": _fmt_hn(pred.horse_id),
                    "horse_id": pred.horse_id,
                    "horse_name": pred.horse_name,
                    "jockey": pred.jockey,
                    "score": pred.score,
                    "reasons": " | ".join(pred.reasons),
                })
        download_df = pd.DataFrame(download_rows)

        # ===== CSVダウンロードボタン(UTF-8-sig で BOM 付与、Excel互換) =====
        csv_bytes = download_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="📥 予想結果を CSV でダウンロード",
            data=csv_bytes,
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
                        "馬番": _fmt_hn(p.horse_id),
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
                    mark_part = f"{p.mark} " if p.mark else "　 "
                    label = f"{mark_part}{_fmt_hn(p.horse_id)} {p.horse_name}({p.jockey})  スコア {p.score}"
                    with st.expander(label, expanded=False):
                        if p.reasons:
                            for r in p.reasons:
                                st.write(f"- {r}")
                        else:
                            st.write("(理由情報なし)")

elif race_card_df is None:
    st.info("👆 出馬表 CSV をアップロードしてください。")
