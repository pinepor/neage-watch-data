#!/usr/bin/env python3
"""
値上がりウォッチ 月次データ更新スクリプト
毎月1日に GitHub Actions から自動実行される
"""

import json
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST)
NEXT_MONTH = (TODAY.replace(day=1) + timedelta(days=32)).replace(day=1)
TWO_MONTHS = (NEXT_MONTH + timedelta(days=32)).replace(day=1)

DATA_PATH = Path(__file__).parent.parent / "prices.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NeageWatchBot/1.0)"}


def load_existing() -> dict:
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def fetch_neage_blog() -> list[dict]:
    """neage.hateblo.jp から値上げ情報をスクレイピング"""
    items = []
    # 来月・再来月の月を対象URLに組み込む
    for target_date in [NEXT_MONTH, TWO_MONTHS]:
        month_str = target_date.strftime("%Y/%m")
        url = f"https://neage.hateblo.jp/archive/{month_str}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            # 記事タイトルから品目情報を抽出
            for entry in soup.select("a.entry-title-link"):
                title = entry.get_text(strip=True)
                item = parse_title(title, target_date)
                if item:
                    items.append(item)
        except Exception as e:
            print(f"  [warn] neage.hateblo.jp fetch failed: {e}")
    return items


def parse_title(title: str, target_date: datetime) -> dict | None:
    """記事タイトルから品目・カテゴリ・値上げ率を推定"""
    # カテゴリキーワードマッピング
    cat_map = [
        (["食品", "食べ", "食料", "パン", "麺", "米", "醤油", "みそ", "酒", "飲料"], "food"),
        (["光熱", "電気", "ガス", "水道"], "utilities"),
        (["外食", "ファスト", "レストラン", "牛丼", "ラーメン"], "dining"),
        (["日用品", "洗剤", "シャンプー", "ティッシュ", "洗濯"], "daily"),
        (["交通", "バス", "電車", "タクシー"], "transport"),
    ]
    category = "other"
    for keywords, cat in cat_map:
        if any(kw in title for kw in keywords):
            category = cat
            break

    # タイトルが短すぎる・意味なさそうなら除外
    if len(title) < 5:
        return None

    # 値上げ率を抽出（例: +10%、10%アップ）
    rate_match = re.search(r"[＋+](\d+(?:\.\d+)?)[%％]", title)
    if rate_match:
        rate = float(rate_match.group(1)) / 100
    else:
        rate = 0.08  # デフォルト: 8%

    increase_date = target_date.strftime("%Y-%m-01")

    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, title + increase_date)),
        "productName": title[:40],
        "company": "各社",
        "category": category,
        "increaseRate": rate,
        "increaseDate": increase_date,
        "reason": "原材料・物流費高騰",
        "source": "値上げまとめ（neage.hateblo.jp）",
        "referencePrice": default_price(category),
    }


def default_price(category: str) -> int:
    return {
        "food": 250,
        "utilities": 6000,
        "dining": 400,
        "daily": 400,
        "transport": 400,
        "other": 300,
    }.get(category, 300)


def merge_items(existing_items: list[dict], new_items: list[dict]) -> list[dict]:
    """既存データを保持しつつ新規データをマージ。IDが重複する場合は既存優先"""
    existing_ids = {item["id"] for item in existing_items}
    # 来月以降の既存データは削除して新データで上書き
    cutoff = TODAY.strftime("%Y-%m-01")
    kept = [i for i in existing_items if i["increaseDate"] >= cutoff]
    kept_ids = {i["id"] for i in kept}

    added = 0
    for item in new_items:
        if item["id"] not in kept_ids and item["id"] not in existing_ids:
            kept.append(item)
            added += 1

    print(f"  既存データ保持: {len(kept) - added}件, 新規追加: {added}件")
    return sorted(kept, key=lambda x: x["increaseDate"])


def main():
    print(f"[{TODAY.strftime('%Y-%m-%d %H:%M JST')}] 月次データ更新開始")

    existing = load_existing()
    existing_items = existing.get("items", [])
    print(f"  現在のデータ: {len(existing_items)}件")

    print("  スクレイピング中: neage.hateblo.jp")
    new_items = fetch_neage_blog()
    print(f"  取得: {len(new_items)}件")

    merged = merge_items(existing_items, new_items)

    output = {
        "lastUpdated": TODAY.strftime("%Y-%m-%d"),
        "items": merged,
    }

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  完了: 合計{len(merged)}件 → prices.json 更新済み")


if __name__ == "__main__":
    main()
