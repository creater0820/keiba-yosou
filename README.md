# 競馬予想アプリ (keiba-yosou)

JRA中央競馬の予想を自動化する Streamlit Web アプリ。
個人利用専用(商用配布なし)。

---

## 👨‍🦳 利用者(お父様)向け使い方

### 1. アプリを開く
お知らせした URL(例: `https://keiba-yosou.streamlit.app/`)をブラウザのお気に入りに登録して、当日アクセスする。

> インストール作業は **一切不要** です。Edge / Chrome / Firefox いずれもOK。

### 2. 当日出馬表 CSV を準備
JRA-VAN DataLab(または TARGET frontier JV)から、
レース当日の **出馬表 CSV** をエクスポートする。

### 3. アップロード〜予想実行
1. 画面中央の「**当日出馬表 CSV をアップロード**」エリアにファイルをドラッグ&ドロップ
2. レース数・出走頭数が表示されるので確認
3. 「🎯 **予想実行**」ボタンをクリック
4. レースごとに ◎(本命)○(対抗)▲(単穴)△(連下)が表示される
5. 各馬の評価理由はクリックで展開可能

### 4. 結果を保存したい場合
「📥 **予想結果を CSV でダウンロード**」ボタンで、全レース分の予想結果を CSV 保存できる。

### 困ったとき
- ファイルが読めないと表示される → CSV のフォーマットが想定と違う可能性あり。Yasu に連絡。
- 「過去データなし」と表示される馬 → 新馬・初出走の馬。スコアは参考値のみ。

---

## 🛠 開発者(Yasu)向け

### セットアップ

```bash
# 仮想環境作成
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# ローカル起動
streamlit run app.py
```

ブラウザが自動で開く(`http://localhost:8501`)。
サイドバーで過去データの読み込み状態(本番Parquet / サンプルCSV)を確認できる。

### ディレクトリ構成

```
keiba-yosou/
├── app.py                          # Streamlit エントリーポイント (UI)
├── prediction_logic.py             # 予想ロジック(差し替え可能)
├── data_loader.py                  # データ読み込み・バリデーション
├── requirements.txt                # 依存パッケージ
├── CLAUDE.md                       # Claude Code 用プロジェクト指示
├── README.md                       # このファイル
├── .streamlit/config.toml          # Streamlit 設定(テーマ・上限サイズ)
├── data/
│   ├── historical/                 # 本番過去データ (Parquet, gitignore)
│   │   ├── races.parquet
│   │   ├── horses.parquet
│   │   └── pedigree.parquet
│   └── samples/                    # 開発用ダミーデータ (CSV, リポジトリ同梱)
│       ├── sample_race_card.csv
│       └── sample_historical/
│           ├── races.csv
│           ├── horses.csv
│           └── pedigree.csv
├── scripts/
│   └── generate_samples.py         # サンプルデータ再生成スクリプト
└── tests/                          # ロジック単体テスト(MVP後追加予定)
```

### 過去データの更新フロー

1. JRA-VAN DataLab から最新の過去データを取得
2. ローカルで pandas を使って Parquet に変換し、`data/historical/*.parquet` に配置
   ```python
   import pandas as pd
   df = pd.read_csv("races_2026.csv")
   df.to_parquet("data/historical/races.parquet")
   ```
3. ファイルサイズを確認(GitHub の単一ファイル上限 100MB に注意)
4. `git add data/historical/*.parquet -f`(`.gitignore` で除外しているので `-f` 必須)
5. `git commit -m "data: 過去データを 2026-MM-DD 時点に更新"` → push
6. Streamlit Cloud が自動再デプロイ

> サンプルデータを再生成したい場合: `python scripts/generate_samples.py`

### 依存パッケージの更新

```bash
pip install <new-package>
pip freeze > requirements.txt
git add requirements.txt
git commit -m "build: <new-package> を追加"
```

### 予想ロジックの差し替え

`prediction_logic.py` の `DEFAULT_RULES` リストにスコアリング関数を追加・削除するだけで挙動を変えられる。

```python
def rule_my_logic(horse_row, hist):
    # お父様の本ロジック
    return score, "理由文字列"

DEFAULT_RULES = [rule_my_logic]   # 既存ルールを置き換え
```

### Streamlit Community Cloud へのデプロイ

1. GitHub に **Public** リポジトリとして push
2. https://share.streamlit.io/ にログイン(GitHub 連携)
3. 「**New app**」 → リポジトリ・ブランチ(`main`)・メインファイル(`app.py`)を指定
4. 「Deploy!」を押すと数分で公開 URL が発行される
5. 以後、`main` への push は自動で再デプロイされる

> Streamlit Cloud の無料プランは 1GB メモリ。1年分の過去データは Parquet で十数MB なので余裕で収まる。

### 開発ルール

- 機能追加・修正は `feature/xxx` ブランチを切る(`main` 直編集禁止)
- コミットは [Conventional Commits](https://www.conventionalcommits.org/ja/v1.0.0/) 形式
  (`feat:`, `fix:`, `docs:`, `build:` など)
- `main` は常に Streamlit Cloud で動作する状態を維持

---

## 制約・注意事項

- 公開リポジトリのため、**機密情報(個人情報・購入履歴・予算等)を含めない**
- 「的中保証」「投資成績」のような表現を UI に入れない
- 第三者への配信機能は作らない(個人利用専用)

詳細仕様は `CLAUDE.md` を参照。
