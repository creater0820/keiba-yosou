"""
本ロジック v1.0 / Step 2-4: 判定エンジン。

CLAUDE.md「推奨馬選定ロジック(本ロジック v1.0)」の Step 2(◎本命判定)、
Step 3(減点・除外)、Step 4(ワイド候補抽出)を担当する純関数群。

入力は dict / list / DataFrame の組み合わせで、Streamlit / pandas は
DataFrame アクセスのみで使い、UI とは完全分離。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd

# Step 2 の◎候補閾値(spec)
HONMEI_MARK_THRESHOLD = 5

# Step 3 / 減点ルール
SPECIAL_HANSHIN_DEMERIT_DISTANCE = 1600   # 阪神1600m + 外枠 → 3着以下扱い
OUTER_FRAMES = frozenset({7, 8})          # 外枠 = 7枠 or 8枠

# Step 4 / ワイド候補
RULE4_BAD_POSITIONS = frozenset({5, 7, 9, 11, 13})  # 前走着順がこれらなら ワイド候補
RULE5_FRAME = 1                                       # 1枠
RULE5_MIN_POPULARITY = 5                              # 5番人気以降
RULE3_MIN_POPULARITY = 7                              # 7番人気以降
RULE8_TRACKS = frozenset({"小倉", "中京"})            # 小倉 / 中京 + 逃げ
WIDE_CANDIDATE_LIMIT = 3                              # 最大3頭


# ==================================================================
# データキャリア
# ==================================================================

@dataclass
class HorseMarkData:
    """各馬の○マーク情報。Phase 2 の collect_onmarks 結果 + メタ情報。"""
    horse_id: str
    horse_name: str
    horse_number: int               # 馬番
    frame_number: int               # 枠番
    popularity: int                 # 人気(1=1番人気)
    running_style: str              # "逃げ" / "先行" / "差し" / "追込" / "不明(先行扱い)"
    marks_count: int                # ○マーク数
    matched_rules: list[str] = field(default_factory=list)  # 該当ルール理由
    last_finishing_position: int | None = None              # 前走着順(rule_4 用)


@dataclass
class WideCandidate:
    """ワイド候補の選出情報。"""
    horse_id: str
    horse_name: str
    horse_number: int
    popularity: int
    matched_rules: list[str]      # ['rule_3', 'rule_4'] のような番号リスト
    reasons: list[str]            # 人間可読な理由文字列
    priority: int = 0             # 該当ルール数(優先度)


@dataclass
class DemeritEntry:
    """減点対象馬の情報。"""
    horse_id: str
    horse_name: str
    horse_number: int
    downgrade_to: int             # 2 = 2着以下扱い、3 = 3着以下扱い
    rule_id: str                  # "rule_6" / "rule_7"
    reason: str


# ==================================================================
# Step 3: 減点ルール
# ==================================================================

def detect_demerit_horses(horses: list[HorseMarkData], race_meta: dict) -> list[DemeritEntry]:
    """
    減点ルール(rule_6, rule_7)に該当する馬のリストを返す。

    rule_6: 単勝1番人気 かつ 逃げ脚質 → 2着以下扱い
    rule_7: 阪神1600m + 7枠 or 8枠     → 3着以下扱い

    引数:
        horses: 全馬の HorseMarkData リスト
        race_meta: {"racecourse": str, "distance": int, ...} の dict

    戻り値: 該当馬の DemeritEntry リスト(該当馬のみ)
    """
    entries: list[DemeritEntry] = []
    racecourse = str(race_meta.get("racecourse", "")).strip()
    try:
        distance = int(race_meta.get("distance") or 0)
    except (ValueError, TypeError):
        distance = 0

    is_hanshin_mile = (
        racecourse == "阪神" and distance == SPECIAL_HANSHIN_DEMERIT_DISTANCE
    )

    for h in horses:
        # rule_6: 1番人気の逃げ
        if h.popularity == 1 and h.running_style == "逃げ":
            entries.append(DemeritEntry(
                horse_id=h.horse_id,
                horse_name=h.horse_name,
                horse_number=h.horse_number,
                downgrade_to=2,
                rule_id="rule_6",
                reason="単勝1番人気かつ逃げ脚質 → 軸馬には不適(2着以下扱い)",
            ))
        # rule_7: 阪神1600m + 外枠
        if is_hanshin_mile and h.frame_number in OUTER_FRAMES:
            entries.append(DemeritEntry(
                horse_id=h.horse_id,
                horse_name=h.horse_name,
                horse_number=h.horse_number,
                downgrade_to=3,
                rule_id="rule_7",
                reason=f"阪神1600m + {h.frame_number}枠(外枠) → 3着以下扱い",
            ))
    return entries


# ==================================================================
# Step 2: ◎本命判定
# ==================================================================

@dataclass
class JudgmentResult:
    """determine_main_pick の戻り値。"""
    main_pick: str | None          # 本命馬の horse_id(候補なしなら None)
    sub_pick: str | None           # 準本命(◎候補ゼロ時の最高○マーク馬)
    main_pick_marks: int           # 本命の○マーク数
    sub_pick_marks: int            # 準本命の○マーク数
    candidates: list[str]          # 当初の◎候補(○≥5)
    excluded_by_demerit: list[str] # 減点で除外された horse_id
    demerit_entries: list[DemeritEntry]
    reason: str                    # 人間可読な要約


def determine_main_pick(
    horses: list[HorseMarkData],
    race_meta: dict,
) -> JudgmentResult:
    """
    Step 2-3 を統合して ◎本命を決定する。

    手順:
        a) ○マーク 5個以上 を◎候補
        b) 減点ルール(rule_6, rule_7)に該当する馬を候補から除外
        c) 残った候補が複数なら 人気で昇順タイブレーク(1番人気優先)
        d) 候補ゼロ → ◎なし、最高○マーク数の馬を準本命として返す
    """
    # a) ○候補
    candidates_raw = [h for h in horses if h.marks_count >= HONMEI_MARK_THRESHOLD]
    candidate_ids = [h.horse_id for h in candidates_raw]

    # b) 減点
    demerits = detect_demerit_horses(horses, race_meta)
    demerit_ids = {d.horse_id for d in demerits}
    candidates_after = [h for h in candidates_raw if h.horse_id not in demerit_ids]
    excluded = [hid for hid in candidate_ids if hid in demerit_ids]

    # c) タイブレーク(人気昇順)
    if candidates_after:
        winner = min(
            candidates_after,
            key=lambda h: (h.popularity if h.popularity > 0 else 999),
        )
        return JudgmentResult(
            main_pick=winner.horse_id,
            sub_pick=None,
            main_pick_marks=winner.marks_count,
            sub_pick_marks=0,
            candidates=candidate_ids,
            excluded_by_demerit=excluded,
            demerit_entries=demerits,
            reason=(
                f"◎候補 {len(candidates_raw)} 頭(減点除外 {len(excluded)} 頭)→ "
                f"人気{winner.popularity}番 {winner.horse_name} を本命に確定"
            ),
        )

    # d) 候補ゼロ → 準本命を立てる
    valid = [h for h in horses if h.horse_id not in demerit_ids]
    if valid:
        sub = max(valid, key=lambda h: (h.marks_count, -h.popularity if h.popularity else -999))
        return JudgmentResult(
            main_pick=None,
            sub_pick=sub.horse_id,
            main_pick_marks=0,
            sub_pick_marks=sub.marks_count,
            candidates=candidate_ids,
            excluded_by_demerit=excluded,
            demerit_entries=demerits,
            reason=(
                f"◎候補(○≥{HONMEI_MARK_THRESHOLD})なし → "
                f"準本命=最高○{sub.marks_count}個 の {sub.horse_name}"
            ),
        )

    return JudgmentResult(
        main_pick=None,
        sub_pick=None,
        main_pick_marks=0,
        sub_pick_marks=0,
        candidates=candidate_ids,
        excluded_by_demerit=excluded,
        demerit_entries=demerits,
        reason="該当馬なし(全頭減点)",
    )


# ==================================================================
# Step 4: ワイド候補抽出
# ==================================================================

def _is_adjacent_frame(a: int, b: int) -> bool:
    """枠 a と b が隣接(±1)か。"""
    return abs(a - b) == 1


def extract_wide_candidates(
    horses: list[HorseMarkData],
    race_meta: dict,
) -> list[WideCandidate]:
    """
    Step 4 のワイド候補(最大 WIDE_CANDIDATE_LIMIT 頭)を抽出する。

    rule_3: 単勝1番人気の隣枠 + 7番人気以降 → ワイド候補
    rule_4: 前走着順が 5/7/9/11/13 → ワイド候補
    rule_5: 1枠の逃げ馬 + 5番人気以降 → ワイド候補
    rule_8: 小倉 or 中京 + 逃げ脚質 → ワイド候補

    優先度:複数該当 → 上位、同数なら人気上位を優先。
    """
    racecourse = str(race_meta.get("racecourse", "")).strip()
    is_kokura_or_chukyo = racecourse in RULE8_TRACKS

    # 1番人気の枠を取得(rule_3 の判定に使用)
    fav = next((h for h in horses if h.popularity == 1), None)
    fav_frame = fav.frame_number if fav else None

    bucket: dict[str, WideCandidate] = {}

    for h in horses:
        matched: list[str] = []
        reasons: list[str] = []

        # rule_3: 1番人気の隣枠 + 7番人気以降
        if (
            fav_frame is not None
            and h.popularity >= RULE3_MIN_POPULARITY
            and _is_adjacent_frame(h.frame_number, fav_frame)
            and h.horse_id != (fav.horse_id if fav else None)
        ):
            matched.append("rule_3")
            reasons.append(f"R3: 1番人気({fav.horse_name}/枠{fav_frame})の隣枠 + {h.popularity}番人気")

        # rule_4: 前走着順が 5/7/9/11/13
        if (
            h.last_finishing_position is not None
            and h.last_finishing_position in RULE4_BAD_POSITIONS
        ):
            matched.append("rule_4")
            reasons.append(f"R4: 前走{h.last_finishing_position}着 = ワイド候補着順")

        # rule_5: 1枠の逃げ + 5番人気以降
        if (
            h.frame_number == RULE5_FRAME
            and h.running_style == "逃げ"
            and h.popularity >= RULE5_MIN_POPULARITY
        ):
            matched.append("rule_5")
            reasons.append(f"R5: 1枠の逃げ馬 + {h.popularity}番人気")

        # rule_8: 小倉/中京 + 逃げ
        if is_kokura_or_chukyo and h.running_style == "逃げ":
            matched.append("rule_8")
            reasons.append(f"R8: {racecourse}の逃げ馬")

        if matched:
            bucket[h.horse_id] = WideCandidate(
                horse_id=h.horse_id,
                horse_name=h.horse_name,
                horse_number=h.horse_number,
                popularity=h.popularity,
                matched_rules=matched,
                reasons=reasons,
                priority=len(matched),
            )

    # 優先度降順 → 同点は人気昇順
    candidates = sorted(
        bucket.values(),
        key=lambda c: (-c.priority, c.popularity if c.popularity > 0 else 999),
    )
    return candidates[:WIDE_CANDIDATE_LIMIT]


# ==================================================================
# 集計補助(prediction_logic.py での統合用)
# ==================================================================

def compute_popularities_from_odds(odds_series: pd.Series) -> pd.Series:
    """
    単勝オッズ昇順で人気を計算する(1=最低オッズ=1番人気)。
    odds が NaN や 0 の馬は人気末尾(0 を返す = "人気なし"表記)。
    """
    valid = pd.to_numeric(odds_series, errors="coerce")
    # 同オッズの場合はインデックス順で stable に
    rank = valid.rank(method="min", ascending=True, na_option="bottom")
    rank = rank.fillna(0).astype(int)
    return rank


# ==================================================================
# v2 (rating-based): 100点以上で◎ + ワイド候補抽出
# ==================================================================

def detect_demerit_horses_from_ratings(
    horse_ratings: list,  # list[HorseRating]
    race_meta: dict,
) -> list[DemeritEntry]:
    """
    rating ベース判定でも減点ルール B1/B2 は同じセマンティクスで適用する。
    HorseRating の属性を使って既存 detect_demerit_horses 相当を実行する。
    """
    entries: list[DemeritEntry] = []
    racecourse = str(race_meta.get("racecourse", "")).strip()
    try:
        distance = int(race_meta.get("distance") or 0)
    except (ValueError, TypeError):
        distance = 0
    is_hanshin_mile = (
        racecourse == "阪神" and distance == SPECIAL_HANSHIN_DEMERIT_DISTANCE
    )

    for h in horse_ratings:
        if h.popularity == 1 and h.running_style == "逃げ":
            entries.append(DemeritEntry(
                horse_id=h.horse_id,
                horse_name=h.horse_name,
                horse_number=h.horse_number,
                downgrade_to=2,
                rule_id="B1",
                reason="単勝1番人気かつ逃げ脚質 → 軸馬には不適(2着以下扱い)",
            ))
        if is_hanshin_mile and h.frame_number in OUTER_FRAMES:
            entries.append(DemeritEntry(
                horse_id=h.horse_id,
                horse_name=h.horse_name,
                horse_number=h.horse_number,
                downgrade_to=3,
                rule_id="B2",
                reason=f"阪神1600m + {h.frame_number}枠(外枠) → 3着以下扱い",
            ))
    return entries


def determine_main_pick_v2(
    horse_ratings: list,  # list[HorseRating]
    race_meta: dict,
    threshold: int = 100,
):
    """
    rating ベースの本命判定。

    手順:
        a) total_rating ≥ threshold(既定 100)を ◎候補に
        b) 減点ルール B1/B2 該当馬を候補から除外
        c) 残った候補が複数なら 人気で昇順タイブレーク
        d) 候補ゼロ → ◎なし、最高 rating 馬を準◎として返す

    戻り値: JudgmentResult(main_pick_marks/sub_pick_marks フィールドは
            rating 値を流用、UI 互換性を保つため既存 dataclass を再利用)
    """
    candidates_raw = [h for h in horse_ratings if h.total_rating >= threshold]
    candidate_ids = [h.horse_id for h in candidates_raw]

    demerits = detect_demerit_horses_from_ratings(horse_ratings, race_meta)
    demerit_ids = {d.horse_id for d in demerits}
    candidates_after = [h for h in candidates_raw if h.horse_id not in demerit_ids]
    excluded = [hid for hid in candidate_ids if hid in demerit_ids]

    if candidates_after:
        # 同 rating なら人気上位、同人気なら馬番(stable tiebreak)
        winner = min(
            candidates_after,
            key=lambda h: (
                -h.total_rating,
                h.popularity if h.popularity > 0 else 999,
                h.horse_number,
            ),
        )
        return JudgmentResult(
            main_pick=winner.horse_id,
            sub_pick=None,
            main_pick_marks=winner.total_rating,
            sub_pick_marks=0,
            candidates=candidate_ids,
            excluded_by_demerit=excluded,
            demerit_entries=demerits,
            reason=(
                f"rating ≥ {threshold} の候補 {len(candidates_raw)} 頭"
                f"(減点除外 {len(excluded)} 頭) → 人気{winner.popularity}番 "
                f"{winner.horse_name} を本命に確定(rating {winner.total_rating})"
            ),
        )

    # 候補ゼロ → 準本命を立てる
    valid = [h for h in horse_ratings if h.horse_id not in demerit_ids]
    if valid:
        sub = max(
            valid,
            key=lambda h: (
                h.total_rating,
                -(h.popularity if h.popularity > 0 else 999),
            ),
        )
        return JudgmentResult(
            main_pick=None,
            sub_pick=sub.horse_id,
            main_pick_marks=0,
            sub_pick_marks=sub.total_rating,
            candidates=candidate_ids,
            excluded_by_demerit=excluded,
            demerit_entries=demerits,
            reason=(
                f"rating ≥ {threshold} の候補なし → "
                f"準本命=最高 rating {sub.total_rating} の {sub.horse_name}"
            ),
        )

    return JudgmentResult(
        main_pick=None,
        sub_pick=None,
        main_pick_marks=0,
        sub_pick_marks=0,
        candidates=candidate_ids,
        excluded_by_demerit=excluded,
        demerit_entries=demerits,
        reason="該当馬なし(全頭減点)",
    )


def extract_wide_candidates_v2(
    horse_ratings: list,
    race_meta: dict,
) -> list[WideCandidate]:
    """
    rating ベースのワイド候補抽出。判定 v1 の rule 3/4/5/8 と同じ条件。
    rule_id は新仕様(A2/A3/A4/A5)で命名し直す。
    """
    racecourse = str(race_meta.get("racecourse", "")).strip()
    is_kokura_or_chukyo = racecourse in RULE8_TRACKS

    fav = next((h for h in horse_ratings if h.popularity == 1), None)
    fav_frame = fav.frame_number if fav else None

    bucket: dict[str, WideCandidate] = {}

    for h in horse_ratings:
        matched: list[str] = []
        reasons: list[str] = []

        # A2: 1番人気の隣枠 + 7番人気以降
        if (
            fav_frame is not None
            and h.popularity >= RULE3_MIN_POPULARITY
            and _is_adjacent_frame(h.frame_number, fav_frame)
            and h.horse_id != (fav.horse_id if fav else None)
        ):
            matched.append("A2")
            reasons.append(
                f"A2: 1番人気({fav.horse_name}/枠{fav_frame})の隣枠 + {h.popularity}番人気"
            )

        # A3: 前走着順が 5/7/9/11/13
        if (
            h.last_finishing_position is not None
            and h.last_finishing_position in RULE4_BAD_POSITIONS
        ):
            matched.append("A3")
            reasons.append(f"A3: 前走{h.last_finishing_position}着 = ワイド候補着順")

        # A4: 1枠の逃げ + 5番人気以降
        if (
            h.frame_number == RULE5_FRAME
            and h.running_style == "逃げ"
            and h.popularity >= RULE5_MIN_POPULARITY
        ):
            matched.append("A4")
            reasons.append(f"A4: 1枠の逃げ馬 + {h.popularity}番人気")

        # A5: 小倉/中京 + 逃げ
        if is_kokura_or_chukyo and h.running_style == "逃げ":
            matched.append("A5")
            reasons.append(f"A5: {racecourse}の逃げ馬")

        if matched:
            bucket[h.horse_id] = WideCandidate(
                horse_id=h.horse_id,
                horse_name=h.horse_name,
                horse_number=h.horse_number,
                popularity=h.popularity,
                matched_rules=matched,
                reasons=reasons,
                priority=len(matched),
            )

    candidates = sorted(
        bucket.values(),
        key=lambda c: (-c.priority, c.popularity if c.popularity > 0 else 999),
    )
    return candidates[:WIDE_CANDIDATE_LIMIT]


def get_last_finishing_positions(
    horse_ids: Iterable[str],
    target_date: str,
    historical_df: pd.DataFrame,
) -> dict[str, int | None]:
    """各馬の前走着順を返す(target_date より前で最も新しい走)。"""
    result: dict[str, int | None] = {}
    hids = set(str(h) for h in horse_ids)
    relevant = historical_df[historical_df["horse_id"].astype(str).isin(hids)].copy()
    if relevant.empty:
        return {hid: None for hid in hids}
    relevant["_d"] = pd.to_datetime(relevant["race_date"], errors="coerce")
    relevant = relevant[relevant["_d"] < pd.Timestamp(target_date)]
    relevant = relevant.sort_values("_d", ascending=False)

    for hid in hids:
        sub = relevant[relevant["horse_id"].astype(str) == hid]
        if sub.empty:
            result[hid] = None
            continue
        pos = sub.iloc[0]["finishing_position"]
        try:
            if pd.isna(pos):
                result[hid] = None
            else:
                result[hid] = int(pos)
        except (TypeError, ValueError):
            result[hid] = None
    return result
