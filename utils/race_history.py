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
