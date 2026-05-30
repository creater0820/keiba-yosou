"""v1.12.0 Phase 1: Softmax 確率のキャリブレーション検証スクリプト。

historical/races.parquet の過去レースを使い、各馬の rating(既存ロジック不変)
から softmax で派生した単勝確率が、実際の勝率とどれだけ整合しているかを
複数の温度 T で評価する。

評価指標:
  - Brier Score: mean((p - outcome)^2)。0 に近いほど良い(< 0.20 で妥当)。
  - Log Loss: -mean(o·log p + (1-o)·log(1-p))。小さいほど良い(< 2.5 で妥当)。
  - 信頼性図(Reliability Diagram): 予測確率を 10 bin に分け、bin 内の
    平均予測確率 vs 実際の勝率を比較。対角線に近いほどキャリブレーション良好。

outcome は「その馬が 1 着だったか(finishing_position == 1)」の二値。
全レース × 全頭の (win_prob, outcome) を集計する。

使い方:
    .venv/bin/python scripts/calibrate_probability_v12.py --start 2026-01-01 --end 2026-05-10

注意: predict_race_v2 は rating を計算するだけ(T 非依存)なので、1 レース 1 回
予想 → 同じ rating から 8 種類の T で確率を派生する(効率的)。
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_loader import HistoricalData, load_historical_data
from prediction_logic import predict_race_v2
from utils.probability_engine import compute_race_probabilities

# キャリブレーションで試す温度の候補
TEMPERATURES = [10, 15, 20, 25, 30, 35, 40, 50]


# =====================================================================
# 純粋なメトリクス関数(テスト可能)
# =====================================================================

def brier_score(probs: list[float], outcomes: list[int]) -> float:
    """Brier Score = mean((p - outcome)^2)。"""
    if not probs:
        return float("nan")
    p = np.asarray(probs, dtype=np.float64)
    o = np.asarray(outcomes, dtype=np.float64)
    return float(np.mean((p - o) ** 2))


def log_loss(probs: list[float], outcomes: list[int], eps: float = 1e-15) -> float:
    """Log Loss = -mean(o·log p + (1-o)·log(1-p))。p は [eps, 1-eps] にクリップ。"""
    if not probs:
        return float("nan")
    p = np.clip(np.asarray(probs, dtype=np.float64), eps, 1 - eps)
    o = np.asarray(outcomes, dtype=np.float64)
    return float(-np.mean(o * np.log(p) + (1 - o) * np.log(1 - p)))


def reliability_bins(
    probs: list[float], outcomes: list[int], n_bins: int = 10,
) -> list[dict]:
    """信頼性図用に [0,1] を n_bins 等分し、bin 内の平均予測確率と実勝率を返す。

    戻り値: [{bin_lo, bin_hi, count, mean_pred, actual_rate}, ...]
    count=0 の bin は mean_pred / actual_rate を nan にする。
    """
    p = np.asarray(probs, dtype=np.float64)
    o = np.asarray(outcomes, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out: list[dict] = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        # 最終 bin は右端を含む
        if i == n_bins - 1:
            mask = (p >= lo) & (p <= hi)
        else:
            mask = (p >= lo) & (p < hi)
        cnt = int(mask.sum())
        if cnt == 0:
            out.append({"bin_lo": lo, "bin_hi": hi, "count": 0,
                        "mean_pred": float("nan"), "actual_rate": float("nan")})
        else:
            out.append({
                "bin_lo": float(lo), "bin_hi": float(hi), "count": cnt,
                "mean_pred": float(p[mask].mean()),
                "actual_rate": float(o[mask].mean()),
            })
    return out


def expected_calibration_error(bins: list[dict], total: int) -> float:
    """ECE = Σ (count/total)·|mean_pred - actual_rate|。0 に近いほど対角線に近い。"""
    if total == 0:
        return float("nan")
    ece = 0.0
    for b in bins:
        if b["count"] == 0:
            continue
        ece += (b["count"] / total) * abs(b["mean_pred"] - b["actual_rate"])
    return ece


# =====================================================================
# 過去レースからの (win_prob, outcome) 収集
# =====================================================================

def collect_predictions(
    start_date: str, end_date: str, historical: HistoricalData,
) -> list[tuple[list[float], list[int]]]:
    """各レースの (active馬 rating リスト, outcome リスト) を集める。

    戻り値は [(ratings, outcomes), ...]。outcomes[i]=1 は i 番目の馬が 1 着。
    ratings をそのまま返すのは、後で T を変えて softmax を貼り直すため。
    """
    races = historical.races
    target = races[(races["race_date"] >= start_date)
                   & (races["race_date"] <= end_date)]
    race_ids = target["race_id"].unique()
    print(f"対象 {len(race_ids)} レースを予想中...")

    collected: list[tuple[list, list[int]]] = []
    for i, rid in enumerate(race_ids):
        if i % 200 == 0 and i > 0:
            print(f"  {i}/{len(race_ids)} 完了")
        race_horses = races[races["race_id"] == rid]
        if len(race_horses) < 4:
            continue
        race_date = race_horses["race_date"].iloc[0]
        if pd.isna(race_date) or not race_date:
            continue
        rc = race_horses.copy()
        rc.attrs["data_format"] = "ra_se"
        try:
            pred = predict_race_v2(rc, historical, target_date=str(race_date))
        except Exception:
            continue
        if not pred.horse_ratings:
            continue
        # 実着順マップ
        finish_by_id: dict[str, int | None] = {}
        for rec in race_horses[["horse_id", "finishing_position"]].to_dict("records"):
            fp = rec["finishing_position"]
            finish_by_id[str(rec["horse_id"])] = (
                int(fp) if pd.notna(fp) else None
            )
        # B1/B2 除外馬は確率 0(計算から外す)→ outcome 収集からも除外する
        excluded_ids = {d.horse_id for d in pred.demerit_entries}
        ratings_objs = [h for h in pred.horse_ratings
                        if h.horse_id not in excluded_ids]
        if len(ratings_objs) < 2:
            continue
        outcomes = [1 if finish_by_id.get(h.horse_id) == 1 else 0
                    for h in ratings_objs]
        # 1 着が除外馬だった等で勝者不在のレースはスキップ(確率合計と矛盾)
        if sum(outcomes) != 1:
            continue
        collected.append((ratings_objs, outcomes))

    print(f"有効レース: {len(collected)}")
    return collected


def _win_probs_for_temperature(ratings_objs: list, temperature: float) -> list[float]:
    """active 馬の rating オブジェクト群から、温度 T の単勝確率リストを得る。"""
    probs = compute_race_probabilities(
        ratings_objs, excluded_ids=set(), temperature=temperature,
    )
    by_id = {p.horse_id: p.win_prob for p in probs}
    return [by_id[h.horse_id] for h in ratings_objs]


def evaluate_temperature(
    collected: list[tuple[list, list[int]]], temperature: float,
) -> dict:
    """指定温度での Brier / LogLoss / 信頼性図 / ECE を計算する。"""
    all_probs: list[float] = []
    all_out: list[int] = []
    for ratings_objs, outcomes in collected:
        all_probs.extend(_win_probs_for_temperature(ratings_objs, temperature))
        all_out.extend(outcomes)
    bins = reliability_bins(all_probs, all_out, n_bins=10)
    return {
        "temperature": temperature,
        "n_samples": len(all_probs),
        "brier": brier_score(all_probs, all_out),
        "log_loss": log_loss(all_probs, all_out),
        "ece": expected_calibration_error(bins, len(all_probs)),
        "bins": bins,
    }


def print_reliability(bins: list[dict]) -> None:
    """信頼性図(テキスト)を出力。"""
    print(f"  {'bin':>12}  {'件数':>6}  {'平均予測':>8}  {'実勝率':>8}")
    for b in bins:
        if b["count"] == 0:
            continue
        print(f"  {b['bin_lo']*100:4.0f}-{b['bin_hi']*100:3.0f}%  "
              f"{b['count']:>6}  {b['mean_pred']*100:7.2f}%  "
              f"{b['actual_rate']*100:7.2f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-05-10")
    args = ap.parse_args()

    historical = load_historical_data()
    collected = collect_predictions(args.start, args.end, historical)
    if not collected:
        print("有効レースなし。期間を見直してください。")
        return

    results = [evaluate_temperature(collected, t) for t in TEMPERATURES]

    print()
    print("=" * 60)
    print(f"{'T':>4}  {'サンプル':>8}  {'Brier':>10}  {'LogLoss':>10}  {'ECE':>8}")
    print("-" * 60)
    for r in results:
        print(f"{r['temperature']:>4}  {r['n_samples']:>8}  "
              f"{r['brier']:>10.5f}  {r['log_loss']:>10.5f}  {r['ece']:>8.5f}")
    print("=" * 60)

    # 最適 T: Brier 最小(同点は ECE 最小)
    best = min(results, key=lambda r: (round(r["brier"], 6), r["ece"]))
    print(f"\n★ 最適 T = {best['temperature']} "
          f"(Brier {best['brier']:.5f} / LogLoss {best['log_loss']:.5f} / "
          f"ECE {best['ece']:.5f})")
    print(f"\n--- T={best['temperature']} の信頼性図 ---")
    print_reliability(best["bins"])

    print("\n判定:")
    print(f"  Brier < 0.20  : {'OK' if best['brier'] < 0.20 else 'NG'} "
          f"({best['brier']:.5f})")
    print(f"  LogLoss < 2.5 : {'OK' if best['log_loss'] < 2.5 else 'NG'} "
          f"({best['log_loss']:.5f})")


if __name__ == "__main__":
    main()
