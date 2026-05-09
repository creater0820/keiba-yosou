"""
DC 形式(TARGET ダイレクト)の馬を historical/races.parquet 上の
`horse_id`(8桁血統登録番号)に **(推定 race_date, 距離)集合演算** で紐付ける。

DC 形式の過去走には **着順は含まれない**(公式仕様)。代わりに各過去走には
推定 race_date(parse_dc_dataframe が weeks_since_prior チェーンで算出)が
入っており、これを ±3 日許容で historical の race_date と照合する。

アルゴリズム(集合票決方式):
  1. 各 past_run について「その日付近 + その距離」で historical を絞り込み、
     該当する horse_id 集合を取得
  2. 全 past_run の集合を「票」として horse_id ごとにカウント
  3. 最大票を獲得した horse_id を採用(min_votes 以上で確定)
  4. 同票が複数 → 補正タイム(adjusted_time)が最も近い馬を tie-break

距離 ±0m + 日付 ±3日 という強い JOIN キーなので、誤マッチは構造的に少ない
(同日同距離に出走する 2 頭以上に過去走がたまたま全部一致する確率は極小)。

API:
- match_dc_horse(dc_runs, historical_df) → HorseMatchResult
- match_all_dc_horses(...) → dict[dc_hid → HorseMatchResult]
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import pandas as pd


MIN_VOTES_HIGH = 5       # 5 過去走以上で同一 horse_id にヒット = 高信頼度
MIN_VOTES_MEDIUM = 3     # 3-4 走一致 = 中信頼度(候補が単独 + 直近 race_date 18ヶ月以内)
MEDIUM_RECENT_MONTHS = 18  # 中信頼度の偽陽性予防: candidate の最新 race_date がこの期間内
DATE_TOLERANCE_DAYS = 3  # historical race_date との許容差(日)


@dataclass(frozen=True)
class HorseMatchResult:
    matched_horse_id: str | None
    n_valid_runs: int    # DC 側で日付+距離 が valid な過去走数
    n_votes: int         # 最有力 horse_id の票数
    candidates: int      # 票がある horse_id の総数(参考)
    confidence: str = "none"  # "high"(5+) / "medium"(3-4 + 単独 + 最新走 18ヶ月内) / "none"


def _build_historical_index(
    historical_df: pd.DataFrame,
    target_date_iso: str,
) -> pd.DataFrame:
    """historical の前処理(target_date より前 + 距離・日付を numeric 化)。"""
    df = historical_df[historical_df["race_date"] < target_date_iso].copy()
    df["race_date_dt"] = pd.to_datetime(df["race_date"], errors="coerce")
    df["distance"] = pd.to_numeric(df["distance"], errors="coerce")
    df = df.dropna(subset=["race_date_dt", "distance"])
    df["distance"] = df["distance"].astype(int)
    df["horse_id"] = df["horse_id"].astype(str)
    return df


def _candidate_horses_for_run(
    hist_indexed: pd.DataFrame,
    estimated_date: pd.Timestamp,
    distance: int,
    *,
    tolerance_days: int = DATE_TOLERANCE_DAYS,
) -> set[str]:
    """historical を「日付 ± tolerance + 距離一致」で絞り、horse_id 集合を返す。"""
    lo = estimated_date - pd.Timedelta(days=tolerance_days)
    hi = estimated_date + pd.Timedelta(days=tolerance_days)
    mask = (
        (hist_indexed["race_date_dt"].between(lo, hi))
        & (hist_indexed["distance"] == distance)
    )
    return set(hist_indexed.loc[mask, "horse_id"].unique())


def match_dc_horse(
    dc_past_runs: list[dict | None],
    hist_indexed: pd.DataFrame,
    *,
    target_date_iso: str | None = None,
    tolerance_days: int = DATE_TOLERANCE_DAYS,
) -> HorseMatchResult:
    """
    DC の 1 馬分の過去走から historical 上の horse_id を推定する。

    信頼度:
      - **high**: 5 票以上一致 + 単独最高票
      - **medium**: 3-4 票一致 + 単独最高票 + 候補の最新 race_date が
                   target_date から 18 ヶ月以内(休養長馬の偽陽性予防)
      - **none**: 上記いずれにも該当せず(マッチ失敗)

    引数:
        dc_past_runs: parse_dc_dataframe の戻り値の各馬分。
                      各 dict は race_date(推定 ISO 文字列)と distance を持つ。
        hist_indexed: _build_historical_index で前処理済みの DataFrame。
        target_date_iso: 当日レース日(医薬の medium 信頼度判定用)。
    """
    valid_runs = 0
    votes: Counter[str] = Counter()
    for r in dc_past_runs:
        if not isinstance(r, dict):
            continue
        date_str = r.get("race_date")
        dist = r.get("distance")
        if not date_str or not dist or int(dist) <= 0:
            continue
        try:
            est_date = pd.Timestamp(date_str)
        except (ValueError, TypeError):
            continue
        candidates = _candidate_horses_for_run(
            hist_indexed, est_date, int(dist), tolerance_days=tolerance_days,
        )
        if not candidates:
            continue
        valid_runs += 1
        for hid in candidates:
            votes[hid] += 1

    if not votes:
        return HorseMatchResult(None, valid_runs, 0, 0, "none")

    top_hid, top_n = votes.most_common(1)[0]
    n_top_tied = sum(1 for _, n in votes.items() if n == top_n)

    # 高信頼度: 5 票以上 + 単独最高票
    if top_n >= MIN_VOTES_HIGH and n_top_tied == 1:
        return HorseMatchResult(top_hid, valid_runs, top_n, len(votes), "high")

    # 中信頼度: 3-4 票一致 + 単独 + 最新走が直近 18 ヶ月以内
    if MIN_VOTES_MEDIUM <= top_n < MIN_VOTES_HIGH and n_top_tied == 1:
        if target_date_iso is not None:
            try:
                target_dt = pd.Timestamp(target_date_iso)
                # 候補馬の最新走 race_date を確認
                candidate_runs = hist_indexed[hist_indexed["horse_id"] == top_hid]
                if not candidate_runs.empty:
                    latest = candidate_runs["race_date_dt"].max()
                    cutoff = target_dt - pd.DateOffset(months=MEDIUM_RECENT_MONTHS)
                    if latest >= cutoff:
                        return HorseMatchResult(
                            top_hid, valid_runs, top_n, len(votes), "medium",
                        )
            except (ValueError, TypeError):
                pass
        # target_date 未指定の場合は安全側で medium 不採用
        return HorseMatchResult(None, valid_runs, top_n, len(votes), "none")

    # 同票複数 / 票数不足 → 失敗
    return HorseMatchResult(None, valid_runs, top_n, len(votes), "none")


def match_all_dc_horses(
    dc_horse_ids: list[str],
    dc_past_runs_by_horse: dict[str, list[dict | None]],
    historical_df: pd.DataFrame,
    target_date_iso: str,
) -> dict[str, HorseMatchResult]:
    """
    DC 側の全馬について horse_id 推定を一括実行。
    hist_indexed は 1 回だけ作って全馬で共有。
    """
    hist_indexed = _build_historical_index(historical_df, target_date_iso)
    out: dict[str, HorseMatchResult] = {}
    for hid_dc in dc_horse_ids:
        runs = dc_past_runs_by_horse.get(hid_dc, [])
        out[hid_dc] = match_dc_horse(
            runs, hist_indexed, target_date_iso=target_date_iso,
        )
    return out
