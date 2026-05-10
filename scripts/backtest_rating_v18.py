"""v1.8.0 配点傾斜チューニングのバックテストスクリプト。

過去 historical の各レースを 1 つずつ「target」として、その馬の過去 10 走を
historical 内から取得 → predict_race_v2 (rating モード) で予想 → 実際の
着順と照合して以下を計測する:

  - ◎本命 1 着率 / 連対率(1-2 着) / 複勝率(1-3 着)
  - ワイド候補に含まれる馬の複勝率
  - 人気帯別 (1-2 番人気 / 3-5 番人気 / 6+ 番人気) の◎本命的中率
  - 単勝オッズベースの参考回収率(historical odds 列は別指標の可能性あり)

旧配点(v1.7.5.1)と新配点(v1.8.0)それぞれで実行し、
回収率と的中率の改善を比較する。

使い方:
    .venv/bin/python scripts/backtest_rating_v18.py --start 2026-04-01 --end 2026-05-03

注意: historical の odds 列は単勝確定オッズではなく別指標(全行 50 台前後)
の可能性があるため、回収率は **参考値** として扱う。メインは的中率比較。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_loader import HistoricalData, load_historical_data
from prediction_logic import predict_race_v2


def _build_race_card_from_historical(
    race_horses: pd.DataFrame,
) -> pd.DataFrame:
    """historical の 1 レース分を predict_race_v2 入力用 race_card 風に整形。

    historical 行は finishing_position(着順)を持つが、predict は当日情報
    のみ使うので問題ない(実際 race_card_df["finishing_position"] は predict
    内で参照されない)。data_format = "ra_se" を attrs に設定。
    """
    rc = race_horses.copy()
    rc.attrs["data_format"] = "ra_se"
    return rc


def _backtest_one_race(
    race_id: str,
    race_horses: pd.DataFrame,
    historical: HistoricalData,
) -> dict | None:
    """1 レースの予想を実行して結果を辞書で返す。失敗時は None。"""
    if len(race_horses) < 4:
        return None
    race_date = race_horses["race_date"].iloc[0]
    if pd.isna(race_date) or not race_date:
        return None

    rc = _build_race_card_from_historical(race_horses)
    try:
        pred = predict_race_v2(rc, historical, target_date=str(race_date))
    except Exception as e:
        return None

    main_pick = pred.judgment.main_pick
    if not main_pick:
        return None

    # ◎本命の実成績
    main_row = race_horses[race_horses["horse_id"].astype(str) == str(main_pick)]
    if main_row.empty:
        return None
    main = main_row.iloc[0]
    finish = main["finishing_position"]
    finish = int(finish) if pd.notna(finish) else None
    pop = main.get("popularity")
    pop = int(pop) if pd.notna(pop) else None
    odds = main.get("odds")
    odds = float(odds) if pd.notna(odds) else None

    # ワイド候補の実成績
    wides = [w.horse_id for w in pred.wide_candidates]
    wide_finishes = []
    for w_id in wides:
        wr = race_horses[race_horses["horse_id"].astype(str) == str(w_id)]
        if not wr.empty:
            f = wr.iloc[0]["finishing_position"]
            wide_finishes.append(int(f) if pd.notna(f) else None)

    return {
        "race_id": race_id,
        "race_date": str(race_date),
        "main_pick": str(main_pick),
        "main_finish": finish,
        "main_popularity": pop,
        "main_odds": odds,
        "wide_count": len(wides),
        "wide_top3_count": sum(1 for f in wide_finishes
                                if f is not None and f <= 3),
        "rating": next(
            (h.total_rating for h in pred.horse_ratings
             if h.horse_id == main_pick), 0,
        ),
    }


def run_backtest(
    start_date: str,
    end_date: str,
    historical: HistoricalData | None = None,
    label: str = "",
) -> pd.DataFrame:
    """指定期間の全レースをバックテストして DataFrame で返す。"""
    if historical is None:
        historical = load_historical_data()
    races = historical.races
    target = races[
        (races["race_date"] >= start_date)
        & (races["race_date"] <= end_date)
    ]
    race_ids = target["race_id"].unique()
    print(f"[{label}] {len(race_ids)} レースをバックテスト中...")

    results: list[dict] = []
    for i, rid in enumerate(race_ids):
        if i % 100 == 0 and i > 0:
            print(f"  [{label}] {i}/{len(race_ids)} 完了")
        race_horses = races[races["race_id"] == rid]
        r = _backtest_one_race(rid, race_horses, historical)
        if r:
            results.append(r)

    df = pd.DataFrame(results)
    print(f"[{label}] 完了: ◎本命確定 {len(df)} レース / 全 {len(race_ids)} レース")
    return df


def summarize(df: pd.DataFrame, label: str) -> dict:
    """バックテスト結果のサマリ統計を返す。"""
    n = len(df)
    if n == 0:
        return {"label": label, "n": 0}

    win = (df["main_finish"] == 1).sum()
    place = (df["main_finish"] <= 2).sum()
    show = (df["main_finish"] <= 3).sum()

    # 人気帯別的中率(複勝)
    pop_top = df[df["main_popularity"].between(1, 2)]
    pop_mid = df[df["main_popularity"].between(3, 5)]
    pop_dark = df[df["main_popularity"] >= 6]

    def show_rate(sub: pd.DataFrame) -> float:
        if len(sub) == 0:
            return float("nan")
        return (sub["main_finish"] <= 3).mean() * 100

    # 単勝回収率(参考、odds は別指標の可能性ある)
    win_payout_total = df[df["main_finish"] == 1]["main_odds"].fillna(0).sum()
    win_invest_total = n  # 100 円単位の N レース投資
    payback_rate = win_payout_total / win_invest_total * 100 if n else 0

    # ワイド候補(複勝に1頭でも入った率)
    wide_hit = (df["wide_top3_count"] >= 1).sum()

    return {
        "label": label,
        "n": n,
        "win_rate": win / n * 100,
        "place_rate": place / n * 100,
        "show_rate": show / n * 100,
        "win_payout_rate": payback_rate,
        "n_pop_top": len(pop_top),
        "show_pop_top": show_rate(pop_top),
        "n_pop_mid": len(pop_mid),
        "show_pop_mid": show_rate(pop_mid),
        "n_pop_dark": len(pop_dark),
        "show_pop_dark": show_rate(pop_dark),
        "wide_count": df["wide_count"].sum(),
        "wide_hit_count": int(wide_hit),
        "wide_hit_rate": wide_hit / n * 100,
    }


def print_comparison(s_old: dict, s_new: dict) -> None:
    """旧/新の summary を比較表示。"""
    print()
    print("=" * 70)
    print(f"{'指標':<32}  {'旧':>14}  {'新':>14}  {'差':>10}")
    print("-" * 70)

    def row(label, key, fmt="{:.2f}%"):
        v1, v2 = s_old.get(key, 0), s_new.get(key, 0)
        diff = v2 - v1
        sign = "+" if diff >= 0 else ""
        print(f"{label:<32}  {fmt.format(v1):>14}  "
              f"{fmt.format(v2):>14}  {sign}{fmt.format(diff):>9}")

    print(f"{'◎本命確定レース数':<32}  {s_old.get('n', 0):>14}  "
          f"{s_new.get('n', 0):>14}")
    row("  1 着率", "win_rate")
    row("  連対率(1-2 着)", "place_rate")
    row("  複勝率(1-3 着)", "show_rate")
    row("  単勝参考回収率", "win_payout_rate")
    print()
    row("人気帯別 1-2 番人気 複勝率", "show_pop_top")
    row("人気帯別 3-5 番人気 複勝率", "show_pop_mid")
    row("人気帯別 6+ 番人気  複勝率", "show_pop_dark")
    print()
    row("ワイド候補 複勝命中率", "wide_hit_rate")
    print("=" * 70)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-04-01")
    p.add_argument("--end", default="2026-05-03")
    p.add_argument("--label", default="rating", help="ラベル名(出力 CSV パス用)")
    p.add_argument("--out", default=None,
                    help="バックテスト結果 CSV の出力パス。指定なしなら出力しない")
    args = p.parse_args()

    historical = load_historical_data()
    df = run_backtest(args.start, args.end, historical, label=args.label)
    summary = summarize(df, args.label)

    print()
    print(f"=== {args.label} サマリ({args.start} 〜 {args.end}) ===")
    print(f"  ◎本命確定: {summary['n']} レース")
    print(f"  1 着率: {summary['win_rate']:.2f}%")
    print(f"  複勝率: {summary['show_rate']:.2f}%")
    print(f"  単勝参考回収率: {summary['win_payout_rate']:.1f}%")

    if args.out:
        df.to_csv(args.out, index=False)
        print(f"\n保存: {args.out}")


if __name__ == "__main__":
    main()
