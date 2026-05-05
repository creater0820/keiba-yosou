"""
1レース分の出走馬全頭について、直近5走戦歴マトリクスを HTML で描画する。

C1 では「縦3行(着順 / コース距離 / 上がり3F)+ 横5列(5走前→前走)」の
基本テーブル構造だけを実装。C2 で着順の色分け・サーフェスマッチ ★、
C3 で上がり3F の強調表示を追加する。
"""

from __future__ import annotations

import html
from typing import Iterable

import pandas as pd
import streamlit as st

from utils.race_history import get_recent_runs_for_race


# =====================================================================
# CSS (テーブル構造のみ。色・★・3F強調は後続コミットで追加)
# =====================================================================
_MATRIX_CSS = """
<style>
.recent-runs-matrix {
    border-collapse: collapse;
    width: 100%;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 12px;
    margin-top: 8px;
}
.recent-runs-matrix th {
    background: rgba(255,255,255,0.05);
    padding: 6px 8px;
    text-align: center;
    font-weight: normal;
    border-bottom: 1px solid rgba(255,255,255,0.1);
    color: rgba(255,255,255,0.85);
}
.recent-runs-matrix td {
    padding: 0;
    text-align: center;
    border: 1px solid rgba(255,255,255,0.1);
    vertical-align: middle;
}
.recent-runs-matrix .horse-label {
    text-align: left !important;
    white-space: nowrap;
    padding: 8px 12px !important;
    font-weight: 500;
    color: #fff;
    min-width: 180px;
}
.recent-runs-matrix .run-cell {
    min-width: 90px;
}
.recent-runs-matrix .run-cell .position {
    padding: 4px 0;
    font-weight: bold;
}
.recent-runs-matrix .run-cell .course {
    padding: 3px 0;
    font-size: 11px;
    color: rgba(255,255,255,0.85);
}
.recent-runs-matrix .run-cell .last3f {
    padding: 3px 0;
    font-size: 11px;
    color: rgba(255,255,255,0.7);
}
/* 着順背景色 (1-3=緑 / 4-6=黄 / 7-12=橙 / 13+=赤 / 出走なし=灰) */
.recent-runs-matrix .pos-1-3    { background: #4CAF50; color: #fff; }
.recent-runs-matrix .pos-4-6    { background: #FFC107; color: #000; }
.recent-runs-matrix .pos-7-12   { background: #FF9800; color: #fff; }
.recent-runs-matrix .pos-13plus { background: #F44336; color: #fff; }
.recent-runs-matrix .pos-none   { background: #424242; color: #999; }
/* サーフェス一致マーカー: ★(同 芝/ダ) / ★★(同 芝/ダ + 距離±200m) */
.recent-runs-matrix .surface-match::after          { content: " ★";  color: #FFD700; }
.recent-runs-matrix .surface-distance-match::after { content: " ★★"; color: #FFD700; }
/* 上がり3F の評価別文字スタイル(33秒台前半=好末脚 / 35秒台以降=遅い) */
.recent-runs-matrix .last3f-fast { font-weight: bold; color: #66BB6A; }
.recent-runs-matrix .last3f-slow { color: #999;      font-weight: 300; }
/* 凡例タグ */
.recent-runs-matrix-legend {
    font-size: 11px;
    margin-top: 6px;
    color: rgba(255,255,255,0.7);
}
.recent-runs-matrix-legend .legend-tag {
    display: inline-block;
    padding: 2px 6px;
    margin-right: 4px;
    font-size: 11px;
    border-radius: 2px;
}
</style>
"""


# =====================================================================
# 内部ヘルパ
# =====================================================================
def _format_horse_label(mark: str, horse_number, horse_name: str) -> str:
    """ '◎ 14 キミガスキダ' 形式の馬ラベル文字列(HTMLエスケープ済み)。"""
    if pd.isna(horse_number):
        hn_str = "—"
    else:
        try:
            hn_str = str(int(horse_number))
        except (ValueError, TypeError):
            hn_str = str(horse_number)
    mark_part = mark if mark else "&nbsp;&nbsp;"
    safe_name = html.escape(str(horse_name))
    return f"{mark_part} {hn_str} {safe_name}"


def _position_class(pos_value) -> str:
    """着順値 → CSS クラス名(色分け用)。NaN や非数なら pos-none。"""
    if pos_value is None or pd.isna(pos_value):
        return "pos-none"
    try:
        p = int(pos_value)
    except (ValueError, TypeError):
        return "pos-none"
    if p <= 3:
        return "pos-1-3"
    if p <= 6:
        return "pos-4-6"
    if p <= 12:
        return "pos-7-12"
    return "pos-13plus"


def _course_match_class(
    run_surface: str, run_distance: int, target_surface: str, target_distance: int
) -> str:
    """
    今回レースとの「サーフェス一致」「距離一致」を表す CSS クラス。
    - 同芝・同ダート + 距離±200m → "surface-distance-match" (★★)
    - 同芝・同ダート              → "surface-match"          (★)
    - 一致しない                  → ""                       (マーク無し)
    """
    if not run_surface or not target_surface or run_surface != target_surface:
        return ""
    if not run_distance or not target_distance:
        # 距離不明なら一致のみ判定
        return "surface-match"
    if abs(run_distance - target_distance) <= 200:
        return "surface-distance-match"
    return "surface-match"


def _build_run_cell(run: dict | None, target_surface: str, target_distance: int) -> str:
    """1走分のセル HTML を組み立てる(縦に 着順 / コース距離 / 上がり3F の 3 行)。"""
    if run is None:
        return (
            '<td class="run-cell">'
            '<div class="position pos-none">──</div>'
            '<div class="course">出走なし</div>'
            '<div class="last3f">──</div>'
            "</td>"
        )

    # ----- 着順 -----
    pos = run.get("finishing_position")
    if pos is None or pd.isna(pos):
        pos_str = "──"
    else:
        try:
            pos_str = f"{int(pos)}着"
        except (ValueError, TypeError):
            pos_str = "──"
    pos_cls = _position_class(pos)

    # ----- コース・距離 -----
    surface = str(run.get("surface", "") or "").strip()
    raw_distance = run.get("distance")
    try:
        distance = int(raw_distance) if pd.notna(raw_distance) else 0
    except (ValueError, TypeError):
        distance = 0
    course_str = f"{surface}{distance}" if distance else surface or "──"
    course_cls = _course_match_class(surface, distance, target_surface, target_distance)
    course_class_attr = f"course {course_cls}".rstrip()

    # ----- 上がり3F -----
    last_3f = run.get("last_3f")
    last3f_cls = ""
    if last_3f is None or pd.isna(last_3f):
        last3f_str = "──"
    else:
        f = float(last_3f)
        last3f_str = f"{f:.1f}"
        if f < 33.5:
            last3f_cls = "last3f-fast"   # 33秒台前半 → 好末脚(太字緑)
        elif f >= 35.0:
            last3f_cls = "last3f-slow"   # 35秒以上  → 鈍い(淡灰)
    last3f_class_attr = f"last3f {last3f_cls}".rstrip()

    return (
        '<td class="run-cell">'
        f'<div class="position {pos_cls}">{html.escape(pos_str)}</div>'
        f'<div class="{course_class_attr}">{html.escape(course_str)}</div>'
        f'<div class="{last3f_class_attr}">{html.escape(last3f_str)}</div>'
        "</td>"
    )


# =====================================================================
# 公開エントリポイント
# =====================================================================
def render_recent_runs_matrix(
    race_card_df: pd.DataFrame,
    predictions: Iterable,
    historical_df: pd.DataFrame,
) -> None:
    """
    1レース分の出走馬全頭について、直近5走戦歴マトリクスを Streamlit に描画する。

    引数:
        race_card_df: 当該レースの出馬表 DataFrame(1行=1出走馬)
        predictions: そのレースの HorsePrediction リスト(印・スコア取得用)
        historical_df: 過去レース DataFrame(履歴抽出元)
    """
    if race_card_df.empty:
        return

    target_date_iso = str(race_card_df["race_date"].iloc[0])
    target_surface = str(race_card_df["surface"].iloc[0])
    try:
        target_distance = int(race_card_df["distance"].iloc[0])
    except (ValueError, TypeError):
        target_distance = 0

    # 印・スコアを horse_id でひける map にする
    pred_by_id = {str(p.horse_id): p for p in predictions}

    # スコア降順に並べる(◎が一番上)
    horse_meta: list[tuple[str, str, object, str, float]] = []
    for _, row in race_card_df.iterrows():
        hid = str(row["horse_id"])
        pred = pred_by_id.get(hid)
        mark = pred.mark if pred is not None else ""
        score = pred.score if pred is not None else 0.0
        horse_meta.append((hid, mark, row.get("horse_number"), row["horse_name"], score))
    horse_meta.sort(key=lambda x: -x[4])

    # 履歴を一括キャッシュ取得(同じレースを2回開いても再計算されない)
    horse_ids_tuple = tuple(m[0] for m in horse_meta)
    history = get_recent_runs_for_race(
        horse_ids_tuple, target_date_iso, historical_df, n=5
    )

    # ----- HTML 組み立て -----
    parts: list[str] = [_MATRIX_CSS, '<table class="recent-runs-matrix">']
    parts.append(
        "<thead><tr><th></th>"
        "<th>5走前</th><th>4走前</th><th>3走前</th><th>2走前</th><th>前走</th>"
        "</tr></thead><tbody>"
    )

    for hid, mark, hn, name, _score in horse_meta:
        runs = history.get(hid, [None] * 5)
        # runs は [前走, 2走前, ..., 5走前] の順なので、表示順 [5走前, 4走前, ..., 前走] に反転
        runs_display = list(reversed(runs))

        label = _format_horse_label(mark, hn, name)
        parts.append("<tr>")
        parts.append(f'<td class="horse-label">{label}</td>')
        for run in runs_display:
            parts.append(_build_run_cell(run, target_surface, target_distance))
        parts.append("</tr>")

    parts.append("</tbody></table>")

    # ----- 凡例(着順カラーチップ + ★ サーフェスマッチの説明) -----
    parts.append(
        '<div class="recent-runs-matrix-legend">'
        "凡例: "
        '<span class="legend-tag pos-1-3">1-3着</span>'
        '<span class="legend-tag pos-4-6">4-6着</span>'
        '<span class="legend-tag pos-7-12">7-12着</span>'
        '<span class="legend-tag pos-13plus">13着以下</span>'
        '<span class="legend-tag pos-none">出走なし</span>'
        " | <span style=\"color:#FFD700\">★</span> = 今回と同じ芝/ダ"
        " / <span style=\"color:#FFD700\">★★</span> = 同 芝/ダ + 距離±200m"
        "</div>"
    )

    st.markdown("".join(parts), unsafe_allow_html=True)
