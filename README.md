# neage-watch-data

値上がりウォッチ アプリのデータリポジトリです。

## 更新方法

1. `prices.json` を編集
2. `lastUpdated` の日付を更新
3. `git push` するだけで全ユーザーに反映

## JSONフォーマット

```json
{
  "lastUpdated": "YYYY-MM-DD",
  "items": [
    {
      "id": "固定UUID",
      "productName": "商品名",
      "company": "メーカー",
      "category": "food|utilities|dining|daily|transport|other",
      "increaseRate": 0.10,
      "increaseDate": "YYYY-MM-DD",
      "reason": "値上げ理由",
      "source": "情報源",
      "referencePrice": 220
    }
  ]
}
```
