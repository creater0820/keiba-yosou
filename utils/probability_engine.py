"""Softmax 確率派生エンジン(v1.12.0 Phase 1)。

既存の rating 加算ロジック(utils/rating_engine.py 等)には **一切手を入れず**、
レースごとに各馬の `total_rating` から softmax で単勝確率を、Plackett-Luce で
複勝確率(1-3 着内)を計算する純粋関数群。

設計方針:
- **rating は内部信号、確率は派生表示**。確率の値は ◎○▲△マークや買い目に
  一切影響しない(prediction_logic 側で表示用フィールドに格納するだけ)。
- 本モジュールは rating_engine / judgment_engine を import しない(循環回避 +
  完全分離)。入力は `total_rating` / `horse_id` / `horse_number` 属性を持つ
  任意のオブジェクト(実体は HorseRating)を **ダックタイピング** で受ける。
- B1/B2 減点で軸候補から外れた馬は「除外馬」として確率 0、計算からも外す。

数式:
- 単勝確率(softmax、温度 T):
    p_i = exp(rating_i / T) / Σ_j exp(rating_j / T)
  T が大きいほど分布はフラット(横並び)に、小さいほど鋭く(高 rating に集中)。
- 複勝確率(Plackett-Luce、1-3 着内):
    P(i が 1-3 着) = P(i=1着) + P(i=2着) + P(i=3着)
  P(i=2着) = Σ_{j≠i} p_j · p_i/(1-p_j)
  P(i=3着) = Σ_{j≠i} Σ_{k≠i,j} p_j · p_k/(1-p_j) · p_i/(1-p_j-p_k)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

# スキーマバージョン(キャッシュキー / 将来の仕様変更検知用)
PROBABILITY_SCHEMA_VERSION = "v1-softmax-phase1"

# 既定の softmax 温度。scripts/calibrate_probability_v12.py のキャリブレーション
# (1,034 レース / 14,535 サンプル、2026-01-01〜2026-05-10)で決定。
# Brier Score が T とともに単調に低下し、サポート範囲(スライダー 10-50)内の
# 最適は **T=50**(Brier 0.06685 / LogLoss 0.25862 / ECE 0.01414)。
# rating の spread は粗い(0 が多い)ため、鋭い softmax(低 T)は過信になり
# キャリブレーションが悪化する。T=50 でも強い ◎(rating ~165)は ~50% を保つ。
DEFAULT_TEMPERATURE = 50.0

# 温度の下限(0 除算・極端な鋭さ防止)。スライダー最小値とも整合させる。
MIN_TEMPERATURE = 1.0


@dataclass(frozen=True)
class HorseProbability:
    """馬 1 頭分の確率派生結果。prediction_logic / app.py が消費する。"""
    horse_id: str
    horse_number: int      # 馬番
    rating: float          # 元の total_rating(参考表示用)
    win_prob: float        # 単勝確率 [0.0〜1.0]
    place_prob: float      # 複勝確率(1-3 着内)[0.0〜1.0]
    win_rank: int          # 単勝確率の順位(1=最も高い、除外馬は 99)
    is_excluded: bool       # B1/B2 減点で除外されている馬か


def compute_race_probabilities(
    horse_ratings: Iterable,
    excluded_ids: Iterable[str] | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
) -> list[HorseProbability]:
    """レース内の全馬の単勝・複勝確率を計算する。

    引数:
        horse_ratings: `total_rating` / `horse_id` / `horse_number` を持つ
                       オブジェクトのリスト(実体は HorseRating)。
        excluded_ids:  B1/B2 減点で除外された horse_id の集合(確率 0 にする)。
                       None なら除外なし。
        temperature:   softmax 温度 T。MIN_TEMPERATURE 未満は MIN にクランプ。

    戻り値: HorseProbability のリスト(馬番昇順で安定ソート)。
    """
    horses = list(horse_ratings)
    if not horses:
        return []

    excluded = set(str(h) for h in (excluded_ids or []))
    t = max(float(temperature), MIN_TEMPERATURE)

    # 除外馬とアクティブ馬を分ける(除外馬は softmax 計算から外す)
    active = [h for h in horses if str(h.horse_id) not in excluded]
    inactive = [h for h in horses if str(h.horse_id) in excluded]

    if not active:
        # 全頭除外(理論上ほぼ起きない)→ 全馬 0 確率で返す
        return _sorted([_zero_prob(h) for h in horses])

    ratings = np.array([float(h.total_rating) for h in active], dtype=np.float64)

    # ----- 単勝確率(softmax、最大値減算でオーバーフロー回避) -----
    shifted = (ratings - ratings.max()) / t
    exp_scores = np.exp(shifted)
    win_probs = exp_scores / exp_scores.sum()

    # ----- 複勝確率(Plackett-Luce、1-3 着内) -----
    place_probs = _plackett_luce_place_probs(win_probs, n_places=3)

    # ----- 単勝確率の順位(1=最高、同値は元順=馬番でタイブレーク) -----
    # argsort の二重適用で順位を得る(降順)。
    win_ranks = (-win_probs).argsort(kind="stable").argsort(kind="stable") + 1

    result: list[HorseProbability] = []
    for h, wp, pp, wr in zip(active, win_probs, place_probs, win_ranks):
        result.append(HorseProbability(
            horse_id=str(h.horse_id),
            horse_number=int(h.horse_number),
            rating=float(h.total_rating),
            win_prob=float(wp),
            place_prob=float(pp),
            win_rank=int(wr),
            is_excluded=False,
        ))
    for h in inactive:
        result.append(_zero_prob(h))

    return _sorted(result)


def _plackett_luce_place_probs(
    win_probs: np.ndarray, n_places: int = 3,
) -> np.ndarray:
    """各馬の 1〜n_places 着以内の確率を Plackett-Luce で計算する。

    P(i が n_places 着内) = P(i=1着) + P(i=2着) + ... + P(i=n_places着)

    計算量は O(n^n_places)。n_places=3 で 18 頭なら約 5,800 ステップ、
    性能影響は無視できる。
    """
    n = len(win_probs)
    place_probs = win_probs.copy()  # 1 着確率からスタート

    if n_places >= 2 and n >= 2:
        # 2 着確率: j が 1 着 → 残りの中で i が 1 着
        for i in range(n):
            for j in range(n):
                if j == i:
                    continue
                denom = 1.0 - win_probs[j]
                if denom > 0:
                    place_probs[i] += win_probs[j] * (win_probs[i] / denom)

    if n_places >= 3 and n >= 3:
        # 3 着確率: j が 1 着, k が 2 着 → 残りの中で i が 1 着
        for i in range(n):
            for j in range(n):
                if j == i:
                    continue
                denom_j = 1.0 - win_probs[j]
                if denom_j <= 0:
                    continue
                for k in range(n):
                    if k == i or k == j:
                        continue
                    p_k_after_j = win_probs[k] / denom_j
                    denom_jk = 1.0 - win_probs[j] - win_probs[k]
                    if denom_jk <= 0:
                        continue
                    p_i_after_jk = win_probs[i] / denom_jk
                    place_probs[i] += win_probs[j] * p_k_after_j * p_i_after_jk

    # 数値誤差で 1.0 を僅かに超えうるので上限クリップ(理論上 ≤ 1.0)
    return np.clip(place_probs, 0.0, 1.0)


def _zero_prob(horse) -> HorseProbability:
    """除外馬(または全頭除外時)の 0 確率エントリ。"""
    return HorseProbability(
        horse_id=str(horse.horse_id),
        horse_number=int(horse.horse_number),
        rating=float(horse.total_rating),
        win_prob=0.0,
        place_prob=0.0,
        win_rank=99,
        is_excluded=True,
    )


def _sorted(probs: list[HorseProbability]) -> list[HorseProbability]:
    """馬番昇順で安定ソートして返す(UI 表示・テストの決定性のため)。"""
    return sorted(probs, key=lambda p: p.horse_number)
