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
    font-size: 14px;
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
    min-width: 270px;
}}
/* 馬名ラベル内の当日ジョッキー(前走と同一なら通常色) */
.recent-runs-matrix .horse-label .jockey-today {{
    margin-left: 4px;
    font-size: 13px;
    color: rgba(255,255,255,0.7);
    font-weight: normal;
}}
/* 当日ジョッキーが前走と異なる時の赤字強調 */
.recent-runs-matrix .horse-label .jockey-changed {{
    color: #ef4444;
    font-weight: bold;
}}
.recent-runs-matrix .run-cell {{
    min-width: 130px;
}}
.recent-runs-matrix .run-cell .position {{
    padding: 4px 0;
    font-size: 15px;
    font-weight: bold;
}}
/* 通過順(着順の右に併記)。本文より一段階薄くしてノイズを抑える。 */
.recent-runs-matrix .run-cell .pass-order {{
    margin-left: 4px;
    font-size: 12px;
    font-weight: normal;
    color: rgba(255,255,255,0.65);
}}
.recent-runs-matrix .run-cell .course {{
    padding: 3px 0;
    font-size: 13px;
    color: rgba(255,255,255,0.85);
}}
.recent-runs-matrix .run-cell .last3f {{
    padding: 3px 0;
    font-size: 13px;
    color: rgba(255,255,255,0.7);
}}
/* 過去走セルの 4 行目: ジョッキー名(漢字数文字想定、はみ出しは省略) */
.recent-runs-matrix .run-cell .jockey {{
    padding: 3px 0;
    font-size: 13px;
    color: rgba(255,255,255,0.7);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 130px;
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
    font-size: 13px;
    margin-top: 6px;
    color: rgba(255,255,255,0.7);
}}
.recent-runs-matrix-legend .legend-tag {{
    display: inline-block;
    padding: 0 4px;
    margin-right: 4px;
    font-size: 13px;
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
def _is_blank_jockey(value) -> bool:
    """jockey 値が None / NaN / 空文字 / "(不明)" のいずれかか判定する。"""
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return s == "" or s == "(不明)"


def _is_jockey_changed(today_jockey: str | None, prev_jockey: str | None) -> bool:
    """当日 jockey と前走 jockey が異なるか(両方 valid のときのみ判定)。"""
    if _is_blank_jockey(today_jockey) or _is_blank_jockey(prev_jockey):
        return False
    return str(today_jockey).strip() != str(prev_jockey).strip()


def _format_horse_label(
    mark: str,
    horse_number,
    horse_name: str,
    today_jockey: str | None = None,
    jockey_changed: bool = False,
) -> str:
    """
    '◎ 14 キミガスキダ (北村友一)' 形式の馬ラベル(HTML エスケープ済み)。

    today_jockey:
        - 値があれば「(jockey)」を末尾に追加
        - 欠損なら「(不明)」表示
        - 与えられない(None)場合はジョッキー部分を出さない
    jockey_changed:
        True なら jockey 部分に jockey-changed クラスを付け赤字強調する。
    """
    if pd.isna(horse_number):
        hn_str = "—"
    else:
        try:
            hn_str = str(int(horse_number))
        except (ValueError, TypeError):
            hn_str = str(horse_number)
    mark_part = mark if mark else "&nbsp;&nbsp;"
    safe_name = html.escape(str(horse_name))
    base = f"{mark_part} {hn_str} {safe_name}"

    if today_jockey is None:
        return base

    # 欠損時はラベル「(不明)」、それ以外は「(jockey)」(括弧は span 側で1組のみ)
    inside = "不明" if _is_blank_jockey(today_jockey) else str(today_jockey).strip()
    cls = "jockey-today"
    if jockey_changed:
        cls = "jockey-today jockey-changed"
    return (
        f"{base}<span class=\"{cls}\">"
        f"({html.escape(inside)})"
        "</span>"
    )


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
            '<div class="jockey">──</div>'
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

    # ----- ジョッキー(4 行目) -----
    raw_jockey = run.get("jockey")
    jockey_str = "(不明)" if _is_blank_jockey(raw_jockey) else str(raw_jockey).strip()

    return (
        '<td class="run-cell">'
        f'<div class="position {pos_cls}">'
            f'{star_html}{html.escape(pos_str)}{pass_order_html}'
        '</div>'
        f'<div class="course">{html.escape(course_str)}</div>'
        f'<div class="{last3f_class_attr}"{last3f_title_attr}>{html.escape(last3f_str)}</div>'
        f'<div class="jockey">{html.escape(jockey_str)}</div>'
        "</td>"
    )


# =====================================================================
# 公開エントリポイント
# =====================================================================
@st.cache_data(show_spinner=False)
def _build_matrix_html_cached(
    cache_key: str,
    _race_card_df: pd.DataFrame,
    _predictions: list,
    _historical_df: pd.DataFrame,
) -> str:
    """マトリクス HTML 構築のキャッシュ版(perf)。

    cache_key には file_hash + going + race_id を渡す前提。同一なら
    HTML 文字列を再構築せず即返す。Streamlit の st.expander は collapsed
    でも中身を実行するため、34 レース分の HTML 構築コストが毎 rerun で
    かかっていた問題への対策。

    引数:
        cache_key: ハッシュ対象。file_hash + going + race_id 等の合成キー。
        _race_card_df / _predictions / _historical_df: ハッシュ対象外
            (`_` prefix)。pickle 経由で値は保持される。

    fix(history): 旧実装は predictions を `(horse_id, mark, score)` タプル
    のリストに変換していたが、消費側 `_build_matrix_html` が attribute
    access(`p.horse_id`)を使っていたため AttributeError で落ちていた。
    HorsePrediction は dataclass なので Streamlit の cache pickle で
    そのまま保持できるため、変換を撤去し object のまま渡す方式に修正。
    """
    return _build_matrix_html(
        _race_card_df, _predictions, _historical_df,
    )


def _build_matrix_html(
    race_card_df: pd.DataFrame,
    predictions: Iterable,
    historical_df: pd.DataFrame,
) -> str:
    """マトリクス HTML 文字列を構築して返す(pure 関数、Streamlit 出力なし)。"""
    if race_card_df.empty:
        return ""

    target_date_iso = str(race_card_df["race_date"].iloc[0])
    target_surface = str(race_card_df["surface"].iloc[0])
    try:
        target_distance = int(race_card_df["distance"].iloc[0])
    except (ValueError, TypeError):
        target_distance = 0

    # 印・スコアを horse_id でひける map にする
    pred_by_id = {str(p.horse_id): p for p in predictions}

    # 当日のジョッキーを horse_id 単位で引ける dict に
    today_jockey_by_id: dict[str, str] = {}
    if "jockey" in race_card_df.columns:
        for _, row in race_card_df.iterrows():
            today_jockey_by_id[str(row["horse_id"])] = str(row.get("jockey", "") or "").strip()

    # スコア降順に並べる(◎が一番上)
    horse_meta: list[tuple[str, str, object, str, float]] = []
    for _, row in race_card_df.iterrows():
        hid = str(row["horse_id"])
        pred = pred_by_id.get(hid)
        mark = pred.mark if pred is not None else ""
        score = pred.score if pred is not None else 0.0
        horse_meta.append((hid, mark, row.get("horse_number"), row["horse_name"], score))
    horse_meta.sort(key=lambda x: -x[4])

    # 履歴の取得経路:
    # 1. DC 形式の race_card_df.attrs["dc_past_runs"] が優先(DC ファイル同梱)
    # 2. それ以外は historical_df から血統登録番号で引き当て
    horse_ids_tuple = tuple(m[0] for m in horse_meta)
    dc_past_runs = race_card_df.attrs.get("dc_past_runs")
    if dc_past_runs:
        history = {hid: dc_past_runs.get(hid, [None] * 5) for hid in horse_ids_tuple}
    else:
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

        # ----- 当日のジョッキー + 前走比較で赤字判定 -----
        today_jockey = today_jockey_by_id.get(hid)
        prev_run = runs[0] if runs else None
        prev_jockey = prev_run.get("jockey") if isinstance(prev_run, dict) else None
        jockey_changed = _is_jockey_changed(today_jockey, prev_jockey)

        label = _format_horse_label(
            mark, hn, name,
            today_jockey=today_jockey,
            jockey_changed=jockey_changed,
        )
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

    return "".join(parts)


def render_recent_runs_matrix(
    race_card_df: pd.DataFrame,
    predictions: Iterable,
    historical_df: pd.DataFrame,
    *,
    cache_key: str | None = None,
) -> None:
    """
    1レース分の出走馬全頭について、直近5走戦歴マトリクスを Streamlit に描画する。

    引数:
        race_card_df: 当該レースの出馬表 DataFrame(1行=1出走馬)
        predictions: そのレースの HorsePrediction リスト(印・スコア取得用)
        historical_df: 過去レース DataFrame(履歴抽出元)
        cache_key (perf): 渡されると HTML 構築結果を @st.cache_data で
                          メモ化する。呼び出し側は file_hash + going + race_id
                          等の組合せ文字列を渡す。None なら毎回再構築。
    """
    if race_card_df.empty:
        return

    # predictions は HorsePrediction(dataclass)のリスト想定。
    # @st.cache_data の `_` prefix 引数で hash 対象外にしつつ pickle で値を
    # 保持できるので、変換せずそのまま渡す。
    # 旧実装の payload tuple 変換はここで AttributeError を起こしていた
    # (消費側の _build_matrix_html が `p.horse_id` を attribute access する)。
    predictions_list = list(predictions)
    try:
        if cache_key:
            html = _build_matrix_html_cached(
                cache_key, race_card_df, predictions_list, historical_df,
            )
        else:
            html = _build_matrix_html(
                race_card_df, predictions_list, historical_df,
            )
    except (AttributeError, TypeError, KeyError) as e:
        # cache 周りの異常で落ちた場合でも予想結果セクション全体は止めない。
        # cache 抜きで再構築を試み、それもダメなら静かに描画スキップ。
        st.caption(
            f"(直近5走戦歴マトリクスの描画でエラー: {type(e).__name__})"
        )
        try:
            html = _build_matrix_html(
                race_card_df, predictions_list, historical_df,
            )
        except Exception:
            html = ""
    if html:
        st.markdown(html, unsafe_allow_html=True)
