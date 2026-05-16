"""v1.9.1 適用後の脚質カバレッジと G-Style 発火数を実機 DC で計測。

ユーザー要望(v1.9.1 完了報告):
  1. Tier 別の救済頭数内訳(Tier 1a/1b/1c/2/4/5 別)
  2. G7〜G10/G12 の発火頭数の変化(v1.9.0 vs v1.9.1 を予測実行で比較)
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from data_loader import enrich_dc_with_historical, load_race_card  # noqa: E402
from prediction_logic import predict_race_dc  # noqa: E402
from utils.race_history import (  # noqa: E402
    _collect_valid_corners,
    determine_running_style_with_confidence,
)


DC_CSV = ROOT / "data" / "raw" / "DC260509.CSV"
HISTORICAL = ROOT / "data" / "historical" / "races.parquet"
TODAY_GOING = "良"
G_STYLE_RULE_IDS = {"G7", "G8", "G9", "G10", "G12"}
G_FRAME_RULE_IDS = {"G1", "G2", "G3", "G4", "G5", "G6"}


def _classify_tier(runs: list[dict | None], distance: int | None) -> str:
    """determine_running_style_with_confidence と同一ロジックで Tier を返す。

    実関数は (style, confidence) しか返さないので、ここで再現的に分類する。
    """
    head5 = (runs or [])[:5]
    n_runs = sum(1 for r in head5 if r is not None)
    if n_runs == 0:
        return "Tier 5 (絶対 default)" if distance is None else "Tier 4 (距離別 default)"
    c1 = _collect_valid_corners(head5, "corner_1")
    c3 = _collect_valid_corners(head5, "corner_3")
    c4 = _collect_valid_corners(head5, "corner_4")
    if len(c1) >= 3:
        return "Tier 1a (corner_1)"
    if len(c3) >= 3:
        return "Tier 1b (corner_3)"
    if len(c4) >= 3:
        return "Tier 1c (corner_4)"
    if c1 or c3 or c4:
        return "Tier 2 (1-2走 暫定)"
    return "Tier 4 (距離別 default)" if distance else "Tier 5 (絶対 default)"


def main():
    print("=" * 70)
    print("v1.9.1 脚質カバレッジ + G-Style 発火数 計測")
    print(f"  DC CSV     : {DC_CSV}")
    print("=" * 70)

    historical = pd.read_parquet(HISTORICAL)
    race_card = load_race_card(str(DC_CSV))
    enriched = enrich_dc_with_historical(
        race_card, historical, today_going=TODAY_GOING,
    )
    past_runs_by_horse = enriched.attrs.get("dc_past_runs", {})

    # ----- Tier 別頭数集計 + confidence 内訳 -----
    tier_counter: Counter = Counter()
    conf_counter: Counter = Counter()
    style_counter: Counter = Counter()

    for row in enriched.to_dict("records"):
        hid = str(row["horse_id"])
        runs = past_runs_by_horse.get(hid, [])
        try:
            dist = int(row.get("distance") or 0) or None
        except (TypeError, ValueError):
            dist = None
        style, conf = determine_running_style_with_confidence(runs, distance=dist)
        tier_counter[_classify_tier(runs, dist)] += 1
        conf_counter[conf] += 1
        style_counter[style] += 1

    total = sum(tier_counter.values())

    print("\n【脚質判定 Tier 別内訳】")
    for tier in (
        "Tier 1a (corner_1)",
        "Tier 1b (corner_3)",
        "Tier 1c (corner_4)",
        "Tier 2 (1-2走 暫定)",
        "Tier 4 (距離別 default)",
        "Tier 5 (絶対 default)",
    ):
        n = tier_counter.get(tier, 0)
        pct = (n / total * 100) if total else 0
        print(f"  {tier:<28}: {n:4d} 頭 ({pct:5.1f}%)")
    print(f"  {'合計':<28}: {total:4d} 頭")

    print("\n【confidence 内訳】")
    for c in ("high", "medium", "default"):
        n = conf_counter.get(c, 0)
        pct = (n / total * 100) if total else 0
        print(f"  {c:<10}: {n:4d} 頭 ({pct:5.1f}%)")

    print("\n【脚質配分】")
    for s in ("逃げ", "先行", "差し", "追込", "不明(先行扱い)"):
        n = style_counter.get(s, 0)
        pct = (n / total * 100) if total else 0
        print(f"  {s:<14}: {n:4d} 頭 ({pct:5.1f}%)")

    # ----- G ルール発火数を予測実行から集計 -----
    print("\n" + "=" * 70)
    print("G ルール発火数(全レースの predict_race_dc 実行)")
    print("=" * 70)

    race_ids = enriched["race_id"].unique()
    g_fire_counter: Counter = Counter()
    g_horse_total = 0

    for rid in race_ids:
        race_subset = enriched[enriched["race_id"] == rid]
        if race_subset.empty:
            continue
        race_subset = race_subset.copy()
        race_subset.attrs.update(enriched.attrs)
        race_subset.attrs["dc_past_runs"] = {
            hid: past_runs_by_horse.get(hid, [])
            for hid in race_subset["horse_id"].astype(str).tolist()
        }
        race_subset.attrs["data_format"] = "dc"
        race_subset.attrs["dc_going"] = TODAY_GOING
        try:
            pred = predict_race_dc(race_subset, historical)
        except Exception as e:
            print(f"  予想失敗 race={rid}: {type(e).__name__}: {e}")
            continue
        for r in pred.horse_ratings:
            g_horse_total += 1
            for hit in r.matched:
                rid_short = hit.rule_id
                if rid_short.startswith("G"):
                    g_fire_counter[rid_short] += 1

    print(f"\n  全頭数(予測実行 OK 馬): {g_horse_total}")
    print()
    print("  G-Frame(枠順補正):")
    for rid in sorted(G_FRAME_RULE_IDS):
        n = g_fire_counter.get(rid, 0)
        print(f"    {rid:<5}: {n:3d} 頭")
    g_frame_total = sum(g_fire_counter.get(r, 0) for r in G_FRAME_RULE_IDS)
    print(f"    G-Frame 合計: {g_frame_total} 件")

    print()
    print("  G-Style(脚質補正、v1.9.1 改善対象):")
    for rid in sorted(G_STYLE_RULE_IDS):
        n = g_fire_counter.get(rid, 0)
        print(f"    {rid:<5}: {n:3d} 頭")
    g_style_total = sum(g_fire_counter.get(r, 0) for r in G_STYLE_RULE_IDS)
    print(f"    G-Style 合計: {g_style_total} 件")


if __name__ == "__main__":
    main()
