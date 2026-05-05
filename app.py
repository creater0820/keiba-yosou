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

import datetime as dt
import hashlib

import pandas as pd
import plotly.express as px
import streamlit as st

from data_loader import (
    HistoricalData,
    load_historical_data,
    load_race_card_cached,
    summarize_race_card,
    validate_race_card,
)
from prediction_logic import HorsePrediction, predict_all_races_cached
from utils.recent_runs_renderer import render_recent_runs_matrix

# =====================================================================
# 画面全体の設定
# =====================================================================
st.set_page_config(
    page_title="競馬予想アプリ",
    page_icon="🏇",
    layout="wide",
)


# =====================================================================
# サイドバー用ヘルパ(競馬場フィルタのラベル組み立て・パース)
# =====================================================================
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def _format_course_label(racecourse: str, dates: list[dt.date]) -> str:
    """
    競馬場名 + 開催日リスト → ラジオボタン用ラベル。
    例: ('京都', [date(2026,5,3)])           → '京都 5/3(日)'
        ('京都', [date(2026,5,3), date(...)]) → '京都 5/3(日)/5/4(月)'
    """
    if not dates:
        return racecourse
    sorted_dates = sorted(set(dates))
    date_strs = [
        f"{d.month}/{d.day}({WEEKDAY_JA[d.weekday()]})"
        for d in sorted_dates
    ]
    return f"{racecourse} {'/'.join(date_strs)}"


def _parse_course_from_label(label: str) -> str:
    """
    ラベル文字列から競馬場名だけ取り出す。
    '京都 5/3(日)' → '京都'、'全場' → '全場'(下流コードがそのまま判定に使えるよう維持)
    """
    if label == "全場":
        return "全場"
    return label.split(" ", 1)[0]


# =====================================================================
# 描画ヘルパ
# =====================================================================
def render_predictions_section(
    *,
    all_predictions: dict[str, list[HorsePrediction]],
    race_card_df: pd.DataFrame,
    display_df: pd.DataFrame,
    selected_course: str,
    historical_races: pd.DataFrame,
) -> None:
    """
    予想結果セクションを描画する(成功メッセージ・CSVダウンロード・レース別エクスパンダ)。

    引数:
        all_predictions: ファイル全体の予想結果(race_id → 馬予想リスト)
        race_card_df:    アップロード時の出馬表全体(馬番 lookup 用、フィルタ前)
        display_df:      現フィルタ後の出馬表(表示対象 race_id を導出する)
        selected_course: サイドバー選択値("全場" or 場名)
        historical_races: 過去レースの DataFrame(直近5走戦歴マトリクス用)
    """
    # 表示対象 race_id で予想を絞る(計算済み結果からの派生なので瞬時)
    display_race_ids = set(display_df["race_id"].unique())
    display_predictions: dict[str, list[HorsePrediction]] = {
        rid: preds for rid, preds in all_predictions.items()
        if rid in display_race_ids
    }

    course_suffix = f" / {selected_course}のみ" if selected_course != "全場" else ""
    st.success(
        f"予想完了({len(display_predictions)} / {len(all_predictions)} "
        f"レース表示中{course_suffix})"
    )

    # 馬番マップ(全 race_card_df から)
    if "horse_number" in race_card_df.columns:
        hn_map = dict(zip(
            race_card_df["horse_id"].astype(str),
            race_card_df["horse_number"],
        ))
    else:
        hn_map = {}

    def _fmt_hn(horse_id: str) -> str:
        v = hn_map.get(str(horse_id))
        if v is None or pd.isna(v) or v == "":
            return "—"
        try:
            return str(int(v))
        except (ValueError, TypeError):
            return str(v)

    # ----- CSVダウンロード(現フィルタの予想のみ、UTF-8-sig) -----
    download_rows: list[dict] = []
    for race_id, preds in display_predictions.items():
        race_info_row = display_df[display_df["race_id"] == race_id].iloc[0]
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
    csv_bytes = download_df.to_csv(index=False).encode("utf-8-sig")
    file_suffix = f"_{selected_course}" if selected_course != "全場" else ""
    st.download_button(
        label="📥 予想結果を CSV でダウンロード",
        data=csv_bytes,
        file_name=f"prediction_results{file_suffix}.csv",
        mime="text/csv",
    )

    # ----- レースごとの結果表示 -----
    st.subheader("レースごとの予想")
    st.caption(
        "発走時刻は JRA 標準スケジュールから推定したもので、実際の発走時刻とは "
        "±10 分前後ズレることがあります。"
    )

    # 場 → 時刻 → R の自然順で並べ替える
    # post_time が無いレース(列欠損 or 範囲外)は最後に回す
    def _race_sort_key(race_id: str) -> tuple:
        row = display_df[display_df["race_id"] == race_id].iloc[0]
        course = str(row.get("racecourse", "") or "")
        time_str = str(row.get("post_time", "") or "")
        # 空文字は最後に来るよう "99:99" でフォールバック
        time_key = time_str if time_str else "99:99"
        try:
            rno = int(row.get("race_number") or 99)
        except (ValueError, TypeError):
            rno = 99
        return (course, time_key, rno)

    sorted_race_ids = sorted(display_predictions.keys(), key=_race_sort_key)

    for race_id in sorted_race_ids:
        preds = display_predictions[race_id]
        race_info_row = display_df[display_df["race_id"] == race_id].iloc[0]

        # 発走時刻(推定)を取り出してタイトルに添える
        post_time = str(race_info_row.get("post_time", "") or "").strip()
        post_time_part = f"  {post_time}発走" if post_time else ""

        # 本命馬(◎)を 1 頭抽出してタイトルにプレビュー表示
        honmei_pred = next((p for p in preds if p.mark == "◎"), None)
        honmei_text = f" — ◎ {honmei_pred.horse_name}" if honmei_pred is not None else ""

        title = (
            f"【{race_info_row.get('racecourse', '')} "
            f"{race_info_row.get('race_number', '')}R】 "
            f"{race_info_row.get('race_name', '')} "
            f"{race_info_row.get('distance', '')}m "
            f"{race_info_row.get('surface', '')}"
            f"{post_time_part}"
            f"{honmei_text}"
        )
        # 既定で閉じる(クリックで展開)。スクロール量削減のため。
        with st.expander(title, expanded=False):
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

            # 直近5走戦歴マトリクス(出走馬全頭の調子・適性を一望)
            with st.expander("📊 直近5走戦歴", expanded=False):
                race_card_for_this = display_df[display_df["race_id"] == race_id]
                render_recent_runs_matrix(race_card_for_this, preds, historical_races)

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

# 出馬表 CSV を DataFrame に変換(@st.cache_data でキャッシュ済み)
race_card_df: pd.DataFrame | None = None
source_name: str | None = None
file_hash: str | None = None
if uploaded is not None:
    file_bytes = uploaded.getvalue()
    file_hash = hashlib.md5(file_bytes).hexdigest()
    try:
        # 同じバイト列なら再パースをスキップ。フィルタ切り替え時の再計算回避の要。
        race_card_df = load_race_card_cached(file_bytes, uploaded.name)
        source_name = uploaded.name
    except Exception as e:
        st.error(f"CSV の読み込みに失敗しました: {e}")

# 別ファイルがアップロードされたら、前回の予想結果は破棄する
if file_hash is not None and st.session_state.get("predictions_for_file") != file_hash:
    st.session_state.pop("all_predictions", None)
    st.session_state.pop("predictions_for_file", None)


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
    # 動的に選択肢にし、各場の開催日も併記する(例: '京都 5/3(日)')。
    if race_card_df is not None and "racecourse" in race_card_df.columns:
        st.divider()
        st.subheader("📍 競馬場フィルタ")

        # 場 → 開催日(date のリスト)のマップを作る
        # race_date は文字列のことも datetime のこともあるので一旦 datetime に揃える
        _dates_series = pd.to_datetime(race_card_df["race_date"], errors="coerce").dt.date
        course_dates_map: dict[str, list[dt.date]] = (
            race_card_df.assign(_d=_dates_series)
            .dropna(subset=["_d", "racecourse"])
            .groupby("racecourse")["_d"]
            .apply(lambda s: sorted(set(s.tolist())))
            .to_dict()
        )

        course_options = ["全場"] + [
            _format_course_label(c, course_dates_map[c])
            for c in sorted(course_dates_map.keys())
        ]
        selected_label = st.radio(
            "表示する競馬場",
            course_options,
            index=0,
            key="course_filter",
        )
        # 下流のフィルタ判定は単純な場名で行うため、ラベルからパースして取り出す
        selected_course = _parse_course_from_label(selected_label)
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
# サイドバーで選んだ場でフィルタした DataFrame を以後は display_df として扱う。
# バリデーションだけは元の race_card_df(フィルタ前)に対して行う
# (列構成不整合は「全場」だろうと「東京のみ」だろうと同じ問題なので)。
if race_card_df is not None:
    # 列構成チェック(フィルタ前の生データに対して)
    validation = validate_race_card(race_card_df)
    if not validation.ok:
        st.error(validation.message)
        st.stop()  # 列が揃っていなければ予想実行に進ませない

    # 競馬場フィルタを適用
    if selected_course == "全場":
        display_df = race_card_df
    else:
        display_df = race_card_df[race_card_df["racecourse"] == selected_course].copy()

    # 読み込み完了メッセージ(フィルタ状態を併記)
    course_suffix = f" / {selected_course}のみ表示中" if selected_course != "全場" else ""
    st.success(f"読み込み完了: {source_name}{course_suffix}")

    # 概要メトリクス
    summary = summarize_race_card(display_df)
    col1, col2 = st.columns(2)
    metric_suffix = f"({selected_course}のみ)" if selected_course != "全場" else ""
    col1.metric("レース数", f"{summary['race_count']} レース{metric_suffix}")
    col2.metric("出走頭数", f"{summary['horse_count']} 頭{metric_suffix}")

    # 出馬表のプレビュー表(全件表示は重いので先頭のみ、フィルタ後)
    with st.expander("出馬表プレビュー(先頭20行)"):
        st.dataframe(display_df.head(20), use_container_width=True)


# =====================================================================
# 予想実行(全レース一括計算 → session_state 保存)
# =====================================================================
# ボタン押下時は **フィルタ前の race_card_df 全体** で予想計算する。
# フィルタはあくまで「表示」フィルタなので、計算済み結果から派生させる。
# これにより、ラジオ切替で再計算が走らず体感的に瞬時に絞り込める。
if race_card_df is not None and historical is not None:
    st.divider()
    if st.button("🎯 予想実行", type="primary", use_container_width=True):
        # キャッシュキーは file_hash。同じファイルなら計算済み結果が即返る。
        all_predictions = predict_all_races_cached(file_hash, race_card_df, historical)
        st.session_state["all_predictions"] = all_predictions
        st.session_state["predictions_for_file"] = file_hash

# =====================================================================
# 予想結果の描画(session_state にあれば、ボタン未クリックでも表示維持)
# =====================================================================
predictions_in_session = st.session_state.get("all_predictions")
if (predictions_in_session is not None
        and st.session_state.get("predictions_for_file") == file_hash
        and race_card_df is not None
        and historical is not None):
    render_predictions_section(
        all_predictions=predictions_in_session,
        race_card_df=race_card_df,
        display_df=display_df,
        selected_course=selected_course,
        historical_races=historical.races,
    )

elif race_card_df is None:
    st.info("👆 出馬表 CSV をアップロードしてください。")
