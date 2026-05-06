"""
本ロジック v1.0 / Step 1: ○マーク収集ルールエンジン。

CLAUDE.md「推奨馬選定ロジック(本ロジック v1.0)」の Step 1 を実装する純関数群。
Phase 2 のスコープ: ルール 9〜22(上がり3F + 通過順位の14本)+ ルール 24(休養明け救済)。

設計方針:
- Streamlit / pandas DataFrame には直接依存しない(prediction_logic.py から純粋に呼べる形)。
- 各ルールは「過去走 1 行 + 当日レース情報」を入力にとり、(該当?, 理由文字列) を返す。
- 同じ馬で同じルールが複数の過去走で該当しても、○ は最大 1 個までしかカウントしない。
- ルールごとの仕様は RuleSpec の data table に集約し、ロジックは evaluate_rule() で共通化。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

# 阪神・中山は閾値が緩い(0.2〜0.5秒分)場合がある — 各ルールの special_threshold で個別指定
SPECIAL_TRACKS = frozenset({"阪神", "中山"})

# 重馬場とみなす going 値
HEAVY_GOINGS = frozenset({"重", "不良"})


@dataclass(frozen=True)
class RuleSpec:
    """ルール 9〜22 の宣言的仕様。"""
    rule_no: int
    surface: str                   # "芝" or "ダ"
    distance_match: Callable[[int], bool]
    going_dry: bool                # True=良馬場、False=重馬場(重 or 不良)
    threshold: float               # 上がり 3F 閾値(これ未満なら該当)
    special_threshold: float | None  # 阪神中山時の閾値(None なら通常閾値を使う)
    requires_improvement: bool     # 通過順位改善が必要か
    distance_label: str            # ログ表示用


# ------------------------------------------------------------------
# ルール 9〜22 のスペック表(spec.md と完全対応)
# ------------------------------------------------------------------
RULES_9_TO_22: list[RuleSpec] = [
    # 芝・短距離
    RuleSpec(9,  "芝", lambda d: d <= 1400, True,  33.3, 33.5, True,  "1400m以下"),
    RuleSpec(10, "芝", lambda d: d <= 1400, False, 34.0, 34.2, True,  "1400m以下"),
    # 芝・マイル
    RuleSpec(11, "芝", lambda d: d == 1600, True,  34.2, None, True,  "1600m"),
    # ルール12 だけ通過順位改善は要求しない(spec の特例)
    RuleSpec(12, "芝", lambda d: d == 1600, False, 35.0, None, False, "1600m"),
    # 芝・中距離
    RuleSpec(13, "芝", lambda d: 1800 <= d <= 2000, True,  34.0, 34.5, True, "1800-2000m"),
    RuleSpec(14, "芝", lambda d: 1800 <= d <= 2000, False, 35.0, 35.5, True, "1800-2000m"),
    # 芝・長距離
    RuleSpec(15, "芝", lambda d: d >= 2200, True,  35.0, 35.5, True, "2200m以上"),
    RuleSpec(16, "芝", lambda d: d >= 2200, False, 35.5, 36.0, True, "2200m以上"),
    # ダート・短距離
    RuleSpec(17, "ダ", lambda d: d <= 1400, True,  35.0, None, True, "1400m以下"),
    RuleSpec(18, "ダ", lambda d: d <= 1400, False, 36.0, None, True, "1400m以下"),
    # ダート・中距離
    RuleSpec(19, "ダ", lambda d: 1600 <= d <= 2000, True,  36.0, None, True, "1600-2000m"),
    RuleSpec(20, "ダ", lambda d: 1600 <= d <= 2000, False, 35.5, None, True, "1600-2000m"),
    # ダート・長距離
    RuleSpec(21, "ダ", lambda d: d >= 2200, True,  37.0, None, True, "2200m以上"),
    RuleSpec(22, "ダ", lambda d: d >= 2200, False, 36.5, None, True, "2200m以上"),
]


# ==================================================================
# 共通ヘルパ
# ==================================================================

def is_heavy_going(going: str | None) -> bool:
    """重馬場(重 or 不良)か。 良 / 稍重 は False。"""
    if going is None:
        return False
    return str(going).strip() in HEAVY_GOINGS


def is_dry_going(going: str | None) -> bool:
    """良馬場か。 稍重 / 重 / 不良 は False(spec が「良馬場」と「重馬場」二分のため
    稍重は明示分類されない → どのルールにも該当させない安全側挙動)。"""
    if going is None:
        return False
    return str(going).strip() == "良"


def has_pass_order_improvement(run: dict) -> bool:
    """
    通過順位が「後半のコーナーほど位置を上げている(=順位が小さくなる)」か。

    定義(spec の例 10→8→5→3 を素直に解釈):
      - corner_1, corner_2, corner_3, corner_4 のうち valid なものだけ拾い、
      - 最初の通過順位 > 最後の通過順位 を満たすなら True(全体として位置を上げた)。
      - valid な値が 2 個未満なら False(短距離・直線等で判定不能)。
    """
    corners: list[int] = []
    for k in ("corner_1", "corner_2", "corner_3", "corner_4"):
        v = run.get(k)
        if v is None:
            continue
        try:
            if pd.isna(v):
                continue
        except (TypeError, ValueError):
            pass
        try:
            corners.append(int(v))
        except (ValueError, TypeError):
            continue
    if len(corners) < 2:
        return False
    return corners[0] > corners[-1]


def _format_corners(run: dict) -> str:
    """ログ用に通過順を 'a-b-c-d' 形式に整形(欠損は ?)。"""
    parts: list[str] = []
    for k in ("corner_1", "corner_2", "corner_3", "corner_4"):
        v = run.get(k)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            parts.append("?")
        else:
            try:
                parts.append(str(int(v)))
            except (ValueError, TypeError):
                parts.append("?")
    return "-".join(parts)


# ==================================================================
# 単一ルール × 単一過去走 の評価
# ==================================================================

def matches_any_onmark_rule(past_run: dict | None) -> tuple[bool, list[str]]:
    """
    過去走 1 行が Rule 9〜22 の少なくとも 1 本に該当するか判定する。

    UI 側(recent_runs_renderer)で「○ルール該当走 → 緑文字」を判定するための
    薄いラッパ。collect_onmarks() の中身そのものを再利用すると「同一ルールは
    1走で1回」という馬単位の制約が混ざってしまうため、ここでは過去走 1 行に
    対する即時判定だけを行う。

    引数:
        past_run: 過去走 1 行(キー: surface/distance/going/last_3f/racecourse/
                  corner_1..4)。None や全欠損は (False, []) を返す。

    戻り値:
        (該当?, 該当したルール ID のリスト)
        例: (True, ["R9", "R15"])  /  (False, [])

    呼び出し例:
        is_pass, ids = matches_any_onmark_rule(run)
        if is_pass:
            css_class = "last3f-pass"
            tooltip = f"{', '.join(ids)} 該当"
    """
    if past_run is None:
        return False, []
    matched: list[str] = []
    for rule in RULES_9_TO_22:
        ok, _reason = evaluate_rule(rule, past_run)
        if ok:
            matched.append(f"R{rule.rule_no}")
    return (len(matched) > 0, matched)


def evaluate_rule(rule: RuleSpec, run: dict) -> tuple[bool, str]:
    """
    ある過去走 1 行に対して 1 つのルールが該当するか判定する。

    引数:
        rule: 評価するルール仕様
        run:  過去走 1 行(dict、必要キー: surface, distance, going, last_3f,
              racecourse, corner_1..4)

    戻り値:
        (True/False, 理由文字列)
        該当しないときは ("", False) ではなく (False, "") を返す
        理由文字列は UI / ログでの根拠表示に使う。
    """
    # ----- 芝/ダ -----
    surface = str(run.get("surface", "")).strip()
    if surface != rule.surface:
        return False, ""

    # ----- 距離(整数化して match 関数に通す) -----
    distance = run.get("distance")
    if distance is None:
        return False, ""
    try:
        if pd.isna(distance):
            return False, ""
    except (TypeError, ValueError):
        pass
    try:
        d_int = int(distance)
    except (ValueError, TypeError):
        return False, ""
    if not rule.distance_match(d_int):
        return False, ""

    # ----- 馬場(良 or 重) -----
    going = str(run.get("going", "")).strip()
    if rule.going_dry and not is_dry_going(going):
        return False, ""
    if (not rule.going_dry) and not is_heavy_going(going):
        return False, ""

    # ----- 上がり 3F -----
    last_3f = run.get("last_3f")
    if last_3f is None:
        return False, ""
    try:
        if pd.isna(last_3f):
            return False, ""
        l3f = float(last_3f)
    except (TypeError, ValueError):
        return False, ""

    racecourse = str(run.get("racecourse", "")).strip()
    use_special = (
        rule.special_threshold is not None and racecourse in SPECIAL_TRACKS
    )
    threshold = rule.special_threshold if use_special else rule.threshold
    if l3f >= threshold:
        return False, ""

    # ----- 通過順位改善(必要なルールのみ) -----
    if rule.requires_improvement and not has_pass_order_improvement(run):
        return False, ""

    # ----- 該当 → 理由文字列を組み立てる -----
    going_label = "良" if rule.going_dry else "重"
    track_note = f"({racecourse}特例)" if use_special else ""
    reason = (
        f"R{rule.rule_no}: {rule.surface}{rule.distance_label} {going_label} "
        f"上3F {l3f:.1f}<{threshold:.1f}{track_note}"
    )
    if rule.requires_improvement:
        reason += f" + 通過順 {_format_corners(run)}"
    return True, reason


# ==================================================================
# ルール 24: 休養明け前走凡走 の救済
# ==================================================================

REST_THRESHOLD_DAYS = 180  # 前走と前々走の間がこれ以上なら「休養明け」
POOR_RESULT_THRESHOLD = 5  # 5着以下を「凡走」扱い


def detect_rule_24_situation(past_runs: list[dict | None]) -> bool:
    """
    ルール 24 が発動する状況か判定。
      条件1: 前走 と 2走前 の race_date 差が 180日以上(=前走が休養明け)
      条件2: 前走の着順が 5着以下(凡走)
    両方満たすと True → 評価対象を 2走前・3走前 に切り替えるシグナル。
    """
    if past_runs is None or len(past_runs) < 2:
        return False
    prev = past_runs[0]
    prev2 = past_runs[1]
    if prev is None or prev2 is None:
        return False

    # 日付差
    try:
        d_prev = pd.Timestamp(prev["race_date"])
        d_prev2 = pd.Timestamp(prev2["race_date"])
        gap_days = (d_prev - d_prev2).days
    except (KeyError, ValueError, TypeError):
        return False
    if gap_days < REST_THRESHOLD_DAYS:
        return False

    # 前走着順
    pos = prev.get("finishing_position")
    if pos is None:
        return False
    try:
        if pd.isna(pos):
            return False
        return int(pos) >= POOR_RESULT_THRESHOLD
    except (TypeError, ValueError):
        return False


# ==================================================================
# 馬1頭分の○マーク集計
# ==================================================================

def collect_onmarks(past_runs: list[dict | None]) -> tuple[int, list[str]]:
    """
    馬 1 頭の直近 5 走を入力に、Step 1 ルール群で ○ マーク数を集計する。

    引数:
        past_runs: get_recent_n_runs() の戻り値想定。
                   [前走, 2走前, 3走前, 4走前, 5走前] の順、不足は None。

    戻り値:
        (○マーク数, 該当ルールの理由文字列リスト)

    評価対象の選択(ルール 24 が決定):
        - 通常: 直近5走を全て評価(各ルールは 1走で1回までしか該当しない仕組みなので、
          芝/ダ・距離区分・良/重 が異なる過去走で別ルールが該当することで○が累積する)
        - ルール 24 発動時: 前走を除外して 2走前 + 3走前 のみ評価(spec の救済仕様)
    """
    if not past_runs:
        return 0, []

    rule24_active = detect_rule_24_situation(past_runs)
    if rule24_active:
        # 救済: 2走前 と 3走前 のみを評価対象にする(前走+4,5走前は除外)
        targets = [
            past_runs[i]
            for i in (1, 2)
            if i < len(past_runs) and past_runs[i] is not None
        ]
    else:
        # 通常: 直近5走を全て評価
        targets = [r for r in past_runs[:5] if r is not None]

    if not targets:
        # ルール 24 発動も評価対象なし、通常も前走なし → ○ 0 個
        notes: list[str] = []
        if rule24_active:
            notes.append("R24: 休養明け+前走凡走を検出したが 2,3走前データなし")
        return 0, notes

    # 同じルールが複数の過去走で該当しても 1 個までに制限
    fired: dict[int, str] = {}
    for run in targets:
        for rule in RULES_9_TO_22:
            if rule.rule_no in fired:
                continue
            ok, reason = evaluate_rule(rule, run)
            if ok:
                fired[rule.rule_no] = reason

    reasons = [fired[k] for k in sorted(fired.keys())]
    if rule24_active:
        reasons.append("R24: 休養明け前走凡走 → 2,3走前で評価")
    return len(fired), reasons
