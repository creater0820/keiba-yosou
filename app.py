"""
Streamlit エントリーポイント(本ロジック v1.0 UI)。

UI セクション構成(各レース内):
  1) 本命・注目馬     ◎本命/準◎準本命 + ○ 数別ランキング
  2) ワイド候補       WC1〜WC3 + 該当ルール理由
  3) 危険人気馬       減点ルール(R6, R7)該当馬 + 警告
  4) 推奨買い目       単勝・複勝・馬連・三連複・ワイド の全券種
  5) 直近5走戦歴      Phase 1 のマトリクス(既存維持)
  6) 全頭の○マーク詳細  全頭 + 該当ルール

データ層: utils/{onmark_rules, judgment_engine, betting_strategy} 経由で
prediction_logic.predict_all_races_cached の戻り値(dict[race_id → RacePrediction])
を消費する。スコア値は表示しない(Phase 5 で廃止)。

起動:
    streamlit run app.py
"""

from __future__ import annotations

import datetime as dt
import hashlib

import pandas as pd
import streamlit as st

from data_loader import (
    HistoricalData,
    load_historical_data,
    load_race_card_cached,
    summarize_race_card,
    validate_race_card,
)
from prediction_logic import (
    HorsePrediction,  # noqa: F401  (互換 shim、未使用)
    RacePrediction,
    predict_all_races_cached,
)
from utils.recent_runs_renderer import render_recent_runs_matrix


# =====================================================================
# 画面全体の設定
# =====================================================================
st.set_page_config(
    page_title="競馬予想アプリ(本ロジック v1.0)",
    page_icon="🏇",
    layout="wide",
)


# =====================================================================
# サイドバー用ヘルパ(競馬場フィルタのラベル組み立て・パース)
# =====================================================================
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def _format_course_label(racecourse: str, dates: list[dt.date]) -> str:
    if not dates:
        return racecourse
    sorted_dates = sorted(set(dates))
    date_strs = [
        f"{d.month}/{d.day}({WEEKDAY_JA[d.weekday()]})"
        for d in sorted_dates
    ]
    return f"{racecourse} {'/'.join(date_strs)}"


def _parse_course_from_label(label: str) -> str:
    if label == "全場":
        return "全場"
    return label.split(" ", 1)[0]


# =====================================================================
# 描画ヘルパ群
# =====================================================================

def _format_horse_label(prefix: str, horse_number: int, horse_name: str) -> str:
    return f"{prefix} 馬番{int(horse_number)} {horse_name}"


def _format_horse_runtime(horse) -> str:
    """HorseMarkData の脚質 + 人気 を 1 行で。"""
    pop_str = f"{horse.popularity}人気" if horse.popularity > 0 else "人気不明"
    return f"{horse.running_style} / {pop_str}"


def _render_section_main_pick(pred: RacePrediction) -> None:
    """セクション 1: 本命・注目馬"""
    st.markdown("**🏆 本命・注目馬**")
    j = pred.judgment

    # 本命 or 準本命
    if j.main_pick:
        axis = next((h for h in pred.horses if h.horse_id == j.main_pick), None)
        if axis:
            st.success(
                f"◎本命: 馬番{axis.horse_number} **{axis.horse_name}** "
                f"({_format_horse_runtime(axis)}) ○{axis.marks_count}個"
            )
    elif j.sub_pick:
        sub = next((h for h in pred.horses if h.horse_id == j.sub_pick), None)
        if sub:
            st.warning(
                f"準◎: 馬番{sub.horse_number} **{sub.horse_name}** "
                f"({_format_horse_runtime(sub)}) ○{sub.marks_count}個 "
                f"※○≥5 の本命候補なし"
            )
    else:
        st.info("該当馬なし(全頭減点で軸馬決定不能)")

    st.caption(f"判定: {j.reason}")

    # ○3 以上の注目馬テーブル(本命除く)
    axis_id = j.main_pick or j.sub_pick
    notables = [
        h for h in sorted(pred.horses, key=lambda x: -x.marks_count)
        if h.horse_id != axis_id and h.marks_count >= 1
    ][:6]
    if notables:
        rows = [
            {
                "○": h.marks_count,
                "馬番": h.horse_number,
                "馬名": h.horse_name,
                "脚質": h.running_style,
                "人気": h.popularity if h.popularity > 0 else "",
            }
            for h in notables
        ]
        st.markdown("注目馬(○ ≥ 1):")
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _render_section_wide(pred: RacePrediction) -> None:
    """セクション 2: ワイド候補"""
    st.markdown("**🎯 ワイド候補(最大3頭)**")
    if not pred.wide_candidates:
        st.write("該当馬なし(R3/R4/R5/R8 のいずれも該当せず)")
        return
    for i, w in enumerate(pred.wide_candidates, 1):
        st.markdown(
            f"- **WC{i}**: 馬番{w.horse_number} {w.horse_name}"
            f"({w.popularity}人気)"
        )
        for r in w.reasons:
            st.caption(f"    ↳ {r}")


def _render_section_demerit(pred: RacePrediction) -> None:
    """セクション 3: 危険人気馬"""
    st.markdown("**⚠️ 危険人気馬(減点)**")
    if not pred.demerit_entries:
        st.write("該当馬なし")
        return
    for d in pred.demerit_entries:
        st.error(
            f"⚠ 馬番{d.horse_number} {d.horse_name} ({d.rule_id}) "
            f"→ {d.downgrade_to}着以下扱い:{d.reason}"
        )


def _render_section_betting(pred: RacePrediction) -> None:
    """セクション 4: 推奨買い目"""
    st.markdown("**💴 推奨買い目**")
    bp = pred.betting
    if not bp.tickets:
        st.write(f"({bp.main_horse_label}) 軸馬決定不能 / 候補不足のため買い目なし")
        return
    st.caption(f"軸: {bp.main_horse_label}")
    rows = [
        {
            "券種": t.bet_type,
            "馬番": "-".join(str(n) for n in t.horse_numbers),
            "馬名": " / ".join(t.horse_names),
            "備考": t.note or "",
        }
        for t in bp.tickets
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _render_section_all_marks(pred: RacePrediction) -> None:
    """セクション 6: 全頭の○マーク詳細"""
    with st.expander("全頭の○マーク詳細", expanded=False):
        for h in sorted(pred.horses, key=lambda x: (-x.marks_count, x.horse_number)):
            mark_label = ""
            if h.horse_id == pred.judgment.main_pick:
                mark_label = "◎ "
            elif h.horse_id == pred.judgment.sub_pick:
                mark_label = "準◎ "
            head = (
                f"{mark_label}馬番{h.horse_number} {h.horse_name} "
                f"({_format_horse_runtime(h)}) ○{h.marks_count}個"
            )
            if h.matched_rules:
                with st.expander(head, expanded=False):
                    for r in h.matched_rules:
                        st.write(f"- {r}")
            else:
                st.write(head + "  — 該当ルールなし")


def _expander_title(pred: RacePrediction) -> str:
    """expander タイトル: 場 + R + レース名 + 距離 + 芝/ダ + 発走時刻 + ◎/準◎ + ○数"""
    m = pred.race_meta
    base = (
        f"【{m.get('racecourse','')} {m.get('race_number','')}R】 "
        f"{m.get('race_name','')} {m.get('distance','')}m {m.get('surface','')}"
    )
    pt = m.get("post_time", "")
    if pt:
        base += f"  {pt}発走"

    j = pred.judgment
    if j.main_pick:
        h = next((x for x in pred.horses if x.horse_id == j.main_pick), None)
        if h:
            base += f" — ◎{h.horse_name} ○{h.marks_count}"
    elif j.sub_pick:
        h = next((x for x in pred.horses if x.horse_id == j.sub_pick), None)
        if h:
            base += f" — 準◎{h.horse_name} ○{h.marks_count}"
    return base


def render_predictions_section(
    *,
    all_predictions: dict[str, RacePrediction],
    race_card_df: pd.DataFrame,
    display_df: pd.DataFrame,
    selected_course: str,
    historical_races: pd.DataFrame,
) -> None:
    """v1.0 予想結果のメイン領域を描画。"""
    # 表示対象 race_id でフィルタ
    display_race_ids = set(display_df["race_id"].unique())
    display_predictions: dict[str, RacePrediction] = {
        rid: p for rid, p in all_predictions.items() if rid in display_race_ids
    }

    course_suffix = f" / {selected_course}のみ" if selected_course != "全場" else ""
    st.success(
        f"予想完了({len(display_predictions)} / {len(all_predictions)} "
        f"レース表示中{course_suffix})"
    )
    st.caption("ロジック: **本ロジック v1.0**(○マーク収集 → 本命判定 → ワイド抽出 → 買い目)")

    # ----- レース一覧サマリ統計 -----
    n_honmei = sum(1 for p in display_predictions.values() if p.judgment.main_pick)
    n_subpick = sum(1 for p in display_predictions.values() if p.judgment.sub_pick and not p.judgment.main_pick)
    n_with_wides = sum(1 for p in display_predictions.values() if p.wide_candidates)
    n_with_demerit = sum(1 for p in display_predictions.values() if p.demerit_entries)
    st.caption(
        f"統計: ◎本命確定 {n_honmei} レース / 準◎のみ {n_subpick} レース / "
        f"ワイド候補あり {n_with_wides} レース / 危険人気馬あり {n_with_demerit} レース"
    )

    # ----- CSV ダウンロード(本命+ワイド+減点 を行展開) -----
    download_rows: list[dict] = []
    for race_id, p in display_predictions.items():
        m = p.race_meta
        # 軸馬
        axis_id = p.judgment.main_pick or p.judgment.sub_pick
        axis = next((h for h in p.horses if h.horse_id == axis_id), None) if axis_id else None
        download_rows.append({
            "race_id": race_id,
            "racecourse": m.get("racecourse", ""),
            "race_number": m.get("race_number", ""),
            "race_name": m.get("race_name", ""),
            "区分": "◎" if p.judgment.main_pick else ("準◎" if p.judgment.sub_pick else "(なし)"),
            "馬番": axis.horse_number if axis else "",
            "馬名": axis.horse_name if axis else "",
            "○": axis.marks_count if axis else 0,
            "脚質": axis.running_style if axis else "",
            "人気": axis.popularity if axis and axis.popularity > 0 else "",
        })
        for i, w in enumerate(p.wide_candidates, 1):
            download_rows.append({
                "race_id": race_id,
                "racecourse": m.get("racecourse", ""),
                "race_number": m.get("race_number", ""),
                "race_name": m.get("race_name", ""),
                "区分": f"WC{i}",
                "馬番": w.horse_number,
                "馬名": w.horse_name,
                "○": "",
                "脚質": "",
                "人気": w.popularity,
            })
        for d in p.demerit_entries:
            download_rows.append({
                "race_id": race_id,
                "racecourse": m.get("racecourse", ""),
                "race_number": m.get("race_number", ""),
                "race_name": m.get("race_name", ""),
                "区分": f"危険({d.rule_id})",
                "馬番": d.horse_number,
                "馬名": d.horse_name,
                "○": "",
                "脚質": "",
                "人気": "",
            })
    if download_rows:
        download_df = pd.DataFrame(download_rows)
        csv_bytes = download_df.to_csv(index=False).encode("utf-8-sig")
        file_suffix = f"_{selected_course}" if selected_course != "全場" else ""
        st.download_button(
            label="📥 v1.0 予想結果を CSV でダウンロード",
            data=csv_bytes,
            file_name=f"prediction_v1{file_suffix}.csv",
            mime="text/csv",
        )

    # ----- レースごとの結果表示 -----
    st.subheader("レースごとの予想")
    st.caption(
        "発走時刻は JRA 標準スケジュールから推定したもので、"
        "実際の発走時刻とは ±10 分前後ズレることがあります。"
    )

    # 並び順: 場 → 発走時刻 → R番
    def _sort_key(rid: str) -> tuple:
        p = display_predictions[rid]
        m = p.race_meta
        return (
            m.get("racecourse", ""),
            m.get("post_time", "") or "99:99",
            m.get("race_number", 99),
        )

    for race_id in sorted(display_predictions.keys(), key=_sort_key):
        p = display_predictions[race_id]
        with st.expander(_expander_title(p), expanded=False):
            _render_section_main_pick(p)
            st.divider()
            _render_section_wide(p)
            st.divider()
            _render_section_demerit(p)
            st.divider()
            _render_section_betting(p)
            st.divider()

            # 直近5走戦歴(Phase 1 のマトリクス)
            with st.expander("📊 直近5走戦歴", expanded=False):
                race_card_for_this = display_df[display_df["race_id"] == race_id]
                # マトリクスは旧 HorsePrediction 風の入力を期待していたので、
                # RacePrediction の horses を擬似的に薄ラッパで渡す。
                pseudo_preds = [
                    HorsePrediction(
                        horse_id=h.horse_id,
                        horse_name=h.horse_name,
                        jockey="",  # マトリクスは jockey を表示しない
                        score=float(h.marks_count),
                        mark="◎" if h.horse_id == p.judgment.main_pick else
                             ("○" if h.horse_id == p.judgment.sub_pick else ""),
                        reasons=h.matched_rules,
                    )
                    for h in p.horses
                ]
                render_recent_runs_matrix(race_card_for_this, pseudo_preds, historical_races)

            _render_section_all_marks(p)


# =====================================================================
# 過去データの読み込み
# =====================================================================
HISTORICAL_DATA_SCHEMA_VERSION = "v3-corner-positions"


@st.cache_data(show_spinner="過去データを読み込み中…")
def get_historical(_schema_version: str = HISTORICAL_DATA_SCHEMA_VERSION) -> HistoricalData:
    return load_historical_data()


# =====================================================================
# メイン領域 上部: タイトル + 出馬表アップロード
# =====================================================================
st.title("🏇 競馬予想アプリ(本ロジック v1.0)")
st.caption("当日の出馬表 CSV をアップロードして「予想実行」を押してください。")

uploaded = st.file_uploader(
    "当日出馬表 CSV をアップロード",
    type=["csv"],
    accept_multiple_files=False,
    help="JV-Link または TARGET frontier JV からエクスポートした CSV を想定。",
)

race_card_df: pd.DataFrame | None = None
source_name: str | None = None
file_hash: str | None = None
if uploaded is not None:
    file_bytes = uploaded.getvalue()
    file_hash = hashlib.md5(file_bytes).hexdigest()
    try:
        race_card_df = load_race_card_cached(file_bytes, uploaded.name)
        source_name = uploaded.name
    except Exception as e:
        st.error(f"CSV の読み込みに失敗しました: {e}")

# 別ファイルがアップロードされたら、前回の予想結果は破棄
if file_hash is not None and st.session_state.get("predictions_for_file") != file_hash:
    st.session_state.pop("all_predictions", None)
    st.session_state.pop("predictions_for_file", None)


# =====================================================================
# サイドバー: アプリ説明 + 競馬場フィルタ + 過去データ統計
# =====================================================================
with st.sidebar:
    st.title("🏇 競馬予想アプリ")
    st.caption("JRA中央競馬・本ロジック v1.0")

    st.markdown(
        """
        ### 使い方
        1. 当日の出馬表 CSV をアップロード
        2. 「予想実行」ボタンを押す
        3. 各レースで ◎本命 / ワイド候補 / 危険人気馬 を確認
        4. 推奨買い目を参考に
        """
    )

    if race_card_df is not None and "racecourse" in race_card_df.columns:
        st.divider()
        st.subheader("📍 競馬場フィルタ")
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
        selected_course = _parse_course_from_label(selected_label)
    else:
        selected_course = "全場"

    st.divider()
    st.subheader("📊 過去データ")
    SOURCE_LABEL = {"parquet": "本番(Parquet)", "csv_sample": "サンプル(CSV)"}
    try:
        historical = get_historical()
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
    validation = validate_race_card(race_card_df)
    if not validation.ok:
        st.error(validation.message)
        st.stop()

    if selected_course == "全場":
        display_df = race_card_df
    else:
        display_df = race_card_df[race_card_df["racecourse"] == selected_course].copy()

    course_suffix = f" / {selected_course}のみ表示中" if selected_course != "全場" else ""
    st.success(f"読み込み完了: {source_name}{course_suffix}")

    summary = summarize_race_card(display_df)
    col1, col2 = st.columns(2)
    metric_suffix = f"({selected_course}のみ)" if selected_course != "全場" else ""
    col1.metric("レース数", f"{summary['race_count']} レース{metric_suffix}")
    col2.metric("出走頭数", f"{summary['horse_count']} 頭{metric_suffix}")

    with st.expander("出馬表プレビュー(先頭20行)"):
        st.dataframe(display_df.head(20), use_container_width=True)


# =====================================================================
# 予想実行
# =====================================================================
if race_card_df is not None and historical is not None:
    st.divider()
    if st.button("🎯 予想実行(本ロジック v1.0)", type="primary", use_container_width=True):
        all_predictions = predict_all_races_cached(file_hash, race_card_df, historical)
        st.session_state["all_predictions"] = all_predictions
        st.session_state["predictions_for_file"] = file_hash


# =====================================================================
# 予想結果の描画
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
