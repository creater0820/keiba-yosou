"""
本ロジック v1.0 / 適用例の集計ヘルパ。

prediction_logic.predict_all_races_v1() の戻り値(dict[race_id → RacePrediction])
を「ルール → 該当した馬・レース」の逆引きに変換する。

pages/01_ロジック説明.py で各ルールの実データ適用例を表示するために使う。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from prediction_logic import RacePrediction


# ==================================================================
# データキャリア
# ==================================================================

@dataclass(frozen=True)
class RuleApplication:
    """1 つのルールが 1 頭の馬に適用された 1 件の記録。"""
    rule_id: str            # canonical ID ("R9", "R24", "R3", "R6", "STEP2-A" ...)
    race_id: str
    racecourse: str
    race_number: int
    race_name: str
    horse_number: int
    horse_name: str
    detail: str             # 元の理由文字列(spec の数値根拠付き)


# 内部の理由文字列から rule_id を取り出す正規表現:
# - "R9: 芝1400m以下 良 上3F 33.0<33.3..."
# - "R24: 休養明け前走凡走 → ..."
# - "R23: ダート不良 + 逃げ → ○+1..."
_RULE_PREFIX_RE = re.compile(r"^R(\d+):\s*(.*)$")


def _parse_rule_id_from_reason(reason: str) -> str | None:
    """`Rxx: ...` 形式の理由文字列から canonical rule_id を取り出す。"""
    m = _RULE_PREFIX_RE.match(reason or "")
    if not m:
        return None
    return f"R{m.group(1)}"


# rule_3 / rule_4 / ... → "R3" / "R4" の正規化(WideCandidate.matched_rules 用)
_RULE_NUM_RE = re.compile(r"^rule_(\d+)$")


def _normalize_wide_rule_id(rule_str: str) -> str | None:
    m = _RULE_NUM_RE.match(rule_str or "")
    if not m:
        return None
    return f"R{m.group(1)}"


# ==================================================================
# 1 レース分の RuleApplication 抽出
# ==================================================================

def collect_applications_for_race(
    pred: RacePrediction,
) -> list[RuleApplication]:
    """1 レース分の予想結果から、発火したルール(R1〜R24)の適用例を全部拾う。"""
    apps: list[RuleApplication] = []

    meta = pred.race_meta
    rcourse = str(meta.get("racecourse", ""))
    rnum = int(meta.get("race_number") or 0)
    rname = str(meta.get("race_name", ""))
    rid = pred.race_id

    # ----- Step 1 / R9〜R22 / R24(各馬の matched_rules を spread) -----
    for h in pred.horses:
        for reason in h.matched_rules:
            rid_canon = _parse_rule_id_from_reason(reason)
            if rid_canon is None:
                continue
            apps.append(RuleApplication(
                rule_id=rid_canon,
                race_id=rid,
                racecourse=rcourse,
                race_number=rnum,
                race_name=rname,
                horse_number=h.horse_number,
                horse_name=h.horse_name,
                detail=reason,
            ))

    # ----- Step 3 / R6, R7(減点) -----
    for d in pred.demerit_entries:
        canon = _normalize_wide_rule_id(d.rule_id)  # "rule_6" → "R6"
        if canon is None:
            continue
        apps.append(RuleApplication(
            rule_id=canon,
            race_id=rid,
            racecourse=rcourse,
            race_number=rnum,
            race_name=rname,
            horse_number=d.horse_number,
            horse_name=d.horse_name,
            detail=d.reason,
        ))

    # ----- Step 4 / R3, R4, R5, R8(ワイド候補) -----
    for w in pred.wide_candidates:
        # WideCandidate.matched_rules = ["rule_3", "rule_4"], reasons = ["R3: ...", "R4: ..."]
        # reasons の方から canonical id を取りつつ、見つからない場合は matched_rules フォールバック
        for reason in w.reasons:
            canon = _parse_rule_id_from_reason(reason)
            if canon is None:
                continue
            apps.append(RuleApplication(
                rule_id=canon,
                race_id=rid,
                racecourse=rcourse,
                race_number=rnum,
                race_name=rname,
                horse_number=w.horse_number,
                horse_name=w.horse_name,
                detail=reason,
            ))

    # ----- Step 2(本命判定の発火状況) -----
    j = pred.judgment
    if j.main_pick:
        axis = next((x for x in pred.horses if x.horse_id == j.main_pick), None)
        if axis:
            apps.append(RuleApplication(
                rule_id="STEP2-A",
                race_id=rid,
                racecourse=rcourse,
                race_number=rnum,
                race_name=rname,
                horse_number=axis.horse_number,
                horse_name=axis.horse_name,
                detail=(
                    f"○{axis.marks_count}個 + 人気{axis.popularity}番 → 本命確定"
                ),
            ))
    elif j.sub_pick:
        sub = next((x for x in pred.horses if x.horse_id == j.sub_pick), None)
        if sub:
            apps.append(RuleApplication(
                rule_id="STEP2-B",
                race_id=rid,
                racecourse=rcourse,
                race_number=rnum,
                race_name=rname,
                horse_number=sub.horse_number,
                horse_name=sub.horse_name,
                detail=(
                    f"○≥5 候補なし → 最高 ○{sub.marks_count}個 の {sub.horse_name} を準◎に"
                ),
            ))

    return apps


# ==================================================================
# 全レース横断の rule_id インデックス
# ==================================================================

def index_by_rule(
    predictions: dict[str, RacePrediction],
) -> dict[str, list[RuleApplication]]:
    """rule_id → 適用例リスト(全レース横断)。"""
    index: dict[str, list[RuleApplication]] = {}
    for pred in predictions.values():
        for app in collect_applications_for_race(pred):
            index.setdefault(app.rule_id, []).append(app)
    return index


# ==================================================================
# ◎が出にくい問題用のヒストグラム
# ==================================================================

@dataclass(frozen=True)
class MarksDistribution:
    """全レース通しての ○マーク分布。"""
    races_total: int                    # 集計対象レース数
    horses_total: int                   # 集計対象出走馬数
    races_with_main_pick: int           # ○≥5 で本命確定したレース数
    races_with_sub_pick_only: int       # 準◎ fallback したレース数
    histogram: dict[int, int]           # ○数 → 馬の頭数
    max_marks_per_race: dict[str, int]  # race_id → そのレースの最大○マーク数


def compute_marks_distribution(
    predictions: dict[str, RacePrediction],
) -> MarksDistribution:
    """全レース横断の ○マーク数分布を計算する。"""
    histogram: dict[int, int] = {}
    max_marks_per_race: dict[str, int] = {}
    races_with_main = 0
    races_with_sub = 0
    horses_total = 0

    for race_id, pred in predictions.items():
        if pred.judgment.main_pick:
            races_with_main += 1
        elif pred.judgment.sub_pick:
            races_with_sub += 1

        max_in_race = 0
        for h in pred.horses:
            histogram[h.marks_count] = histogram.get(h.marks_count, 0) + 1
            horses_total += 1
            if h.marks_count > max_in_race:
                max_in_race = h.marks_count
        max_marks_per_race[race_id] = max_in_race

    return MarksDistribution(
        races_total=len(predictions),
        horses_total=horses_total,
        races_with_main_pick=races_with_main,
        races_with_sub_pick_only=races_with_sub,
        histogram=histogram,
        max_marks_per_race=max_marks_per_race,
    )
