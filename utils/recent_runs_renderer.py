"""
1レース分の出走馬全頭について、直近5走戦歴マトリクスを HTML で描画する。

C1 では「縦3行(着順 / コース距離 / 上がり3F)+ 横5列(5走前→前走)」の
基本テーブル構造だけを実装。C2 で着順の色分け・サーフェスマッチ ★、
C3 で上がり3F の強調表示を追加する。

条件付きフォーマット(本ファイル単独で完結):
- 距離が当日と完全一致(±0m)した過去走 → 行頭(セル先頭の着順行)に ★ を付与
- ○ルール (Rule 9〜22) が 1 本でも発火する過去走 → 上3F 値+「秒」を緑文字で強調
  → 単純な閾値判定(旧 AGARI_THRESHOLD = 33.5)は廃止。芝/ダ・距離・馬場・
    場・通過順位改善 すべてを評価する utils.onmark_rules.matches_any_onmark_rule
    を再利用する(SSoT を本ロジック v1.0 に統一)。
"""

from __future__ import annotations

import html
from typing import Iterable

import pandas as pd
import streamlit as st

from utils.onmark_rules import matches_any_onmark_rule
from utils.race_history import get_recent_runs_for_race


# =====================================================================
# 表示用の色・マーク定数(マジックナンバー禁止)
# =====================================================================
# 緑文字の色値(Tailwind green-500 相当)
LAST3F_PASS_COLOR: str = "#22c55e"
# 距離完全一致を示すマーク文字(U+2605)
DISTANCE_MATCH_STAR: str = "★"


# =====================================================================
# CSS (テーブル構造 + 距離一致★ + 上3F緑強調)
# =====================================================================
_MATRIX_CSS = f"""
<style>
.recent-runs-matrix {{
    border-collapse: collapse;
    width: 100%;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 12px;
    margin-top: 8px;
}}
.recent-runs-matrix th {{
    background: rgba(255,255,255,0.05);
    padding: 6px 8px;
    text-align: center;
    font-weight: normal;
    border-bottom: 1px solid rgba(255,255,255,0.1);
    color: rgba(255,255,255,0.85);
}}
.recent-runs-matrix td {{
    padding: 0;
    text-align: center;
    border: 1px solid rgba(255,255,255,0.1);
    vertical-align: middle;
}}
.recent-runs-matrix .horse-label {{
    text-align: left !important;
    white-space: nowrap;
    padding: 8px 12px !important;
    font-weight: 500;
    color: #fff;
    min-width: 180px;
}}
.recent-runs-matrix .run-cell {{
    min-width: 110px;
}}
.recent-runs-matrix .run-cell .position {{
    padding: 4px 0;
    font-weight: bold;
}}
/* 通過順(着順の右に併記)。本文より一段階薄くしてノイズを抑える。 */
.recent-runs-matrix .run-cell .pass-order {{
    margin-left: 4px;
    font-size: 10px;
    font-weight: normal;
    color: rgba(255,255,255,0.65);
}}
.recent-runs-matrix .run-cell .course {{
    padding: 3px 0;
    font-size: 11px;
    color: rgba(255,255,255,0.85);
}}
.recent-runs-matrix .run-cell .last3f {{
    padding: 3px 0;
    font-size: 11px;
    color: rgba(255,255,255,0.7);
}}
/* 着順クラス(色塗り廃止 — 構造保持のためクラスは残し、見た目は背景・文字色とも既定) */
.recent-runs-matrix .pos-1-3,
.recent-runs-matrix .pos-4-6,
.recent-runs-matrix .pos-7-12,
.recent-runs-matrix .pos-13plus,
.recent-runs-matrix .pos-none {{
    background: transparent;
    color: inherit;
}}
/* 距離完全一致マーカー: 着順行の先頭に「★ 」を出す(行頭=セル上端) */
.recent-runs-matrix .distance-match-star {{
    color: #ffd54a;
    margin-right: 2px;
}}
/* ○ルール(R9〜R22)が 1 本でも該当 → 緑文字+太字。秒単位も同色に含める。
   ホバー時に title 属性(該当ルール ID)が tooltip として出る。

   ⚠ specificity 注意: `.recent-runs-matrix .run-cell .last3f` (0,3,0) が
   color: rgba(255,255,255,0.7) を持っているので、こちらも `.run-cell` を
   挟んで同じ (0,3,0) 以上に揃える必要がある。さもないとクラスは付くのに
   色だけ負ける(過去の本番事故あり)。 */
.recent-runs-matrix .run-cell .last3f-pass {{
    color: {LAST3F_PASS_COLOR};
    font-weight: bold;
    cursor: help;
}}
/* 凡例(色チップなし、テキストのみ) */
.recent-runs-matrix-legend {{
    font-size: 11px;
    margin-top: 6px;
    color: rgba(255,255,255,0.7);
}}
.recent-runs-matrix-legend .legend-tag {{
    display: inline-block;
    padding: 0 4px;
    margin-right: 4px;
    font-size: 11px;
    background: transparent;
    color: inherit;
    border: 1px solid rgba(255,255,255,0.2);
    border-radius: 2px;
}}
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


def _is_exact_distance_match(
    run_distance: int,
    run_surface: str,
    target_distance: int,
    target_surface: str,
) -> bool:
    """
    過去走が当日レースと「サーフェス(芝/ダ) + 距離」で完全一致するか。

    旧仕様は距離のみだったが、芝1200m と ダ1200m を同列に扱うのは混乱を生む
    ため、サーフェス一致も AND 条件で要求する。どちらかが空 / 0 / 不明なら
    False(欠損は照合対象外)。
    """
    if not run_distance or not target_distance:
        return False
    if not run_surface or not target_surface:
        return False
    return (run_distance == target_distance) and (run_surface == target_surface)


def _format_course_with_track(surface: str, distance: int, racecourse: str) -> str:
    """
    コース表記を「サーフェス + 距離 + (場名)」形式で組み立てる。

    括弧は spec 通り全角(U+FF08 / U+FF09)を使う。

    - "ダ", 1800, "阪神" → "ダ1800(阪神)"
    - "芝", 1600, ""     → "芝1600"            (場名欠損)
    - "",   0,    "東京" → "──"                (距離 + サーフェス両方欠損)
    """
    base = f"{surface}{distance}" if distance else (surface or "──")
    if base == "──":
        return base
    if racecourse:
        return f"{base}（{racecourse}）"
    return base


def _format_pass_order(run: dict) -> str:
    """
    通過順を "X-X-X-X" 形式の文字列に整形する。

    corner_1〜corner_4 の順で並べ、None / NaN / 0以下 はスキップして詰める
    (短距離レースは 3 コーナー以降しか記録がない、障害レースで全 0 等)。
    1 つも有効な値が無ければ空文字を返す(呼び出し側で表示自体を抑制)。
    """
    out: list[str] = []
    for k in ("corner_1", "corner_2", "corner_3", "corner_4"):
        v = run.get(k)
        if v is None:
            continue
        try:
            if pd.isna(v):
                continue
        except (TypeError, ValueError):
            pass
        try:
            iv = int(v)
        except (ValueError, TypeError):
            continue
        if iv < 1:
            continue
        out.append(str(iv))
    return "-".join(out)


def _build_run_cell(run: dict | None, target_surface: str, target_distance: int) -> str:
    """
    1走分のセル HTML を組み立てる(縦に 着順 / コース距離 / 上がり3F の 3 行)。

    条件付きフォーマット:
    - サーフェス + 距離 が当日レースと完全一致 → 着順行の冒頭に ★(行頭マーカー)
    - ○ルール (R9〜R22) のいずれかが発火 → 上3F 値を緑文字で強調 + tooltip に
      該当ルール ID を表示
    - 通過順位(corner_1..4)が有効 → 着順の右にハイフン区切りで併記
    - 開催場名 → コース行の末尾に「(○○)」で併記
    """
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

    # ----- 通過順位(着順の右に X-X-X[-X] 形式で表示) -----
    pass_order_str = _format_pass_order(run)
    pass_order_html = (
        f'<span class="pass-order">{html.escape(pass_order_str)}</span>'
        if pass_order_str else ""
    )

    # ----- コース・距離 + 場名 -----
    surface = str(run.get("surface", "") or "").strip()
    raw_distance = run.get("distance")
    try:
        distance = int(raw_distance) if pd.notna(raw_distance) else 0
    except (ValueError, TypeError):
        distance = 0
    racecourse = str(run.get("racecourse", "") or "").strip()
    course_str = _format_course_with_track(surface, distance, racecourse)

    # ----- ★ サーフェス+距離 完全一致 → 行頭マーカー -----
    distance_match = _is_exact_distance_match(
        distance, surface, target_distance, target_surface,
    )
    star_html = (
        f'<span class="distance-match-star">{DISTANCE_MATCH_STAR}</span> '
        if distance_match else ""
    )

    # ----- 上がり3F + 緑強調(○ルール R9〜R22 が 1 本でも該当する走) -----
    last_3f = run.get("last_3f")
    if last_3f is None or pd.isna(last_3f):
        last3f_str = "──"
        last3f_cls = ""
        last3f_title_attr = ""
    else:
        f = float(last_3f)
        last3f_str = f"{f:.1f}秒"
        is_pass, matched_rule_ids = matches_any_onmark_rule(run)
        if is_pass:
            last3f_cls = "last3f-pass"
            last3f_title_attr = (
                f' title="{html.escape(", ".join(matched_rule_ids))} 該当"'
            )
        else:
            last3f_cls = ""
            last3f_title_attr = ""
    last3f_class_attr = f"last3f {last3f_cls}".rstrip()

    return (
        '<td class="run-cell">'
        f'<div class="position {pos_cls}">'
            f'{star_html}{html.escape(pos_str)}{pass_order_html}'
        '</div>'
        f'<div class="course">{html.escape(course_str)}</div>'
        f'<div class="{last3f_class_attr}"{last3f_title_attr}>{html.escape(last3f_str)}</div>'
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
        "<th>前走</th><th>2走前</th><th>3走前</th><th>4走前</th><th>5走前</th>"
        "</tr></thead><tbody>"
    )

    for hid, mark, hn, name, _score in horse_meta:
        runs = history.get(hid, [None] * 5)
        # runs は [前走, 2走前, ..., 5走前] の直近順。表示も同じく左=前走、右=5走前。
        # 新聞・専門紙の戦歴と同じ並びで「直近の調子」を左端で素早く読める。

        label = _format_horse_label(mark, hn, name)
        parts.append("<tr>")
        parts.append(f'<td class="horse-label">{label}</td>')
        for run in runs:
            parts.append(_build_run_cell(run, target_surface, target_distance))
        parts.append("</tr>")

    parts.append("</tbody></table>")

    # ----- 凡例(距離一致★ + 上3F緑強調) -----
    parts.append(
        '<div class="recent-runs-matrix-legend">'
        "凡例: "
        '<span class="legend-tag">1-3着</span>'
        '<span class="legend-tag">4-6着</span>'
        '<span class="legend-tag">7-12着</span>'
        '<span class="legend-tag">13着以下</span>'
        '<span class="legend-tag">出走なし</span>'
        f" | <span class=\"distance-match-star\">{DISTANCE_MATCH_STAR}</span>"
        f" = 当日距離({target_distance}m)と完全一致"
        f" | <span class=\"last3f-pass\">緑文字</span>"
        " = ○ルール(R9〜R22)該当走 — ホバーで該当ルール ID 表示"
        "</div>"
    )

    st.markdown("".join(parts), unsafe_allow_html=True)
