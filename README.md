# UNION ARENA CARD DB

UNION ARENA公式カードリストを取得し、検索・絞り込み・オフライン表示に対応した静的PWAを生成する単一リポジトリ構成です。

このリポジトリだけで次の処理を行えます。

- 公式カードリストから商品・カード・パラレル情報を取得
- `cards.json`を生成・差分更新
- 必要に応じてカード画像をリポジトリ内へ保存
- GitHub PagesでカードDBを公開
- GitHub Actionsで最新商品を自動同期

## ローカル実行

Python 3.11以降を使用します。

```bash
python -m pip install -r requirements.txt
python scripts/sync_cards.py --series latest
python -m http.server 8000
```

ブラウザで `http://localhost:8000/` を開いてください。

### 全商品を取得

```bash
python scripts/sync_cards.py --series all
```

### 商品を指定

公式サイト内の商品IDまたは商品コードを指定できます。

```bash
python scripts/sync_cards.py --series 570154
python scripts/sync_cards.py --series UA54BT
python scripts/sync_cards.py --series 570154,570153
```

### 画像も保存

通常は公式画像URLを `cards.json` に保持します。完全な同一オリジン配信やオフライン運用が必要な場合だけ画像保存を有効にしてください。

```bash
python scripts/sync_cards.py --series latest --download-images
```

画像は `Cards/<商品コード>/` に保存されます。

## GitHub Actions

- `Update card database`: 毎日、公式サイトの最新商品を確認して `cards.json` を更新
- `Deploy GitHub Pages`: `main` ブランチ更新時に静的サイトをGitHub Pagesへ公開

全商品を初回同期する場合は、Actions画面から `Update card database` を手動実行し、`series` に `all` を指定してください。

## データ形式

主要項目:

- `cardNumber`: 公式カード番号
- `cardName`, `furigana`, `rarity`, `cardType`
- `title`, `product`, `productCode`, `seriesCode`
- `color`, `needEnergy`, `ap`, `bp`
- `features`, `generatedEnergy`
- `effectText`, `trigger`, `triggerType`
- `variants`: 通常版・パラレル版の画像とレアリティ

## 注意

カード情報・画像などの権利は各権利者に帰属します。公式サイトの利用条件を確認し、アクセス頻度と公開範囲に配慮して利用してください。

- 公式サイト: https://www.unionarena-tcg.com/jp/
- 公式カードリスト: https://www.unionarena-tcg.com/jp/cardlist/

