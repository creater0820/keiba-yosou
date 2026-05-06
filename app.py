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
# 文字サイズ調整(お父様の老眼配慮、4 段階切替)
# =====================================================================
# サイドバー最上部のスライダで session_state["font_scale"] を変更すると、
# 下記の CSS が再注入されて UI 全体の文字サイズが切り替わる。
# 「標準」では CSS を注入せず、Streamlit デフォルトを維持する。
FONT_SCALE_OPTIONS = ["標準", "大", "特大", "最大"]

FONT_SCALE_CSS: dict[str, str] = {
    # 標準: 戦歴マトリクスの baseline を utils/recent_runs_renderer.py で
    #       既定値として定義済み(matrix=14 / position=15 / course/last3f/jockey=13
    #       / pass-order=12 / horse-label=14 / 凡例=13)。
    #       スライダ「標準」では何も注入しない(現行 baseline 維持)。
    "標準": "",
    # 大: matrix を +1px 持ち上げ
    #
    # CSS specificity 注意: utils/recent_runs_renderer.py の _MATRIX_CSS は
    # 描画時(後)に注入され、`.recent-runs-matrix` 系セレクタを 0,1,0〜0,3,0
    # で持っている。本ファイルの FONT_SCALE_CSS は script 先頭で注入される
    # ので、後勝ちで _MATRIX_CSS に上書きされてしまう。これを防ぐため、
    # 全セレクタの先頭に `html` を付けて 0,1,1 以上に上げる(html は最上位の
    # 祖先で常に存在する)。
    "大": """
        <style>
        html { font-size: 17.6px; }
        html [data-testid="stSidebar"] { font-size: 15px; }
        html .recent-runs-matrix { font-size: 15px; }
        html .recent-runs-matrix .horse-label { font-size: 15px; min-width: 290px; }
        html .recent-runs-matrix .run-cell { min-width: 140px; }
        html .recent-runs-matrix .run-cell .position { font-size: 16px; }
        html .recent-runs-matrix .run-cell .course,
        html .recent-runs-matrix .run-cell .last3f,
        html .recent-runs-matrix .run-cell .jockey { font-size: 14px; }
        html .recent-runs-matrix .run-cell .pass-order { font-size: 13px; }
        html .recent-runs-matrix-legend { font-size: 14px; }
        </style>
    """,
    # 特大: matrix を +2px
    "特大": """
        <style>
        html { font-size: 19.2px; }
        html [data-testid="stSidebar"] { font-size: 17px; }
        html .recent-runs-matrix { font-size: 16px; }
        html .recent-runs-matrix .horse-label { font-size: 16px; min-width: 310px; }
        html .recent-runs-matrix .run-cell { min-width: 150px; }
        html .recent-runs-matrix .run-cell .position { font-size: 17px; }
        html .recent-runs-matrix .run-cell .course,
        html .recent-runs-matrix .run-cell .last3f,
        html .recent-runs-matrix .run-cell .jockey { font-size: 15px; }
        html .recent-runs-matrix .run-cell .pass-order { font-size: 14px; }
        html .recent-runs-matrix-legend { font-size: 15px; }
        </style>
    """,
    # 最大: matrix を +3px
    "最大": """
        <style>
        html { font-size: 20.8px; }
        html [data-testid="stSidebar"] { font-size: 18px; }
        html .recent-runs-matrix { font-size: 17px; }
        html .recent-runs-matrix .horse-label { font-size: 18px; min-width: 340px; }
        html .recent-runs-matrix .run-cell { min-width: 160px; }
        html .recent-runs-matrix .run-cell .position { font-size: 18px; }
        html .recent-runs-matrix .run-cell .course,
        html .recent-runs-matrix .run-cell .last3f,
        html .recent-runs-matrix .run-cell .jockey { font-size: 16px; }
        html .recent-runs-matrix .run-cell .pass-order { font-size: 15px; }
        html .recent-runs-matrix-legend { font-size: 16px; }
        </style>
    """,
}

# session_state からスケールを取得して CSS を注入(set_page_config の直後)。
# スライダ操作時は Streamlit が自動 rerun するので、次回 rerun でこの行が
# 新しい値で再評価され CSS が更新される。
_current_font_scale = st.session_state.get("font_scale", "標準")
if _current_font_scale not in FONT_SCALE_CSS:
    _current_font_scale = "標準"
if FONT_SCALE_CSS[_current_font_scale]:
    st.markdown(FONT_SCALE_CSS[_current_font_scale], unsafe_allow_html=True)


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


def _is_rating_mode(pred: RacePrediction) -> bool:
    return getattr(pred, "logic_mode", "onmark") == "rating"


def _score_label(pred: RacePrediction, marks_or_rating: int) -> str:
    """ロジックモードに応じた表記を返す。"""
    if _is_rating_mode(pred):
        return f"rate {marks_or_rating}"
    return f"○{marks_or_rating}個"


def _horse_score(pred: RacePrediction, horse_id: str) -> int:
    """rating モードなら horse_ratings から、それ以外は horses.marks_count から。"""
    if _is_rating_mode(pred):
        h = next((x for x in pred.horse_ratings if x.horse_id == horse_id), None)
        return h.total_rating if h else 0
    h = next((x for x in pred.horses if x.horse_id == horse_id), None)
    return h.marks_count if h else 0


def _render_section_main_pick(pred: RacePrediction) -> None:
    """セクション 1: 本命・注目馬"""
    st.markdown("**🏆 本命・注目馬**")
    j = pred.judgment
    rating_mode = _is_rating_mode(pred)

    # 本命 or 準本命
    if j.main_pick:
        axis = next((h for h in pred.horses if h.horse_id == j.main_pick), None)
        if axis:
            score = _horse_score(pred, axis.horse_id)
            st.success(
                f"◎本命: 馬番{axis.horse_number} **{axis.horse_name}** "
                f"({_format_horse_runtime(axis)}) {_score_label(pred, score)}"
            )
    elif j.sub_pick:
        sub = next((h for h in pred.horses if h.horse_id == j.sub_pick), None)
        if sub:
            score = _horse_score(pred, sub.horse_id)
            note = "※rating ≥ 100 の本命候補なし" if rating_mode else "※○≥5 の本命候補なし"
            st.warning(
                f"準◎: 馬番{sub.horse_number} **{sub.horse_name}** "
                f"({_format_horse_runtime(sub)}) {_score_label(pred, score)} "
                f"{note}"
            )
    else:
        st.info("該当馬なし(全頭減点で軸馬決定不能)")

    st.caption(f"判定: {j.reason}")

    # rating モードでは内訳を expander で見せる
    if rating_mode and (j.main_pick or j.sub_pick):
        axis_id = j.main_pick or j.sub_pick
        rating_obj = next((r for r in pred.horse_ratings if r.horse_id == axis_id), None)
        if rating_obj and rating_obj.matched:
            with st.expander(
                f"⚙ 軸馬の rating 内訳 (合計 {rating_obj.total_rating} 点)",
                expanded=False,
            ):
                for hit in rating_obj.matched:
                    st.write(f"- **{hit.rule_id}** (+{hit.rate}): {hit.reason}")

    # 注目馬テーブル(本命除く、rating モード: rating 上位、onmark: ○数上位)
    axis_id = j.main_pick or j.sub_pick
    if rating_mode:
        sorted_notables = sorted(pred.horse_ratings, key=lambda x: -x.total_rating)
        notables = [h for h in sorted_notables if h.horse_id != axis_id and h.total_rating >= 1][:6]
        if notables:
            rows = [
                {
                    "rate": h.total_rating,
                    "馬番": h.horse_number,
                    "馬名": h.horse_name,
                    "脚質": h.running_style,
                    "人気": h.popularity if h.popularity > 0 else "",
                }
                for h in notables
            ]
            st.markdown("注目馬(rate ≥ 1):")
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
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
    """セクション 6: 全頭の rating / ○マーク詳細"""
    rating_mode = _is_rating_mode(pred)
    section_title = "全頭の rating 詳細" if rating_mode else "全頭の○マーク詳細"

    with st.expander(section_title, expanded=False):
        if rating_mode:
            for r in sorted(pred.horse_ratings,
                             key=lambda x: (-x.total_rating, x.horse_number)):
                mark_label = ""
                if r.horse_id == pred.judgment.main_pick:
                    mark_label = "◎ "
                elif r.horse_id == pred.judgment.sub_pick:
                    mark_label = "準◎ "
                pop_str = f"{r.popularity}人気" if r.popularity > 0 else "人気不明"
                head = (
                    f"{mark_label}馬番{r.horse_number} {r.horse_name} "
                    f"({r.running_style} / {pop_str}) rate {r.total_rating}"
                )
                if r.matched:
                    with st.expander(head, expanded=False):
                        for hit in r.matched:
                            st.write(f"- **{hit.rule_id}** (+{hit.rate}): {hit.reason}")
                        if r.rule24_active:
                            st.caption("📌 F2 救済発動(2,3走前で評価)")
                else:
                    st.write(head + "  — 該当ルールなし")
        else:
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
            score = _horse_score(pred, h.horse_id)
            base += f" — ◎{h.horse_name} {_score_label(pred, score)}"
    elif j.sub_pick:
        h = next((x for x in pred.horses if x.horse_id == j.sub_pick), None)
        if h:
            score = _horse_score(pred, h.horse_id)
            base += f" — 準◎{h.horse_name} {_score_label(pred, score)}"
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
    # ロジックモードを先頭レースから判定して表示
    sample_pred = next(iter(display_predictions.values()), None)
    if sample_pred and getattr(sample_pred, "logic_mode", "onmark") == "rating":
        st.caption(
            "ロジック: **本ロジック v1.1 (rating-based)** — C/D/E/F1/F2/F3 評価で "
            "rating ≥ 100 を ◎本命に確定。"
        )
    else:
        st.caption(
            "ロジック: **本ロジック v1.0** "
            "(○マーク収集 → 本命判定 → ワイド抽出 → 買い目)"
        )

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
        # ◎本命確定(rating ≥ 100)のレースは expander タイトル全体を緑強調する
        # ことで、スクロール中に統計バナー記載の確定レース数を一目で識別可能に。
        # _expander_title() の戻り値自体は変えず、ラッパのみで装飾する。
        title = _expander_title(p)
        if p.judgment.main_pick is not None:
            title = f":green[{title}]"
        with st.expander(title, expanded=False):
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
HISTORICAL_DATA_SCHEMA_VERSION = "v4-rating-engine"


@st.cache_data(show_spinner="過去データを読み込み中…")
def get_historical(_schema_version: str = HISTORICAL_DATA_SCHEMA_VERSION) -> HistoricalData:
    return load_historical_data()


# =====================================================================
# メイン領域 上部: タイトル + 出馬表アップロード
# =====================================================================
st.title("🏇 競馬予想アプリ(本ロジック v1.0)")
st.caption("当日の出馬表 CSV をアップロードして「予想実行」を押してください。")

# 別ページ(ロジック説明 等)に遷移しても CSV を保持するため、
# アップロード内容(bytes/name/hash)を session_state に永続化する。
# Streamlit の file_uploader 単体ではページ遷移後に状態が空になるケースがある。
SS_FILE_BYTES = "uploaded_csv_bytes"
SS_FILE_NAME = "uploaded_csv_name"
SS_FILE_HASH = "uploaded_csv_hash"

uploaded = st.file_uploader(
    "当日出馬表 CSV をアップロード",
    type=["csv"],
    accept_multiple_files=False,
    key="race_card_uploader",
    help="JV-Link または TARGET frontier JV からエクスポートした CSV を想定。",
)

race_card_df: pd.DataFrame | None = None
source_name: str | None = None
file_hash: str | None = None
file_bytes: bytes | None = None
restored_from_session = False

if uploaded is not None:
    # 新規アップロード(または同一セッション内の再表示) → セッションに保存
    file_bytes = uploaded.getvalue()
    file_hash = hashlib.md5(file_bytes).hexdigest()
    source_name = uploaded.name
    st.session_state[SS_FILE_BYTES] = file_bytes
    st.session_state[SS_FILE_NAME] = source_name
    st.session_state[SS_FILE_HASH] = file_hash
elif st.session_state.get(SS_FILE_BYTES) is not None:
    # 別ページから戻ってきた → uploader は空だが session に履歴あるので復元
    file_bytes = st.session_state[SS_FILE_BYTES]
    file_hash = st.session_state[SS_FILE_HASH]
    source_name = st.session_state[SS_FILE_NAME]
    restored_from_session = True

# race_card_df を構築(新規 / 復元 共通)
if file_bytes is not None:
    try:
        race_card_df = load_race_card_cached(file_bytes, source_name or "uploaded.csv")
    except Exception as e:
        st.error(f"CSV の読み込みに失敗しました: {e}")

# 別ファイルがアップロードされたら、前回の予想結果は破棄
if file_hash is not None and st.session_state.get("predictions_for_file") != file_hash:
    if uploaded is not None:
        # 新規アップロードの時のみ予想を破棄(復元時は既存の予想を残したい)
        st.session_state.pop("all_predictions", None)
        st.session_state.pop("predictions_for_file", None)


# =====================================================================
# サイドバー: アプリ説明 + 競馬場フィルタ + 過去データ統計
# =====================================================================
with st.sidebar:
    # ----- 文字サイズ調整スライダ(最上部、常時表示) -----
    # session_state["font_scale"] に保存され、レース絞り込みや予想実行を跨いで
    # 維持される。スクリプト先頭の CSS 注入が次回 rerun 時に新しい値で
    # 再評価される。
    st.subheader("🔤 文字サイズ")
    st.select_slider(
        label="表示倍率",
        options=FONT_SCALE_OPTIONS,
        value=st.session_state.get("font_scale", "標準"),
        key="font_scale",
        label_visibility="collapsed",
        help="老眼配慮の段階調整。「標準」=現行サイズ、最大で約 1.3 倍。",
    )
    st.divider()

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

    # ====================================================================
    # 🌟 推奨馬 (rating ≥ 100) ─ 競馬場フィルタの直下に配置
    # ====================================================================
    # 予想実行済みかつ rating モードの結果がセッションにある場合のみ表示。
    # 競馬場フィルタの選択値で絞り込み連動する。
    _session_preds = st.session_state.get("all_predictions")
    if _session_preds and race_card_df is not None:
        st.divider()
        st.subheader("🌟 推奨馬 (rating ≥ 100)")
        # 馬番→ jockey 引きマップ(race_card_df から)
        _jockey_by_hid: dict[str, str] = {}
        if "jockey" in race_card_df.columns:
            for _, _row in race_card_df.iterrows():
                _jockey_by_hid[str(_row["horse_id"])] = (
                    str(_row.get("jockey", "") or "").strip()
                )

        recs: list[dict] = []
        for _rid, _pred in _session_preds.items():
            ratings = getattr(_pred, "horse_ratings", None) or []
            if not ratings:
                continue  # onmark モード等
            _meta = _pred.race_meta
            _course = _meta.get("racecourse", "")
            if selected_course != "全場" and _course != selected_course:
                continue
            for _h in ratings:
                if _h.total_rating < 100:
                    continue
                _jockey = _jockey_by_hid.get(_h.horse_id, "") or "(不明)"
                if not _jockey.strip():
                    _jockey = "(不明)"
                recs.append({
                    "course": _course,
                    "race_number": int(_meta.get("race_number") or 0),
                    "post_time": _meta.get("post_time", ""),
                    "horse_number": _h.horse_number,
                    "horse_name": _h.horse_name,
                    "rating": _h.total_rating,
                    "jockey": _jockey,
                })

        # 並び順: post_time 昇順 → R番昇順
        recs.sort(key=lambda r: (r["post_time"] or "99:99", r["race_number"]))

        if recs:
            for r in recs:
                # tooltip に rating 値を載せる(コンパクトなまま親切表示)
                _line = (
                    f"<span title='rating {r['rating']}'>"
                    f"{r['course']}{r['race_number']}R "
                    f"<b>{r['horse_number']} {r['horse_name']}</b>"
                    f"({r['jockey']})"
                    f"</span>"
                )
                st.markdown(_line, unsafe_allow_html=True)
        else:
            st.caption("該当馬なし(rating 100 以上の馬がいません)")

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
    if restored_from_session:
        msg_col, btn_col = st.columns([5, 1])
        msg_col.info(
            f"📂 セッションから復元: {source_name}{course_suffix}"
            "(別ページから戻った時はアップロード履歴を再利用しています)"
        )
        if btn_col.button("🗑 クリア", help="アップロード履歴と予想結果を消す"):
            for k in (SS_FILE_BYTES, SS_FILE_NAME, SS_FILE_HASH,
                       "all_predictions", "predictions_for_file"):
                st.session_state.pop(k, None)
            st.rerun()
    else:
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
        # サイドバーは script の先頭付近で描画されるので、その時点で
        # session_state["all_predictions"] を読めるよう即座に rerun する。
        # (予想実行ボタンはサイドバーより後ろにあるため、この rerun を挟まないと
        # 「🌟 推奨馬」セクションが 1 回分の click 直後には見えない)
        st.rerun()


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
