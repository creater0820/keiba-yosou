# 競馬予想アプリ (keiba-yosou)

JRA中央競馬の予想を自動化する Streamlit Web アプリ。
個人利用専用(商用配布なし)。

---

## 👨‍🦳 利用者向け使い方

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

## 🛠 開発者向け

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
│   ├── historical/                 # 本番過去データ (Parquet, リポジトリ同梱)
│   │   └── races.parquet           # JV-Link 由来。horses/pedigree は今後追加
│   ├── raw/                        # 受領 CSV 置き場 (.gitignore)
│   └── samples/                    # 開発用ダミーデータ (CSV)
│       ├── sample_race_card.csv
│       └── sample_historical/
├── docs/
│   └── UPDATE_DATA.md              # 過去データ差し替え手順
├── scripts/
│   ├── generate_samples.py         # サンプルデータ再生成
│   └── csv_to_parquet.py           # 受領CSV → Parquet 変換
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

### Streamlit Community Cloud へのデプロイ手順

a. https://share.streamlit.io にアクセス
b. **GitHub 連携でログイン**(初回はリポジトリ閲覧権限を許可)
c. 右上「**New app**」 → 「**Deploy a public app from GitHub**」を選択
   - Repository: `creater0820/keiba-yosou`
   - Branch: `main`
   - Main file path: `app.py`
   - App URL (任意): `keiba-yosou` 等
d. **Advanced settings** で Python version `3.11` を指定(任意だが推奨)
e. 「**Deploy**」をクリック → 依存パッケージのインストールに数分かかる
f. デプロイ完了後、`https://<your-app-name>.streamlit.app/` で公開される
   - 以後 `main` への push 毎に **自動で再デプロイ** される
   - 再デプロイ中も旧バージョンは閉じられず、ダウンタイムは実質ゼロ

> Streamlit Cloud の無料プランは 1GB メモリ。1年分の過去データは Parquet で十数MB なので余裕で収まる。

### 過去データの差し替え

詳細は [docs/UPDATE_DATA.md](docs/UPDATE_DATA.md) 参照。
要約: 受領した CSV を `data/raw/` に置き、
`python scripts/csv_to_parquet.py` を実行して
`data/historical/*.parquet` を生成 → commit & push。

### CSV エンコーディング規約

入力は柔軟、**出力は UTF-8-sig で統一**、というポリシー:

| 種別 | 規約 |
|---|---|
| **出力 CSV**(アプリ生成・スクリプト出力) | **UTF-8-sig (BOM 付き)** で統一(Excel 互換) |
| **入力 CSV**(アップロード・読み込み) | UTF-8-sig / UTF-8 / Shift_JIS / cp932 を自動判定 |
| 過去データ生CSV(`data/raw/`、TARGET エクスポート) | Shift_JIS のままで OK(読み込み時に自動変換) |

実装担当箇所:
- 出力: `scripts/generate_samples.py` の `write_csv()`、`scripts/extract_one_day.py`、
  `app.py` の予想結果ダウンロードボタン → すべて `encoding="utf-8-sig"`
- 入力: `data_loader.load_race_card()` が `utils.target_format.decode_with_fallback()` を
  使って 4 種類のエンコーディングを順に試行

新しい出力 CSV を追加するときは UTF-8-sig を明示すること:

```python
df.to_csv(path, encoding="utf-8-sig", index=False)
# pandas は to_csv の encoding="utf-8-sig" で自動的に BOM を先頭に付与する
```

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
