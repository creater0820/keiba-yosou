"""
動作確認用の予想 JSON を5件、predictions/ 配下に自動生成するスクリプト。

実行例:
    python scripts/seed_test_predictions.py

仕組み:
- data/historical/races.parquet からランダムに5レース選択
- 各レースの出走馬から「ある程度それっぽい」推奨馬4頭を選定して
  ◎○▲△ を割り振る(過去履歴ベースの簡易ヒューリスティック)
- predictions/<race_date>_<racecourse>_<NN>R.json として書き出し

これにより、的中履歴ダッシュボードに動作確認用データが入る。
本物の予想と紛らわしくないよう logic_version は "v0.0-seed-test" にする。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pandas as pd

# プロジェクトルートを sys.path に追加(scripts/ から実行する想定)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.prediction_io import build_prediction_dict, save_prediction_to_disk

RACES_PARQUET = Path("data/historical/races.parquet")
SEED_LOGIC_VERSION = "v0.0-seed-test"
N_RACES = 5

random.seed(20260503)  # 再現性のため固定シード


def _pick_recommendations(race_entries: pd.DataFrame) -> list[dict]:
    """
    1レースの出走馬リストから、◎○▲△ の推奨馬4頭を選定する。
    簡易ヒューリスティック:
      - その馬の過去の最近着順(同 race_id 以前)で並べ、上位4頭を ◎○▲△
      - 過去履歴が無い馬は中位扱い
      - 着順が同じならランダム揺らぎを加える
    """
    if len(race_entries) < 4:
        return []  # 4頭に満たないレースはスキップ

    # ここでは簡易化のため、出走馬から「ランダムに4頭選ぶ」+ 微少な順位バイアスのみ
    # (ダッシュボード動作確認用のシードなので、的中率が ~25% 前後になればOK)
    sampled = race_entries.sample(n=4, random_state=random.randint(0, 1_000_000)).reset_index(drop=True)

    marks = ["◎", "○", "▲", "△"]
    recs = []
    for i, (_, row) in enumerate(sampled.iterrows()):
        recs.append({
            "mark": marks[i],
            "horse_id": str(row["horse_id"]),
            "horse_name": str(row["horse_name"]),
            "score": round(80 - i * 5 + random.uniform(-2, 2), 2),  # 80, 75, 70, 65 + noise
            "reasons": ["[seed test] ランダム選定 + 順位バイアス"],
        })
    return recs


def main() -> None:
    if not RACES_PARQUET.exists():
        print(f"❌ {RACES_PARQUET} が見つかりません。先に csv_to_parquet を実行してください。")
        sys.exit(1)

    print(f"races.parquet 読み込み中…")
    races = pd.read_parquet(RACES_PARQUET)
    print(f"  全 {len(races):,} 行 / {races['race_id'].nunique():,} レース")

    # ランダムにレースを選ぶ(4頭以上揃っているもののみ)
    field_size_by_race = races.groupby("race_id").size()
    eligible_race_ids = field_size_by_race[field_size_by_race >= 4].index.tolist()
    chosen_race_ids = random.sample(eligible_race_ids, k=min(N_RACES, len(eligible_race_ids)))

    print(f"\nseed 対象レース({len(chosen_race_ids)} 件):")
    saved_paths: list[Path] = []
    for race_id in chosen_race_ids:
        race_entries = races[races["race_id"] == race_id]
        head = race_entries.iloc[0]

        race_info = {
            "race_id": race_id,
            "race_date": str(head["race_date"]),
            "racecourse": str(head["racecourse"]),
            "race_number": int(head["race_number"]) if pd.notna(head["race_number"]) else 0,
            "race_name": str(head["race_name"]),
            "distance": int(head["distance"]) if pd.notna(head["distance"]) else 0,
            "surface": str(head["surface"]),
        }

        recs = _pick_recommendations(race_entries)
        if not recs:
            print(f"  ⏭ {race_id}: 4頭未満のためスキップ")
            continue

        prediction = build_prediction_dict(race_info, recs, logic_version=SEED_LOGIC_VERSION)
        path = save_prediction_to_disk(prediction)
        saved_paths.append(path)
        print(f"  ✓ {path.name} ({race_info['racecourse']} {race_info['race_number']}R, "
              f"{race_info['race_name']}, {race_info['distance']}m {race_info['surface']})")

    print(f"\n✅ {len(saved_paths)} 件の seed 予想を {Path('predictions/')} に保存しました。")
    print("ダッシュボード(pages/02_的中履歴.py)で動作確認してください。")


if __name__ == "__main__":
    main()
