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
                                         / F3 斤量-3kg +20
                                         / F4 坂路好調 +30 / F5 坂路抜群 +40)

C / D / E の 同一過去走で複数該当 → 最高 rate のみ採用(over-counting 防止)。
同一 rule_id が複数走で発火 → 1 回までしかカウントしない。

F4 / F5(坂路調教): v1.5 で実装、v1.7.5 で実測ベース閾値に緩和:
  - F5(+40): lap1 ≤ 12.3 秒 OR lap1+lap2 ≤ 24.8 秒(上位 ~12%)
  - F4(+30): lap1 ≤ 12.5 秒 OR lap1+lap2 ≤ 25.4 秒(F5 排他、上位 ~13%)
旧閾値 11.2 は業界トップ 1-2% 水準で実 CSV 発火 0 件だったため、
お父様の坂路 CSV 2265 サンプル分布に基づき穴馬検出ルールとして再設計。
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
        rate=50,  # v1.8.0 第 2 案: 50 維持(第 1 案で 45 にしたら人気馬が浮上せず悪化)
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
        rate=25,  # v1.8.0: 20 → 25(通過順位改善なしでも上3F速い ≒ 隠れ好走で増点)
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
        rate=30,  # v1.8.0 第 2 案: 30 維持(第 1 案で 35 にしても効果なく悪化要因)
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
        rate=25,  # v1.8.0: 20 → 25(斤量減は強いシグナル、人気に織込みされにくい)
        title="1600m以上 + 斤量 -3kg(前走比)→ +25",
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
        title="坂路調教 好調(lap1 ≤ 12.5 秒 OR 1F+2F ≤ 25.4 秒)",
        description=(
            "**v1.5 で実装、v1.7.5 で閾値緩和**。お父様の坂路調教 CSV から"
            "Lap1(ゴール直前 1F)≤ 12.5 秒 もしくは 1F+2F 累積 ≤ 25.4 秒 の"
            "馬に +30 加点(F5 排他で F4 は採用しない、F5 が +40 で上位)。"
            "実 CSV 2265 サンプルで上位 ~25% に該当する穴馬検出ルール。"
            "坂路 CSV 未アップロード時は永続無効(missed_rule_ids 入り)。"
        ),
        contributes_to_rating=True,
        enabled=True,
    ),
    RatingRule(
        rule_id="F5",
        category="F",
        rate=40,
        title="坂路調教 抜群(lap1 ≤ 12.3 秒 OR 1F+2F ≤ 24.8 秒)",
        description=(
            "**v1.5 で実装、v1.7.5 で閾値緩和**。Lap1 ≤ 12.3 秒 もしくは "
            "1F+2F 累積 ≤ 24.8 秒 で +40 加点(F5 が F4 より上位、排他)。"
            "実 CSV 2265 サンプルで上位 ~12% に該当する好走候補絞り込み。"
            "坂路 CSV 未アップロード時は永続無効。"
        ),
        contributes_to_rating=True,
        enabled=True,
    ),
    RatingRule(
        rule_id="F4穴",
        category="F",
        rate=15,  # v1.8.0 第 2 案: 15 維持(第 1 案 20 で穴馬過剰浮上 → 戻す)
        title="F4 該当 + 人気 ≥ 6番人気 → 穴馬上積み +15",
        description=(
            "**v1.7.5.1 で実装**。F4 該当 + 6 番人気以下の馬に追加 +15。"
            "F4(+30)と合算で +45 となり軸候補級。F5穴 と排他。"
        ),
        contributes_to_rating=True,
        enabled=True,
    ),
    RatingRule(
        rule_id="F5穴",
        category="F",
        rate=20,  # v1.8.0 第 2 案: 20 維持(第 1 案 30 で穴馬過剰浮上 → 戻す)
        title="F5 該当 + 人気 ≥ 6番人気 → 穴馬上積み +20",
        description=(
            "**v1.7.5.1 で実装**。F5 該当 + 6 番人気以下の馬に追加 +20。"
            "F5(+40)と合算で +60 となり本命級評価。"
        ),
        contributes_to_rating=True,
        enabled=True,
    ),
    # v1.8.0 で導入を検討した C穴 / D穴 / E穴 は、第 1 案バックテスト
    # (2026-04-01〜2026-05-03、69→86 レース ◎ 増)で複勝率 24.64% → 20.93%、
    # 単勝参考回収率 545% → 438% と全指標悪化したため第 2 案で撤回。
    # 穴馬狙いは F4穴/F5穴(坂路調教ベース)に集約する設計が最適と確定。
    # 将来再検討する時はバックテスト前提で配点を実測決定する。
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
        rate=6,  # v1.8.0: 4 → 6
        title="1番人気の隣枠 + 7番人気以降 → ワイド候補",
        description="該当馬を ワイド候補に。priority weight(rating 加算なし)。",
        contributes_to_rating=False,
    ),
    RatingRule(
        rule_id="A3",
        category="wide",
        rate=8,  # v1.8.0: 5 → 8
        title="前走着順が 5/7/9/11/13着 → ワイド候補",
        description="該当馬を ワイド候補に(priority weight 8)。",
        contributes_to_rating=False,
    ),
    RatingRule(
        rule_id="A4",
        category="wide",
        rate=12,  # v1.8.0: 7 → 12
        title="1枠の逃げ馬 + 5番人気以降 → ワイド候補",
        description="該当馬を ワイド候補に(priority weight 12)。",
        contributes_to_rating=False,
    ),
    RatingRule(
        rule_id="A5",
        category="wide",
        rate=35,  # v1.8.0: 30 → 35
        title="競馬場が小倉 or 中京 + 逃げ脚質 → ワイド候補",
        description="該当馬を ワイド候補に(priority weight 35、最高優先)。",
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
# G: コース特性バイアス補正(v1.9.0 Phase 1)
# =====================================================================
# course_bias_rules.py が SSoT。ここでは RatingRule に変換してロジック
# 説明ページに表示できるよう ALL_RATING_RULES に組み込む。
def _make_g_rating_rules() -> list[RatingRule]:
    """utils/course_bias_rules.py の G ルールから RatingRule リストを生成。"""
    from utils.course_bias_rules import ALL_G_RULES
    return [
        RatingRule(
            rule_id=g.rule_id,
            category=g.category,  # "G-Frame" or "G-Style"
            rate=g.rate,
            title=g.description,
            description=g.description,
            contributes_to_rating=True,
            enabled=True,
        )
        for g in ALL_G_RULES
    ]


RATING_RULES_G: list[RatingRule] = _make_g_rating_rules()


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
    + RATING_RULES_G
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
