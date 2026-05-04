"""
予想(predictions/*.json)と実結果(data/historical/races.parquet)の突合。

責務:
- predictions DataFrame と races DataFrame を race_id + horse_id でジョイン
- 印別(◎○▲△)の連対率・複勝率を計算
- レースサマリ(◎単勝的中、複勝的中、三連複的中等)を計算
- hit_history/results.parquet として永続化(増分上書き)

設計:
- Streamlit に依存しない pandas のみのモジュール
- 突合はピュア関数 match_predictions_to_results(predictions, races) で完結
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# 突合済み履歴の保存先(リポジトリにコミット対象)
HIT_HISTORY_DIR = Path("hit_history")
HIT_HISTORY_PATH = HIT_HISTORY_DIR / "results.parquet"

# 印の優先順(◎>○>▲>△)
MARK_ORDER = ["◎", "○", "▲", "△"]


# =====================================================================
# 突合: predictions × races
# =====================================================================

def match_predictions_to_results(
    predictions_df: pd.DataFrame,
    races_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    予想(推奨馬1頭=1行)に各馬の実着順を付与する。

    返す列(predictions_df の全列に加えて):
        actual_finishing_position : 実際の着順(<NA> なら結果未確定 or 過去データに無い)
        race_field_size           : そのレースの出走頭数
        hit_win                   : 1着なら 1、それ以外 0(未確定 NaN)
        hit_place                 : 2着以内なら 1
        hit_show                  : 3着以内なら 1
        is_resolved               : そのレースの結果が races_df に揃っていれば True
    """
    if predictions_df.empty:
        return predictions_df.copy()

    # races の必要列だけ抽出してジョイン用に整える
    needed = ["race_id", "horse_id", "finishing_position"]
    if not all(c in races_df.columns for c in needed):
        raise ValueError(f"races_df に必須列が不足: 期待 {needed}, 実 {list(races_df.columns)}")

    races_subset = races_df[needed].copy()
    races_subset["horse_id"] = races_subset["horse_id"].astype(str)

    # レース毎の出走頭数(field_size)を計算
    field_size = (
        races_subset.groupby("race_id")["horse_id"].nunique()
        .rename("race_field_size").reset_index()
    )

    pred = predictions_df.copy()
    pred["horse_id"] = pred["horse_id"].astype(str)

    merged = pred.merge(
        races_subset.rename(columns={"finishing_position": "actual_finishing_position"}),
        on=["race_id", "horse_id"],
        how="left",
    ).merge(field_size, on="race_id", how="left")

    # is_resolved: そのレース全体が races_df に存在するか(field_size の有無で判定)
    merged["is_resolved"] = merged["race_field_size"].notna()

    # 着順ベースの的中フラグ。未確定は NaN を維持して的中率の分母を歪めない。
    pos = merged["actual_finishing_position"]
    merged["hit_win"] = pos.where(pos.isna(), (pos == 1).astype("Int64"))
    merged["hit_place"] = pos.where(pos.isna(), (pos <= 2).astype("Int64"))
    merged["hit_show"] = pos.where(pos.isna(), (pos <= 3).astype("Int64"))

    return merged


# =====================================================================
# 集計: 印別パフォーマンス
# =====================================================================

def per_mark_performance(matched_df: pd.DataFrame) -> pd.DataFrame:
    """
    印別の出現数・着回数・各種率を返す。

    返す列: mark, n, wins, places(1-2着延べ), shows(1-3着延べ),
           win_rate, place_rate, show_rate
    """
    resolved = matched_df[matched_df["is_resolved"]].copy()
    if resolved.empty:
        return pd.DataFrame(columns=[
            "mark", "n", "wins", "places", "shows", "win_rate", "place_rate", "show_rate",
        ])

    rows = []
    for mark in MARK_ORDER:
        sub = resolved[resolved["mark"] == mark]
        n = len(sub)
        if n == 0:
            rows.append({"mark": mark, "n": 0, "wins": 0, "places": 0, "shows": 0,
                         "win_rate": 0.0, "place_rate": 0.0, "show_rate": 0.0})
            continue
        wins = int((sub["actual_finishing_position"] == 1).sum())
        places = int((sub["actual_finishing_position"] <= 2).sum())
        shows = int((sub["actual_finishing_position"] <= 3).sum())
        rows.append({
            "mark": mark,
            "n": n,
            "wins": wins,
            "places": places,
            "shows": shows,
            "win_rate": wins / n,
            "place_rate": places / n,
            "show_rate": shows / n,
        })
    return pd.DataFrame(rows)


# =====================================================================
# 集計: レース単位サマリ(三連複的中など)
# =====================================================================

def race_summary(matched_df: pd.DataFrame) -> pd.DataFrame:
    """
    レース毎の予想vs結果サマリを返す。

    返す列: race_id, race_date, racecourse, race_number, race_name,
           is_resolved,
           honmei_horse_name, honmei_finishing_position,
           hit_honmei_win, hit_honmei_show,
           hit_2head_in_top3 (◎○▲ のうち2頭以上が3着以内なら 1)
    """
    if matched_df.empty:
        return pd.DataFrame()

    races: list[dict] = []
    group_cols = ["race_id", "race_date", "racecourse", "race_number", "race_name"]
    for keys, sub in matched_df.groupby(group_cols, sort=False):
        is_resolved = bool(sub["is_resolved"].any())

        # 本命 (◎) 行
        honmei_rows = sub[sub["mark"] == "◎"]
        honmei = honmei_rows.iloc[0] if not honmei_rows.empty else None

        # ◎○▲ の3頭が3着以内に何頭入ったか
        top3_marks = sub[sub["mark"].isin(["◎", "○", "▲"])]
        in_top3_count = int((top3_marks["actual_finishing_position"] <= 3).fillna(False).sum())

        races.append({
            "race_id": keys[0],
            "race_date": keys[1],
            "racecourse": keys[2],
            "race_number": keys[3],
            "race_name": keys[4],
            "is_resolved": is_resolved,
            "honmei_horse_name": honmei["horse_name"] if honmei is not None else "",
            "honmei_finishing_position": honmei["actual_finishing_position"] if honmei is not None else pd.NA,
            "hit_honmei_win": int(honmei["hit_win"]) if honmei is not None and pd.notna(honmei["hit_win"]) else pd.NA,
            "hit_honmei_show": int(honmei["hit_show"]) if honmei is not None and pd.notna(honmei["hit_show"]) else pd.NA,
            "hit_2head_in_top3": 1 if (is_resolved and in_top3_count >= 2) else (0 if is_resolved else pd.NA),
        })
    return pd.DataFrame(races)


# =====================================================================
# 永続化(hit_history/results.parquet)
# =====================================================================

def save_hit_history(matched_df: pd.DataFrame) -> Path:
    """突合済み DataFrame を hit_history/results.parquet に保存する(全件上書き)。"""
    HIT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    matched_df.to_parquet(HIT_HISTORY_PATH, index=False)
    return HIT_HISTORY_PATH


def load_hit_history() -> pd.DataFrame:
    """hit_history/results.parquet を読み込む(無ければ空 DataFrame)。"""
    if not HIT_HISTORY_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(HIT_HISTORY_PATH)
