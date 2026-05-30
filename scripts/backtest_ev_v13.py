"""EV 戦略バックテスト(v1.13.0 Phase 2)。

historical/races.parquet には実 `odds` 列(単勝オッズ)があるので、過去レースで:
1. 各馬の win_prob を Phase 1 ロジック(predict_race_v2 → softmax)で計算
2. 実 odds と組み合わせて EV = win_prob × odds − 1 を計算
3. 複数戦略を比較:
   - Baseline: ◎本命を単勝購入(v1.10.0 までの戦略)
   - A: EV > 0 の馬を単勝購入
   - B: EV > 0.10 の馬を単勝購入
   - C: EV > 0.20 の馬を単勝購入
   - D: 各レース最高 EV 馬(EV > 0 のみ)1 点
   - E: Kelly 比例配分(参考、可変ステーク)
4. 各戦略の 取引数 / 的中率 / 回収率 / 累積収支 を出力
5. EV 閾値感度分析(0.05/0.10/0.15/0.20/0.30)
6. EV 分布ヒストグラム(全馬 / 1着馬)

使い方:
    .venv/bin/python scripts/backtest_ev_v13.py --start 2026-04-01 --end 2026-05-10

注意: historical の odds 列は SE 取込分は実単勝オッズだが、一部の行は別指標の
可能性がある(CLAUDE.md v1.0 運用メモ)。回収率は **参考値** として扱う。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_loader import HistoricalData, load_historical_data
from prediction_logic import predict_race_v2
from utils.ev_engine import kelly_fraction
from utils.probability_engine import DEFAULT_TEMPERATURE, compute_race_probabilities


# =====================================================================
# 純粋ヘルパ(テスト可能)
# =====================================================================

def summarize_strategy(bets: list[dict]) -> dict:
    """単勝固定 1 点ベットのリストから 取引数/的中率/回収率/収支 を集計。

    各 bet: {"odds": float, "win": bool}。
    回収率(%) = Σ(当たり馬の odds) / 取引数 × 100。
    収支 = Σ(当たり odds) − 取引数(1 点 100% 投資基準)。
    """
    n = len(bets)
    if n == 0:
        return {"n": 0, "hit_rate": 0.0, "payout_rate": 0.0, "net": 0.0}
    hits = sum(1 for b in bets if b["win"])
    returned = sum(b["odds"] for b in bets if b["win"])
    return {
        "n": n,
        "hit_rate": hits / n * 100,
        "payout_rate": returned / n * 100,
        "net": returned - n,
    }


def summarize_kelly(bets: list[dict]) -> dict:
    """Kelly 可変ステークの集計(参考)。

    各 bet: {"odds": float, "win": bool, "stake": float}。
    回収率(%) = Σ(当たり stake×odds) / Σ(stake) × 100。
    """
    staked = sum(b["stake"] for b in bets)
    if staked <= 0:
        return {"n": 0, "hit_rate": 0.0, "payout_rate": 0.0, "net": 0.0, "staked": 0.0}
    returned = sum(b["stake"] * b["odds"] for b in bets if b["win"])
    hits = sum(1 for b in bets if b["win"])
    return {
        "n": len(bets),
        "hit_rate": hits / len(bets) * 100,
        "payout_rate": returned / staked * 100,
        "net": returned - staked,
        "staked": staked,
    }


def ev_histogram(values: list[float], edges: list[float]) -> list[dict]:
    """EV 値のヒストグラム。edges は境界(昇順)。各 bin の件数を返す。"""
    arr = np.asarray(values, dtype=np.float64)
    out = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if i == len(edges) - 2:
            cnt = int(((arr >= lo) & (arr <= hi)).sum())
        else:
            cnt = int(((arr >= lo) & (arr < hi)).sum())
        out.append({"lo": lo, "hi": hi, "count": cnt})
    return out


def _valid_odds(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 1.0:  # NaN or ≤1.0
        return None
    return f


# =====================================================================
# 1 レースから全馬の (win_prob, odds, finish) を収集
# =====================================================================

def collect_race(
    race_horses: pd.DataFrame, historical: HistoricalData, temperature: float,
) -> dict | None:
    """1 レースを予想 → 各馬の win_prob / odds / finish / main_pick を返す。"""
    if len(race_horses) < 4:
        return None
    race_date = race_horses["race_date"].iloc[0]
    if pd.isna(race_date) or not race_date:
        return None
    rc = race_horses.copy()
    rc.attrs["data_format"] = "ra_se"
    try:
        pred = predict_race_v2(rc, historical, target_date=str(race_date))
    except Exception:
        return None
    if not pred.horse_ratings:
        return None

    excluded = {d.horse_id for d in pred.demerit_entries}
    active = [h for h in pred.horse_ratings if h.horse_id not in excluded]
    if len(active) < 2:
        return None
    probs = {p.horse_id: p for p in compute_race_probabilities(
        active, excluded_ids=set(), temperature=temperature,
    )}

    finish_by_id, odds_by_id = {}, {}
    for rec in race_horses[["horse_id", "finishing_position", "odds"]].to_dict("records"):
        hid = str(rec["horse_id"])
        fp = rec["finishing_position"]
        finish_by_id[hid] = int(fp) if pd.notna(fp) else None
        odds_by_id[hid] = _valid_odds(rec["odds"])

    horses = []
    for h in active:
        p = probs.get(h.horse_id)
        if p is None:
            continue
        odds = odds_by_id.get(h.horse_id)
        horses.append({
            "horse_id": h.horse_id,
            "win_prob": p.win_prob,
            "odds": odds,
            "finish": finish_by_id.get(h.horse_id),
            "ev": (p.win_prob * odds - 1.0) if odds else None,
        })
    return {"main_pick": pred.judgment.main_pick, "horses": horses}


# =====================================================================
# 戦略適用
# =====================================================================

STRATEGIES = {
    "A: EV>0": 0.0,
    "B: EV>0.10": 0.10,
    "C: EV>0.20": 0.20,
}


def run_backtest(start: str, end: str, historical: HistoricalData,
                 temperature: float = DEFAULT_TEMPERATURE) -> dict:
    races = historical.races
    target = races[(races["race_date"] >= start) & (races["race_date"] <= end)]
    race_ids = target["race_id"].unique()
    print(f"対象 {len(race_ids)} レースを予想中...")

    bets_baseline, bets_D, bets_E = [], [], []
    bets_thresh = {k: [] for k in STRATEGIES}
    all_ev, winner_ev = [], []
    n_valid = 0

    for i, rid in enumerate(race_ids):
        if i % 200 == 0 and i > 0:
            print(f"  {i}/{len(race_ids)} 完了")
        r = collect_race(races[races["race_id"] == rid], historical, temperature)
        if r is None:
            continue
        n_valid += 1
        horses = r["horses"]

        # Baseline: ◎本命 単勝
        mp = r["main_pick"]
        if mp:
            mh = next((h for h in horses if h["horse_id"] == mp), None)
            if mh and mh["odds"]:
                bets_baseline.append({"odds": mh["odds"], "win": mh["finish"] == 1})

        # EV 分布収集
        for h in horses:
            if h["ev"] is not None:
                all_ev.append(h["ev"])
                if h["finish"] == 1:
                    winner_ev.append(h["ev"])

        # 閾値戦略 A/B/C
        for label, th in STRATEGIES.items():
            for h in horses:
                if h["ev"] is not None and h["ev"] > th and h["odds"] >= 2.0:
                    bets_thresh[label].append({"odds": h["odds"], "win": h["finish"] == 1})

        # D: 最高 EV 馬(EV>0)1 点
        evs = [h for h in horses if h["ev"] is not None and h["odds"] >= 2.0]
        if evs:
            best = max(evs, key=lambda h: h["ev"])
            if best["ev"] > 0:
                bets_D.append({"odds": best["odds"], "win": best["finish"] == 1})

        # E: Kelly 比例(参考)
        for h in horses:
            if h["ev"] is not None and h["ev"] > 0 and h["odds"] >= 2.0:
                f = kelly_fraction(h["win_prob"], h["odds"])
                if f > 0:
                    bets_E.append({"odds": h["odds"], "win": h["finish"] == 1, "stake": f})

    print(f"有効レース: {n_valid}")
    return {
        "Baseline: ◎本命単勝": summarize_strategy(bets_baseline),
        **{label: summarize_strategy(bets_thresh[label]) for label in STRATEGIES},
        "D: 最高EV馬1点": summarize_strategy(bets_D),
        "E: Kelly比例(参考)": summarize_kelly(bets_E),
        "_all_ev": all_ev,
        "_winner_ev": winner_ev,
    }


def print_results(res: dict, baseline_label: str = "Baseline: ◎本命単勝") -> None:
    print()
    print("=" * 78)
    print(f"{'戦略':<24}{'取引数':>8}{'的中率':>10}{'回収率':>10}{'収支':>12}")
    print("-" * 78)
    base_payout = res[baseline_label]["payout_rate"]
    for label, s in res.items():
        if label.startswith("_"):
            continue
        diff = ""
        if not label.startswith("Baseline"):
            diff = f"  ({s['payout_rate'] - base_payout:+.1f}pt)"
        print(f"{label:<24}{s['n']:>8}{s['hit_rate']:>9.2f}%"
              f"{s['payout_rate']:>9.1f}%{s['net']:>+11.1f}{diff}")
    print("=" * 78)


def print_sensitivity(all_records: list[dict]) -> None:
    """EV 閾値感度分析(0.05/0.10/0.15/0.20/0.30)。"""
    print("\n--- EV 閾値感度分析 ---")
    print(f"{'閾値':>6}{'取引数':>8}{'的中率':>10}{'回収率':>10}")
    for th in (0.05, 0.10, 0.15, 0.20, 0.30):
        bets = [b for b in all_records if b["ev"] > th and b["odds"] >= 2.0]
        s = summarize_strategy(bets)
        print(f"{th:>6.2f}{s['n']:>8}{s['hit_rate']:>9.2f}%{s['payout_rate']:>9.1f}%")


def print_histogram(label: str, values: list[float]) -> None:
    if not values:
        print(f"\n{label}: データなし")
        return
    edges = [-1.0, -0.5, -0.2, 0.0, 0.2, 0.5, 1.0, 2.0, 100.0]
    hist = ev_histogram(values, edges)
    mx = max(h["count"] for h in hist) or 1
    print(f"\n--- {label}(n={len(values)}、平均 EV {np.mean(values):+.3f}) ---")
    for h in hist:
        bar = "█" * int(h["count"] / mx * 40)
        print(f"  [{h['lo']:+.1f},{h['hi']:+.1f}) {h['count']:>6} {bar}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-04-01")
    ap.add_argument("--end", default="2026-05-10")
    ap.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    args = ap.parse_args()

    historical = load_historical_data()
    res = run_backtest(args.start, args.end, historical, args.temperature)
    print_results(res)
    print_histogram("全馬の EV 分布", res["_all_ev"])
    print_histogram("1着馬の EV 分布", res["_winner_ev"])


if __name__ == "__main__":
    main()
