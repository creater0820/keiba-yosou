"""
馬番 → 枠番(1〜8)の変換ヘルパ。

JRA の 8枠制ルール:
- 出走頭数 ≤ 8: 馬番 = 枠番
- 出走頭数 9〜16: 8枠を 1頭ずつ埋めてから、後ろの枠に2頭目を追加していく
  (詰める順:8枠目から後ろ向きに 1頭ずつ "double 枠" を増やす)
- 出走頭数 17,18: 全枠 2頭になった上で、後ろから 3頭枠を作る

具体的にはこのテーブル(JRA 公式の枠順割当ルール):
   N= 9  → 馬番1-7 が枠1-7、馬番8,9 が枠8
   N=10  → 馬番1-6 が枠1-6、馬番7,8 が枠7、馬番9,10 が枠8
   N=11  → 馬番1-5 が枠1-5、馬番6,7 が枠6、馬番8,9 が枠7、馬番10,11 が枠8
   N=12  → 馬番1-4 が枠1-4、5,6→枠5、7,8→枠6、9,10→枠7、11,12→枠8
   N=13  → 馬番1-3 が枠1-3、4,5→枠4、6,7→枠5、8,9→枠6、10,11→枠7、12,13→枠8
   N=14  → 馬番1,2 が枠1,2、3,4→枠3、5,6→枠4、…、13,14→枠8
   N=15  → 馬番1 が枠1、2,3→枠2、4,5→枠3、…、14,15→枠8
   N=16  → 全枠 2頭ずつ(1,2→枠1、3,4→枠2、…、15,16→枠8)
   N=17  → 1,2→枠1、3,4→枠2、5,6→枠3、7,8→枠4、9,10→枠5、11,12→枠6、
           13,14→枠7、15,16,17→枠8(枠8 が 3 頭)
   N=18  → 1,2→枠1、3,4→枠2、5,6→枠3、7,8→枠4、9,10→枠5、11,12→枠6、
           13,14,15→枠7、16,17,18→枠8(枠7・枠8 が 3 頭ずつ)
"""

from __future__ import annotations


def horse_number_to_frame(horse_number: int, field_size: int) -> int:
    """
    馬番(1..field_size)+ 出走頭数 → 枠番(1..8)。

    引数:
        horse_number: 馬番(1〜field_size の整数)
        field_size:   出走頭数(1〜18)

    戻り値:
        枠番(1〜8)

    Raises:
        ValueError: horse_number または field_size が範囲外の場合
    """
    if not (1 <= horse_number <= field_size):
        raise ValueError(
            f"horse_number={horse_number} は field_size={field_size} の範囲外"
        )
    if not (1 <= field_size <= 18):
        raise ValueError(f"field_size={field_size} は 1〜18 の範囲外")

    # ----- N ≤ 8: 馬番 = 枠番 -----
    if field_size <= 8:
        return horse_number

    # ----- N = 9〜16: 1頭枠 + 2頭枠 -----
    # 1頭枠の数 = 16 - N(枠1から N=15 まで連続)
    # 残りの (N - (16 - N)) = (2N - 16) 馬番が 2頭枠領域、N-8 個の2頭枠
    if field_size <= 16:
        single_frames = 16 - field_size  # N=9 → 7、N=10 → 6、…、N=16 → 0
        if horse_number <= single_frames:
            # 1頭枠: 馬番 = 枠番
            return horse_number
        # 2頭枠領域: 馬番 (single_frames+1)〜N が枠 (single_frames+1)〜8
        offset_in_double = horse_number - single_frames  # 1始まり
        return single_frames + ((offset_in_double - 1) // 2) + 1

    # ----- N = 17, 18: 全枠 2頭の前提に、後ろから 3頭枠を作る -----
    # 3頭枠の数 = N - 16
    # 2頭枠の数 = 8 - (N - 16) = 24 - N
    # 馬番割当:
    #   2頭枠領域(枠1〜double_count): 馬番 1..(double_count*2) が 2頭ずつ
    #   3頭枠領域(枠 double_count+1〜8): 残り馬番が 3頭ずつ
    triple_count = field_size - 16   # N=17→1、N=18→2
    double_count = 8 - triple_count  # N=17→7、N=18→6

    threshold = double_count * 2  # 2頭枠で詰めきる馬番の上限
    if horse_number <= threshold:
        return ((horse_number - 1) // 2) + 1
    # 3頭枠領域
    offset_in_triple = horse_number - threshold  # 1始まり
    return double_count + ((offset_in_triple - 1) // 3) + 1


# =====================================================================
# テスト用テーブル(JRA 公式割当の正解値)
# =====================================================================
# 単体テストで使う想定。本ファイルを直接実行すると assert チェックが走る。
_EXPECTED_TABLE = {
    # field_size: { horse_number: frame_number }
    1:  {1: 1},
    8:  {i: i for i in range(1, 9)},
    9:  {1:1,2:2,3:3,4:4,5:5,6:6,7:7,8:8,9:8},
    10: {1:1,2:2,3:3,4:4,5:5,6:6,7:7,8:7,9:8,10:8},
    11: {1:1,2:2,3:3,4:4,5:5,6:6,7:6,8:7,9:7,10:8,11:8},
    12: {1:1,2:2,3:3,4:4,5:5,6:5,7:6,8:6,9:7,10:7,11:8,12:8},
    13: {1:1,2:2,3:3,4:4,5:4,6:5,7:5,8:6,9:6,10:7,11:7,12:8,13:8},
    14: {1:1,2:2,3:3,4:3,5:4,6:4,7:5,8:5,9:6,10:6,11:7,12:7,13:8,14:8},
    15: {1:1,2:2,3:2,4:3,5:3,6:4,7:4,8:5,9:5,10:6,11:6,12:7,13:7,14:8,15:8},
    16: {1:1,2:1,3:2,4:2,5:3,6:3,7:4,8:4,9:5,10:5,11:6,12:6,13:7,14:7,15:8,16:8},
    17: {1:1,2:1,3:2,4:2,5:3,6:3,7:4,8:4,9:5,10:5,11:6,12:6,13:7,14:7,15:8,16:8,17:8},
    18: {1:1,2:1,3:2,4:2,5:3,6:3,7:4,8:4,9:5,10:5,11:6,12:6,13:7,14:7,15:7,16:8,17:8,18:8},
}


def _self_test():
    fails = 0
    for n, table in _EXPECTED_TABLE.items():
        for hn, expected_frame in table.items():
            got = horse_number_to_frame(hn, n)
            if got != expected_frame:
                print(f"  ✗ N={n} hn={hn}: expected {expected_frame}, got {got}")
                fails += 1
    if fails == 0:
        total = sum(len(v) for v in _EXPECTED_TABLE.values())
        print(f"✓ 全 {total} 組み合わせで JRA 公式割当と一致")
    else:
        print(f"✗ {fails} 件失敗")


if __name__ == "__main__":
    _self_test()
