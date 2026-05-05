"""
開発・動作確認用のダミーサンプルデータを生成するユーティリティスクリプト。

実行例:
    python scripts/generate_samples.py

生成されるもの:
- data/samples/sample_race_card.csv             … 当日出馬表サンプル(東京1R-3R、各12頭)
- data/samples/sample_historical/horses.csv     … 過去登録馬 100頭
- data/samples/sample_historical/pedigree.csv   … 上記100頭の血統情報
- data/samples/sample_historical/races.csv      … 過去30レース分の結果(計約400行)

注意:
- 本物のJRAデータではなく、列構造と数値レンジだけリアルにした完全な架空データ。
- 乱数シードを固定しているため、何度実行しても同じCSVが生成される。
"""

# 標準ライブラリのみで完結させる(過去データ生成にpandasは不要なため)
import csv
import os
import random
from datetime import date, timedelta

# 乱数シードを固定 → 実行のたびに同じデータが出る(再現性確保)
random.seed(42)

# ===== 出力先ディレクトリ =====
SAMPLES_DIR = "data/samples"
HIST_SAMPLES_DIR = os.path.join(SAMPLES_DIR, "sample_historical")
os.makedirs(HIST_SAMPLES_DIR, exist_ok=True)
# 本番過去データの置き場(空ディレクトリでも成立するよう、空ディレクトリを用意)
os.makedirs("data/historical", exist_ok=True)

# ===== 馬名・人名・コース等の素材 =====
# 馬名は接頭辞+接尾辞の組合せで擬似的に生成(JRA登録馬の命名スタイルを参考にした架空名)
PREFIXES = [
    "サクラ", "メイショウ", "ヒカル", "ダイワ", "アドマイヤ", "キタサン",
    "ナリタ", "シンボリ", "テイエム", "マイネル", "クロフネ", "ロード",
    "ヴィクトリア", "シルバー", "ゴールド",
]
SUFFIXES = [
    "ブライト", "ファイト", "クラウン", "キング", "クイーン", "ストーム",
    "ドリーム", "フラッシュ", "シチー", "ルージュ", "プリンス", "アロー",
    "サンダー", "ブレイブ", "ビクトリー", "ジェット", "オーシャン", "スパーク",
]

# 騎手・調教師は実在感のあるダミー(完全に架空の組合せ)
JOCKEYS = [
    "武田太郎", "山口圭一", "川村翔太", "戸田優介", "横田武志", "斉藤典宏",
    "M.ロドリゲス", "福島祐二", "田中裕信", "三沢皇成", "松井弘平", "吉村隼斗",
]
TRAINERS = [
    "友田康夫", "藤野英昭", "国本栄", "中田充正", "矢野芳人", "池川泰寿",
    "音田秀孝", "高井友和", "須田尚介", "宮原敬介",
]

RACECOURSES = ["東京", "中山", "京都", "阪神", "中京"]
SURFACES = ["芝", "ダート"]
GOING_LIST = ["良", "稍重", "重", "不良"]
DISTANCES_TURF = [1200, 1400, 1600, 1800, 2000, 2400, 2500, 3000]
DISTANCES_DIRT = [1200, 1400, 1700, 1800, 2100]
RACE_NAMES = ["新馬", "未勝利", "1勝クラス", "2勝クラス", "3勝クラス", "オープン", "G3", "G2", "G1"]
SIRE_LINES = [
    "サンデーサイレンス系", "ノーザンダンサー系", "ミスタープロスペクター系",
    "ナスルーラ系", "ネイティヴダンサー系",
]
SEXES = ["牡", "牝", "セ"]


def make_horse_name() -> str:
    """ダミー馬名を生成"""
    return random.choice(PREFIXES) + random.choice(SUFFIXES)


def make_horse_id(n: int) -> str:
    """馬IDの整形 (H0001 形式)"""
    return f"H{n:04d}"


def write_csv(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    """
    CSVファイル出力。
    - 文字コード: UTF-8-sig (BOM 付き、Excel 互換)
    - 改行: LF (Python標準動作)
    プロジェクト規約: 出力CSVは UTF-8-sig 統一(README.md の「CSV エンコーディング規約」参照)。
    """
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# =====================================================================
# 1) 過去登録馬 (horses.csv) — 100頭
# =====================================================================
horses: list[dict] = []
for i in range(1, 101):
    starts = random.randint(3, 30)
    wins = random.randint(0, max(1, starts // 3))
    places = random.randint(0, max(1, (starts - wins) // 2))
    shows = random.randint(0, max(1, (starts - wins - places) // 2))
    horses.append({
        "horse_id": make_horse_id(i),
        "horse_name": make_horse_name(),
        "sex": random.choice(SEXES),
        "age": random.randint(3, 7),
        "sire": make_horse_name(),
        "dam": make_horse_name(),
        "dam_sire": make_horse_name(),
        "total_starts": starts,
        "wins": wins,
        "places": places,
        "shows": shows,
    })

# =====================================================================
# 2) 血統情報 (pedigree.csv) — horsesと同じ100頭分
# =====================================================================
pedigree: list[dict] = []
for h in horses:
    pedigree.append({
        "horse_id": h["horse_id"],
        "sire_line": random.choice(SIRE_LINES),
        "broodmare_sire_line": random.choice(SIRE_LINES),
        "inbreeding_score": round(random.uniform(0.0, 5.0), 2),
    })

# =====================================================================
# 3) 過去レース結果 (races.csv) — 30レース、各10〜16頭
# =====================================================================
# 本日基準日(CLAUDE.mdのcurrentDate=2026-05-03)から30日以内の過去レース
TODAY = date.fromisoformat("2026-05-03")

races: list[dict] = []
for _ in range(30):
    race_date = TODAY - timedelta(days=random.randint(1, 30))
    course = random.choice(RACECOURSES)
    race_no = random.randint(1, 12)
    race_id = f"R{race_date.strftime('%Y%m%d')}-{course[:1]}{race_no:02d}"

    surface = random.choice(SURFACES)
    distance = random.choice(DISTANCES_TURF if surface == "芝" else DISTANCES_DIRT)
    going = random.choice(GOING_LIST)
    race_name = random.choice(RACE_NAMES)
    field_size = random.randint(10, 16)

    # この出走馬リスト(過去馬から重複なくサンプル)
    selected = random.sample(horses, field_size)
    # 着順をシャッフル(1〜field_sizeの並び替え)
    finish_order = list(range(1, field_size + 1))
    random.shuffle(finish_order)
    # 人気順もシャッフル(1〜field_size)
    pop_order = list(range(1, field_size + 1))
    random.shuffle(pop_order)

    for idx, h in enumerate(selected):
        # 走破タイムをそれっぽく(距離 / 16.5 m/s 程度を基準にバラす)
        sec_total = distance / 16.5 + random.uniform(-2.0, 2.0)
        minutes = int(sec_total // 60)
        seconds = sec_total - minutes * 60
        time_str = f"{minutes}:{seconds:05.2f}" if minutes > 0 else f"{seconds:05.2f}"

        races.append({
            "race_id": race_id,
            "race_date": race_date.isoformat(),
            "racecourse": course,
            "race_number": race_no,
            "race_name": race_name,
            "distance": distance,
            "surface": surface,
            "going": going,
            "finishing_position": finish_order[idx],
            "horse_id": h["horse_id"],
            "horse_name": h["horse_name"],
            "jockey": random.choice(JOCKEYS),
            "trainer": random.choice(TRAINERS),
            "weight": random.randint(420, 540),
            "weight_change": random.randint(-10, 10),
            "time": time_str,
            "last_3f": round(random.uniform(33.0, 38.0), 1),
            "popularity": pop_order[idx],
            "odds": round(random.uniform(1.5, 200.0), 1),
        })

# =====================================================================
# 4) 当日出馬表 (sample_race_card.csv) — 東京 1R/2R/3R 各12頭
# =====================================================================
race_card: list[dict] = []
for race_no in [1, 2, 3]:
    course = "東京"
    surface = "芝" if race_no != 2 else "ダート"  # 2Rだけダート
    distance = random.choice(DISTANCES_TURF if surface == "芝" else DISTANCES_DIRT)
    going = "良"
    race_name = random.choice(["新馬", "未勝利", "1勝クラス"])
    race_id = f"R{TODAY.strftime('%Y%m%d')}-{course[:1]}{race_no:02d}"

    # 12頭中: 過去データあり10頭、まったくの新馬2頭(過去データなしのテスト用)
    known = random.sample(horses, 10)
    new_horses = []
    for j in range(2):
        new_id = make_horse_id(100 + (race_no - 1) * 2 + j + 1)
        new_horses.append({"horse_id": new_id, "horse_name": make_horse_name()})
    field = known + new_horses
    random.shuffle(field)

    for idx, h in enumerate(field):
        race_card.append({
            "race_id": race_id,
            "race_date": TODAY.isoformat(),
            "racecourse": course,
            "race_number": race_no,
            "race_name": race_name,
            "distance": distance,
            "surface": surface,
            "going": going,
            "horse_number": idx + 1,                     # 馬番(1〜12、ランダムシャッフル後の順)
            "horse_id": h["horse_id"],
            "horse_name": h["horse_name"],
            "jockey": random.choice(JOCKEYS),
            "trainer": random.choice(TRAINERS),
            "weight": random.randint(420, 540),
            "weight_change": random.randint(-10, 10),
            "popularity": idx + 1,                       # 暫定: 並び順を人気として代用
            "odds": round(random.uniform(1.5, 200.0), 1),
        })

# =====================================================================
# CSV書き出し
# =====================================================================
write_csv(
    os.path.join(HIST_SAMPLES_DIR, "horses.csv"),
    horses,
    ["horse_id", "horse_name", "sex", "age", "sire", "dam", "dam_sire",
     "total_starts", "wins", "places", "shows"],
)
write_csv(
    os.path.join(HIST_SAMPLES_DIR, "pedigree.csv"),
    pedigree,
    ["horse_id", "sire_line", "broodmare_sire_line", "inbreeding_score"],
)
write_csv(
    os.path.join(HIST_SAMPLES_DIR, "races.csv"),
    races,
    ["race_id", "race_date", "racecourse", "race_number", "race_name",
     "distance", "surface", "going", "finishing_position", "horse_id",
     "horse_name", "jockey", "trainer", "weight", "weight_change",
     "time", "last_3f", "popularity", "odds"],
)
write_csv(
    os.path.join(SAMPLES_DIR, "sample_race_card.csv"),
    race_card,
    ["race_id", "race_date", "racecourse", "race_number", "race_name",
     "distance", "surface", "going", "horse_number", "horse_id", "horse_name",
     "jockey", "trainer", "weight", "weight_change", "popularity", "odds"],
)

print(f"horses.csv     : {len(horses)} 行")
print(f"pedigree.csv   : {len(pedigree)} 行")
print(f"races.csv      : {len(races)} 行 (30レース)")
print(f"sample_race_card.csv : {len(race_card)} 行 (3レース)")
