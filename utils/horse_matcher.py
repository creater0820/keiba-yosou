"""
DC 形式(TARGET ダイレクト)の馬を historical/races.parquet 上の
`horse_id`(8桁血統登録番号)に **過去走パターンマッチ** で紐付ける。

DC 形式は 血統登録番号 / 馬名 / 騎手 を持たないが、過去 7 走の
`(distance, finishing_position)` シグネチャが一致する馬は同一馬と
高確度で推定できる(同距離+同着順を 3 回以上偶然引く確率は極小)。

API:
- build_signature_index(historical_df, target_date) → dict
- match_dc_horse(dc_past_runs, sig_index) → (horse_id | None, n_pairs, n_matches)
- match_all_dc_horses(race_card_df, historical_df, target_date) → dict[dc_horse_id → match_result]

判定基準(誤マッチ予防):
  - DC 側で有効な (distance, finishing_position) ペアが 3 個以上
  - そのうち historical 側で同一 horse_id にヒットしたペアが 3 個以上
  - 一致率 (hits / pairs) が 60% 以上

historical 側で過去 7 走を見るのは、DC 側が 7 走シグネチャを持つため。
historical でも上限 7 走に揃えることでフェアな比較になる。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import pandas as pd


MIN_MATCHES = 3        # 一致ペア数の最小値
MIN_RATIO = 0.60       # 一致ペア数 / 総有効ペア数 の下限
HISTORICAL_LOOKBACK_RUNS = 7   # historical 側の参照走数(DC 側と揃える)


@dataclass(frozen=True)
class HorseMatchResult:
    """1 頭分のマッチ結果。"""
    matched_horse_id: str | None
    n_valid_pairs: int      # DC 側で評価対象になった有効ペア数
    n_matched: int           # historical との一致ペア数
    confidence: float        # n_matched / n_valid_pairs (0.0-1.0)


def _extract_pairs_from_dc_runs(
    dc_runs: list[dict | None],
) -> list[tuple[int, int]]:
    """DC の過去走 list から有効な (distance, finishing_position) ペアを抽出。"""
    pairs: list[tuple[int, int]] = []
    for r in dc_runs:
        if not isinstance(r, dict):
            continue
        d = r.get("distance")
        p = r.get("finishing_position")
        try:
            d_int = int(d) if d is not None else 0
            p_int = int(p) if p is not None else 0
        except (TypeError, ValueError):
            continue
        if d_int > 0 and p_int > 0:
            pairs.append((d_int, p_int))
    return pairs


def build_signature_index(
    historical_df: pd.DataFrame,
    target_date_iso: str,
    *,
    lookback: int = HISTORICAL_LOOKBACK_RUNS,
) -> dict[tuple[int, int], list[str]]:
    """
    historical 側の `(distance, finishing_position)` → horse_id のリスト
    を作る索引。target_date より前の各馬の最新 lookback 走に絞る。

    返り値の dict は読み取り専用前提。再利用するなら呼び出し側でキャッシュ。
    """
    if historical_df.empty:
        return {}

    df = historical_df[historical_df["race_date"] < target_date_iso].copy()
    df["distance"] = pd.to_numeric(df["distance"], errors="coerce")
    df["finishing_position"] = pd.to_numeric(df["finishing_position"], errors="coerce")
    df = df.dropna(subset=["distance", "finishing_position"])
    df["distance"] = df["distance"].astype(int)
    df["finishing_position"] = df["finishing_position"].astype(int)

    # 各 horse_id の最新 lookback 走に絞る(古いレースの偶然一致を抑制)
    df = df.sort_values("race_date", ascending=False).groupby("horse_id").head(lookback)

    # 索引: (distance, finishing_position) → list[horse_id]
    grouped = df.groupby(["distance", "finishing_position"])["horse_id"]
    return {key: list(group) for key, group in grouped}


def match_dc_horse(
    dc_past_runs: list[dict | None],
    sig_index: dict[tuple[int, int], list[str]],
    *,
    min_matches: int = MIN_MATCHES,
    min_ratio: float = MIN_RATIO,
) -> HorseMatchResult:
    """
    DC の 1 馬分の過去走から historical 上の horse_id を推定する。
    一致が弱ければ matched_horse_id=None で返す(呼び出し側で簡易モード fallback)。
    """
    pairs = _extract_pairs_from_dc_runs(dc_past_runs)
    n_pairs = len(pairs)
    if n_pairs < min_matches:
        return HorseMatchResult(None, n_pairs, 0, 0.0)

    counts: Counter[str] = Counter()
    for pair in pairs:
        for hid in sig_index.get(pair, []):
            counts[hid] += 1

    if not counts:
        return HorseMatchResult(None, n_pairs, 0, 0.0)

    top_hid, top_n = counts.most_common(1)[0]
    confidence = top_n / n_pairs if n_pairs > 0 else 0.0
    if top_n < min_matches or confidence < min_ratio:
        return HorseMatchResult(None, n_pairs, top_n, confidence)

    return HorseMatchResult(top_hid, n_pairs, top_n, confidence)


def match_all_dc_horses(
    dc_horse_ids: list[str],
    dc_past_runs_by_horse: dict[str, list[dict | None]],
    historical_df: pd.DataFrame,
    target_date_iso: str,
) -> dict[str, HorseMatchResult]:
    """
    DC 側の全馬について horse_id 推定を一括実行。
    sig_index は historical を 1 回スキャンして再利用するので、馬数分のループは
    Python 側で十分高速(495 馬で数百ミリ秒オーダー)。
    """
    sig_index = build_signature_index(historical_df, target_date_iso)
    out: dict[str, HorseMatchResult] = {}
    for hid_dc in dc_horse_ids:
        runs = dc_past_runs_by_horse.get(hid_dc, [])
        out[hid_dc] = match_dc_horse(runs, sig_index)
    return out
