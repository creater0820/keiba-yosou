"""
過去レース DataFrame から、特定レース基準で各馬の直近 N 走を抽出するヘルパ。

「直近5走戦歴マトリクス」を描画するために、出走馬まとめて履歴をキャッシュ抽出する。
historical_df は約16万行と大きいので、レース毎に何度も filter するのではなく
1回 filter したものをグループ化して使い回す。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


@st.cache_data(show_spinner=False)
def get_recent_runs_for_race(
    horse_ids: tuple[str, ...],
    target_date_iso: str,
    _historical_df: pd.DataFrame,
    n: int = 5,
) -> dict[str, list[dict | None]]:
    """
    指定したレース日(target_date_iso)以前のレースで、各馬の直近 n 走を返す。

    引数:
        horse_ids: 馬IDのタプル。 Streamlit の cache_data はタプルをハッシュキーに使うため
                   list ではなく tuple で渡してもらう前提。
        target_date_iso: 基準レース日("YYYY-MM-DD" 文字列)。これより**前**の走のみ拾う。
        _historical_df: 過去レース DataFrame。先頭 _ で Streamlit のハッシュ対象から除外
                        (DataFrame はハッシュが重く、かつセッション内で同一インスタンス
                        を使い続ける前提なので除外しても安全)。
        n: 何走分まで取るか(既定 5)。

    戻り値:
        { horse_id: [run0, run1, ..., run{n-1}] }
        run0 が **直近(=前走)**、run{n-1} が **n 走前**(古い方)。
        該当走数が n に満たない馬は末尾を None でパディングして必ず長さ n にする。
        run の中身は historical_df の 1 行を to_dict("records") した dict。
    """
    # 馬がそもそも過去データに無い場合の早期リターン
    if not horse_ids:
        return {}

    relevant = _historical_df[_historical_df["horse_id"].isin(horse_ids)].copy()
    if relevant.empty:
        return {hid: [None] * n for hid in horse_ids}

    # 日付を正規化して target_date より前のみに絞る
    relevant["_race_date"] = pd.to_datetime(relevant["race_date"], errors="coerce")
    target = pd.Timestamp(target_date_iso)
    relevant = relevant[relevant["_race_date"] < target]
    relevant = relevant.sort_values("_race_date", ascending=False)

    # 馬ID 単位でグループ化してから先頭 n 件を取る方が、
    # 馬ごとに DataFrame を filter するより圧倒的に速い
    grouped = dict(list(relevant.groupby("horse_id")))

    result: dict[str, list[dict | None]] = {}
    for hid in horse_ids:
        if hid in grouped:
            past_n = grouped[hid].head(n).drop(columns=["_race_date"])
            runs: list[dict | None] = past_n.to_dict("records")
        else:
            runs = []
        # 不足分は None で末尾パディング
        while len(runs) < n:
            runs.append(None)
        result[hid] = runs
    return result


def get_recent_n_runs(
    horse_id: str,
    target_race_date,
    historical_df: pd.DataFrame,
    n: int = 5,
) -> list[dict | None]:
    """
    単一馬の直近 N 走を返す(デバッグ・スポットチェック用ラッパ)。

    内部では get_recent_runs_for_race(キャッシュ済み) を呼ぶだけ。
    target_race_date は str / pd.Timestamp / datetime のいずれでも可。

    戻り値: [前走, 2走前, ..., N走前] 直近順、不足は None で末尾パディング。
    """
    target_iso = pd.Timestamp(target_race_date).strftime("%Y-%m-%d")
    return get_recent_runs_for_race(
        (str(horse_id),), target_iso, historical_df, n=n,
    )[str(horse_id)]


# =====================================================================
# 脚質判定(本ロジック v1.0 の Step 1 / Phase 2 で使う)
# =====================================================================
# CLAUDE.md「推奨馬選定ロジック(本ロジック v1.0)/ 脚質の判定基準」より:
#   過去5走の初角(1コーナー)通過順位の平均で判定:
#     平均 1〜3番手   → 逃げ
#     平均 4〜6番手   → 先行
#     平均 7〜10番手  → 差し
#     平均 11番手以下 → 追込
#     過去走3走未満   → 不明(暫定で先行扱い)

RUNNING_STYLES = ("逃げ", "先行", "差し", "追込", "不明(先行扱い)")


def determine_running_style(past_runs: list[dict | None]) -> str:
    """
    過去 N 走(get_recent_n_runs の戻り値想定、直近順)から脚質を判定する。

    引数:
        past_runs: [前走, 2走前, ..., N走前] のリスト。各要素は dict or None。
                   各 dict は corner_1 を含む historical の1行をそのまま想定。

    戻り値:
        "逃げ" / "先行" / "差し" / "追込" / "不明(先行扱い)" のいずれか。

    判定ロジック:
        - 直近5走から corner_1 が有効値(NaN以外)のものだけ拾う
        - 拾えたサンプル数 < 3 → "不明(先行扱い)"
        - それ以上なら平均値で 4 区分(仕様書通り)
    """
    if not past_runs:
        return "不明(先行扱い)"

    # 直近5走に絞る(余分があっても先頭5件)
    head5 = past_runs[:5]

    valid_corners: list[float] = []
    for r in head5:
        if r is None:
            continue
        c1 = r.get("corner_1")
        if c1 is None or pd.isna(c1):
            continue
        try:
            valid_corners.append(float(c1))
        except (ValueError, TypeError):
            continue

    if len(valid_corners) < 3:
        return "不明(先行扱い)"

    avg = sum(valid_corners) / len(valid_corners)
    if avg <= 3:
        return "逃げ"
    if avg <= 6:
        return "先行"
    if avg <= 10:
        return "差し"
    return "追込"
