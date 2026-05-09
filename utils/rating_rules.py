"""
本ロジック v1.1 / Rating-based 判定の **ルール仕様 SSoT**(Single Source of Truth)。

CLAUDE.md「推奨馬選定ロジック v1.1(rating ベース)」の全 39 ルールを
構造化データとして保持する。実評価は utils/rating_engine.py が担う。

カテゴリ:
  A — 買い目戦略系(per-horse rating には加算しない、別フラグ)
       A1: 偶奇枠フィルタ          (rate 2、global strategy)
       A2-A5: ワイド候補フラグ + priority weight (rate 4/5/7/30)
  B — 減点・降格系(本命候補から除外フラグ、rating には加算しない)
       B1: 1番人気の逃げ → 2着以下扱い (rate 25)
       B2: 阪神1600m + 7,8枠 → 3着以下扱い (rate 15)
  C — 距離×馬場 別 上3F + 通過順位改善  (rate 50、各 rule 1走で1回まで)
  D — 距離無関係 上3F + 通過順位改善    (rate 20)
  E — 距離×馬場 別 上3F のみ           (rate 20、通過順位改善 不要)
  F — 補正・特殊条件                   (F1 ダ不良逃げ +30 / F2 救済 +15
                                         / F3 斤量-3kg +20 / F4-F5 坂路 TODO)

C / D / E の 同一過去走で複数該当 → 最高 rate のみ採用(over-counting 防止)。
同一 rule_id が複数走で発火 → 1 回までしかカウントしない。

注: F4 / F5(坂路調教 1F/2F ≤ 11.2)は調教データが現状の TARGET CSV /
parquet に含まれていないため未実装。ルール定義のみ残し、データソースが
拡張されたら enabled=True に切り替えられるようにしている。
"""

from __future__ import annotations

from dataclasses import dataclass

from utils.onmark_rules import RULES_9_TO_22, RuleSpec


# =====================================================================
# データキャリア
# =====================================================================

@dataclass(frozen=True)
class RatingRule:
    """1 ルールの宣言的定義。"""
    rule_id: str          # "A1" / "B1" / "C1" / "D1" / "E1" / "F1" 等
    category: str         # "strategy" | "wide" | "demerit" | "C" | "D" | "E" | "F"
    rate: int             # ルールの値(rating 加算 / 優先度 / メタ)
    title: str            # 1 行見出し
    description: str      # 詳細(複数行 OK)
    contributes_to_rating: bool  # True のものだけ total_rating に加算される
    spec: RuleSpec | None = None  # C/D/E 用。evaluate_rule に渡せる仕様
    enabled: bool = True          # F4/F5 等、データ未対応で評価不能なら False


# =====================================================================
# C: 距離×馬場 別 上3F + 通過順位改善 (rate 50)
# =====================================================================
# 既存の RULES_9_TO_22 と完全 1:1 対応(同じ条件、ID と rate だけ付け替え)。
# rule_no 9..22 → C1..C14。新仕様への移行時のレビュー容易性を優先して
# evaluate_rule の入力として再利用する。

_C_LABELS = [
    "芝 ≤1400m 良 上3F<33.3秒(阪神/中山<33.5)+ 通過順位改善",
    "芝 ≤1400m 重 上3F<34.0秒(阪神/中山<34.2)+ 通過順位改善",
    "芝 1600m 良 上3F<34.2秒 + 通過順位改善",
    "芝 1600m 重 上3F<35.0秒(spec の特例で通過順位改善は要求しない)",
    "芝 1800-2000m 良 上3F<34.0秒(阪神/中山<34.5)+ 通過順位改善",
    "芝 1800-2000m 重 上3F<35.0秒(阪神/中山<35.5)+ 通過順位改善",
    "芝 ≥2200m 良 上3F<35.0秒(阪神/中山<35.5)+ 通過順位改善",
    "芝 ≥2200m 重 上3F<35.5秒(阪神/中山<36.0)+ 通過順位改善",
    "ダ ≤1400m 良 上3F<35.0秒 + 通過順位改善",
    "ダ ≤1400m 重 上3F<36.0秒 + 通過順位改善",
    "ダ 1600-2000m 良 上3F<36.0秒 + 通過順位改善",
    "ダ 1600-2000m 重 上3F<35.5秒 + 通過順位改善",
    "ダ ≥2200m 良 上3F<37.0秒 + 通過順位改善",
    "ダ ≥2200m 重 上3F<36.5秒 + 通過順位改善",
]

RATING_RULES_C: list[RatingRule] = [
    RatingRule(
        rule_id=f"C{i+1}",
        category="C",
        rate=50,
        title=_C_LABELS[i],
        description=_C_LABELS[i],
        contributes_to_rating=True,
        spec=spec,
    )
    for i, spec in enumerate(RULES_9_TO_22)
]


# =====================================================================
# E: 距離×馬場 別 上3F のみ(通過順位改善 不要)(rate 20)
# =====================================================================
# C と同条件・同閾値だが requires_improvement=False。RuleSpec を新規生成。
# rule_no は衝突を避けるため 200 番台に置く。

def _drop_improvement(spec: RuleSpec, new_rule_no: int) -> RuleSpec:
    """C 系の RuleSpec から「通過順位改善」要件だけ落とした E 用 spec を作る。"""
    return RuleSpec(
        rule_no=new_rule_no,
        surface=spec.surface,
        distance_match=spec.distance_match,
        going_dry=spec.going_dry,
        threshold=spec.threshold,
        special_threshold=spec.special_threshold,
        requires_improvement=False,
        distance_label=spec.distance_label,
    )


_E_LABELS = [
    "芝 ≤1400m 良 上3F<33.3秒(阪神/中山<33.5)",
    "芝 ≤1400m 重 上3F<34.0秒(阪神/中山<34.2)",
    "芝 1600m 良 上3F<34.2秒",
    "芝 1600m 重 上3F<35.0秒",
    "芝 1800-2000m 良 上3F<34.0秒(阪神/中山<34.5)",
    "芝 1800-2000m 重 上3F<35.0秒(阪神/中山<35.5)",
    "芝 ≥2200m 良 上3F<35.0秒(阪神/中山<35.5)",
    "芝 ≥2200m 重 上3F<35.5秒(阪神/中山<36.0)",
    "ダ ≤1400m 良 上3F<35.0秒",
    "ダ ≤1400m 重 上3F<36.0秒",
    "ダ 1600-2000m 良 上3F<36.0秒",
    "ダ 1600-2000m 重 上3F<35.5秒",
    "ダ ≥2200m 良 上3F<37.0秒",
    "ダ ≥2200m 重 上3F<36.5秒",
]

RATING_RULES_E: list[RatingRule] = [
    RatingRule(
        rule_id=f"E{i+1}",
        category="E",
        rate=20,
        title=_E_LABELS[i],
        description=_E_LABELS[i],
        contributes_to_rating=True,
        spec=_drop_improvement(spec, new_rule_no=200 + i + 1),
    )
    for i, spec in enumerate(RULES_9_TO_22)
]


# =====================================================================
# D: 距離無関係 上3F + 通過順位改善 (rate 20)
# =====================================================================
# distance_match=lambda d: True にして「全距離マッチ」とみなす。
# rule_no は 300 番台を割り当てる(C/E と衝突回避)。

RATING_RULES_D: list[RatingRule] = [
    RatingRule(
        rule_id="D1",
        category="D",
        rate=20,
        title="芝 良 上3F<34.5秒(阪神/中山<35.0)+ 通過順位改善",
        description="距離無関係。芝 良馬場で上3F が速く通過順位改善あり → +20",
        contributes_to_rating=True,
        spec=RuleSpec(
            rule_no=301, surface="芝",
            distance_match=lambda d: True,
            going_dry=True, threshold=34.5, special_threshold=35.0,
            requires_improvement=True, distance_label="(any)",
        ),
    ),
    RatingRule(
        rule_id="D2",
        category="D",
        rate=20,
        title="芝 重 上3F<35.0秒 + 通過順位改善",
        description="距離無関係。芝 重 or 不良馬場で上3F が速く通過順位改善あり → +20",
        contributes_to_rating=True,
        spec=RuleSpec(
            rule_no=302, surface="芝",
            distance_match=lambda d: True,
            going_dry=False, threshold=35.0, special_threshold=None,
            requires_improvement=True, distance_label="(any)",
        ),
    ),
    RatingRule(
        rule_id="D3",
        category="D",
        rate=20,
        title="ダ 良 上3F<36.0秒 + 通過順位改善",
        description="距離無関係。ダ 良馬場で上3F が速く通過順位改善あり → +20",
        contributes_to_rating=True,
        spec=RuleSpec(
            rule_no=303, surface="ダ",
            distance_match=lambda d: True,
            going_dry=True, threshold=36.0, special_threshold=None,
            requires_improvement=True, distance_label="(any)",
        ),
    ),
    RatingRule(
        rule_id="D4",
        category="D",
        rate=20,
        title="ダ 重 上3F<36.5秒 + 通過順位改善",
        description="距離無関係。ダ 重 or 不良馬場で上3F が速く通過順位改善あり → +20",
        contributes_to_rating=True,
        spec=RuleSpec(
            rule_no=304, surface="ダ",
            distance_match=lambda d: True,
            going_dry=False, threshold=36.5, special_threshold=None,
            requires_improvement=True, distance_label="(any)",
        ),
    ),
]


# =====================================================================
# F: 補正・特殊条件
# =====================================================================
# 個別ロジックなので spec ではなく rating_engine 内で直接判定する。

RATING_RULES_F: list[RatingRule] = [
    RatingRule(
        rule_id="F1",
        category="F",
        rate=30,
        title="ダート不良馬場 + 逃げ脚質 → +30",
        description=(
            "今回レースが ダ + 不良 で、馬の脚質が逃げの場合に加点。"
            "馬場が荒れた時の逃げ馬の信頼度を rating に上乗せ。"
        ),
        contributes_to_rating=True,
    ),
    RatingRule(
        rule_id="F2",
        category="F",
        rate=15,
        title="休養明け前走凡走 → 2,3走前で C/D/E 救済評価 + ボーナス +15",
        description=(
            "前走と前々走の race_date 差が 180日以上 + 前走着順 5以下 の馬は、"
            "前走を評価対象から外して 2走前 と 3走前 を直近 2 走として"
            "C/D/E ルールを再評価する。救済評価で 1 本でも該当すれば本ルールも +15。"
        ),
        contributes_to_rating=True,
    ),
    RatingRule(
        rule_id="F3",
        category="F",
        rate=20,
        title="1600m以上 + 斤量 -3kg(前走比)→ +20",
        description=(
            "今回レース距離が 1600m 以上で、前走と比べて斤量が 3kg 以上軽い"
            "(carry_weight 差 ≤ -3.0)場合に加点。負担軽減で末脚伸びる期待値。"
        ),
        contributes_to_rating=True,
    ),
    RatingRule(
        rule_id="F4",
        category="F",
        rate=30,
        title="坂路調教 1F(Lap1)≤ 11.2",
        description=(
            "**v1.5 で実装**。お父様が別途アップロードする坂路調教 CSV を"
            "utils/training_data.py が読み、馬名で当日出馬表とマッチングして"
            "Lap1(ゴール直前 1F)≤ 11.2 秒で +30 加点。F5 と同時に該当する"
            "場合は F5 排他で F4 は採用しない(F5 が +40 で上位)。"
            "坂路 CSV 未アップロード時は永続無効(missed_rule_ids 入り)。"
        ),
        contributes_to_rating=True,
        enabled=True,
    ),
    RatingRule(
        rule_id="F5",
        category="F",
        rate=40,
        title="坂路調教 1F + 2F ともに ≤ 11.2",
        description=(
            "**v1.5 で実装**。坂路調教 Lap1(直前 1F)と Lap2(その前の 1F)"
            "両方が 11.2 秒以下なら +40。1F だけならば F4(+30)、両方なら"
            "F5(+40、F4 排他)。坂路 CSV 未アップロード時は永続無効。"
        ),
        contributes_to_rating=True,
        enabled=True,
    ),
]


# =====================================================================
# A: 買い目戦略系(per-horse rating には加算しない別フラグ)
# =====================================================================
# A1 は global filter(個別馬の rating ではなく買い目絞り込みフラグ)、
# A2-A5 はワイド候補マークの優先度。判定エンジン側でこれらの数を集計する。

RATING_RULES_A: list[RatingRule] = [
    RatingRule(
        rule_id="A1",
        category="strategy",
        rate=2,
        title="1番人気の枠の偶奇でワイド候補を絞り込み",
        description=(
            "global strategy。本命人気枠が奇数(1,3,5,7)→ ワイド候補を奇数枠に、"
            "偶数(2,4,6,8)→ 偶数枠に絞る。per-horse rating には加算しない。"
        ),
        contributes_to_rating=False,
    ),
    RatingRule(
        rule_id="A2",
        category="wide",
        rate=4,
        title="1番人気の隣枠 + 7番人気以降 → ワイド候補",
        description="該当馬を ワイド候補に。rate 4 はソート用 priority weight。",
        contributes_to_rating=False,
    ),
    RatingRule(
        rule_id="A3",
        category="wide",
        rate=5,
        title="前走着順が 5/7/9/11/13着 → ワイド候補",
        description="該当馬を ワイド候補に(priority weight 5)。",
        contributes_to_rating=False,
    ),
    RatingRule(
        rule_id="A4",
        category="wide",
        rate=7,
        title="1枠の逃げ馬 + 5番人気以降 → ワイド候補",
        description="該当馬を ワイド候補に(priority weight 7)。",
        contributes_to_rating=False,
    ),
    RatingRule(
        rule_id="A5",
        category="wide",
        rate=30,
        title="競馬場が小倉 or 中京 + 逃げ脚質 → ワイド候補",
        description="該当馬を ワイド候補に(priority weight 30、最高優先)。",
        contributes_to_rating=False,
    ),
]


# =====================================================================
# B: 減点・降格系(本命候補から除外フラグ)
# =====================================================================
# rating 加算には影響しない。判定エンジンが demerit_entries に格納し、
# 該当馬は ◎ 候補から外す(B1: 2着以下扱い、B2: 3着以下扱い)。

RATING_RULES_B: list[RatingRule] = [
    RatingRule(
        rule_id="B1",
        category="demerit",
        rate=25,
        title="1番人気 + 逃げ → 2着以下扱い(◎候補から除外)",
        description=(
            "1番人気の逃げ馬は他馬から狙われやすく軸馬には不適。"
            "rating 加算には影響しないが、◎候補から除外し 3 着候補までは可。"
        ),
        contributes_to_rating=False,
    ),
    RatingRule(
        rule_id="B2",
        category="demerit",
        rate=15,
        title="阪神1600m + 7枠 or 8枠 → 3着以下扱い",
        description=(
            "阪神 1600m の外枠は実績薄。◎候補から除外、3 着以下扱い。"
        ),
        contributes_to_rating=False,
    ),
]


# =====================================================================
# 集約: 全ルールフラットリスト
# =====================================================================

ALL_RATING_RULES: list[RatingRule] = (
    RATING_RULES_A
    + RATING_RULES_B
    + RATING_RULES_C
    + RATING_RULES_D
    + RATING_RULES_E
    + RATING_RULES_F
)


def find_rating_rule(rule_id: str) -> RatingRule | None:
    """rule_id から RatingRule を引く。未知 ID は None。"""
    for r in ALL_RATING_RULES:
        if r.rule_id == rule_id:
            return r
    return None


# =====================================================================
# 判定閾値(後続フェーズの judgment_engine が参照する)
# =====================================================================

# 100 点以上で ◎本命確定
HONMEI_RATING_THRESHOLD: int = 100

# 同 rule_id は直近10走中で 1回まで(rating_engine 側で実装、v1.4)
RULE_DEDUP_PER_HORSE: bool = True

# C/D/E 排他処理(過去走 1 行で C/D/E が複数該当 → 最高 rate のみ採用)
class RatingPolicy:
    """rating 集計時の重複処理ポリシー。"""
    STRICT = "strict"        # C/D/E 排他(spec デフォルト)
    SUM_ALL = "sum_all"      # C/D/E 全部加算(over-count、A/B テスト用)


DEFAULT_POLICY: str = RatingPolicy.STRICT
