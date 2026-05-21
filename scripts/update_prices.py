#!/usr/bin/env python3
"""
値上がりウォッチ 月次データ更新スクリプト
毎月1日に GitHub Actions から自動実行される

ブログ構造 (neage.hateblo.jp):
  タイトル: 「食品｜X月から値上げするもの一覧（2026年版）」
  本文:
    カテゴリ名（「食品」「お酒／アルコール」など）
    ■会社名
    製品名1
    製品名2
    ≫ 詳細はこちら
    （次の会社ブロックへ続く）
"""

import json
import re
import uuid
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST)
DATA_PATH = Path(__file__).parent.parent / "prices.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NeageWatchBot/1.0)"}

CATEGORY_MAP = [
    (["食品", "飲料", "調味料", "缶詰", "冷凍", "乳製品", "菓子", "お米", "パン", "麺"], "food"),
    (["お酒", "アルコール", "ビール", "ワイン", "日本酒", "焼酎", "酒"], "food"),
    (["外食", "ファスト", "レストラン", "牛丼", "ラーメン", "カフェ", "ファミレス", "飲食"], "dining"),
    (["日用品", "洗剤", "シャンプー", "ティッシュ", "おむつ", "化粧品", "衛生"], "daily"),
    (["光熱", "電気", "ガス", "水道", "電力"], "utilities"),
    (["交通", "バス", "電車", "タクシー", "鉄道", "運賃"], "transport"),
]

DEFAULT_PRICES = {
    "food": 250,
    "utilities": 6000,
    "dining": 400,
    "daily": 400,
    "transport": 400,
    "other": 300,
}

# これらの文字列を含む行はスキップ
SKIP_PATTERNS = [
    "≫ 詳細はこちら",
    "更新してます",
    "まとめました",
    "一覧にまとめました",
    "から値上がりするもの",
    "から値上げするもの",
    "スポンサーリンク",
    "目次",
    "はじめに",
    "参考文献",
    "値上げまとめサイト",
    "広告",
]


def fetch_html(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8")
    except Exception as e:
        print(f"  [warn] {url}: {e}")
        return None


def detect_category(text: str) -> str | None:
    for keywords, cat in CATEGORY_MAP:
        if any(kw in text for kw in keywords):
            return cat
    return None


def parse_article_items(url: str, increase_date: str, default_category: str) -> list[dict]:
    """記事本文から品目リストを抽出（■会社名 + 製品名行の構造）"""
    html = fetch_html(url)
    if not html:
        return []

    # entry-content ブロックを抽出
    body_match = re.search(
        r'class="entry-content[^"]*"(.*?)(?:class="entry-footer|id="comments")',
        html, re.DOTALL
    )
    if not body_match:
        return []

    body = body_match.group(1)
    text = re.sub(r"<[^>]+>", "\n", body)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    items: list[dict] = []
    current_company: str | None = None
    current_category = default_category

    for line in lines:
        if len(line) < 2:
            continue

        # スキップ行（≫ 詳細はこちら など）
        if any(p in line for p in SKIP_PATTERNS):
            current_company = None
            continue

        # 会社名行（■で始まる）
        if line.startswith("■"):
            current_company = line[1:].strip()
            continue

        # 会社が設定されている → 製品名行
        if current_company:
            items.append({
                "id": str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    current_company + "|" + line + "|" + increase_date
                )),
                "productName": line[:40],
                "company": current_company[:30],
                "category": current_category,
                "increaseRate": 0.08,
                "increaseDate": increase_date,
                "reason": "原材料・物流費高騰",
                "source": "値上げまとめ（neage.hateblo.jp）",
                "referencePrice": DEFAULT_PRICES.get(current_category, 300),
            })
            continue

        # カテゴリヘッダー候補（会社未設定の状態で来たテキスト行）
        cat = detect_category(line)
        if cat:
            current_category = cat

    return items


def scan_archives() -> list[tuple[str, str, str, str]]:
    """過去4ヶ月のアーカイブから「X月から値上げするもの一覧」記事を収集"""
    articles: list[tuple[str, str, str, str]] = []
    seen_urls: set[str] = set()

    y, m = TODAY.year, TODAY.month
    for _ in range(4):
        archive_url = f"https://neage.hateblo.jp/archive/{y:04d}/{m:02d}"
        print(f"  スキャン: {y:04d}/{m:02d}")
        html = fetch_html(archive_url)
        if html:
            # 記事URL＋タイトルリンクを抽出
            pattern = (
                r'href="(https://neage\.hateblo\.jp/entry/\d{4}/\d{2}/\d{2}/[^"]+)"'
                r'[^>]*class="entry-title-link"[^>]*>(.*?)</a>'
            )
            matches = re.findall(pattern, html, re.DOTALL)
            # フォールバック: class順序が違う場合
            if not matches:
                pattern2 = (
                    r'class="entry-title-link"[^>]*'
                    r'href="(https://neage\.hateblo\.jp/entry/\d{4}/\d{2}/\d{2}/[^"]+)"'
                    r'[^>]*>(.*?)</a>'
                )
                matches = re.findall(pattern2, html, re.DOTALL)

            for link, raw_title in matches:
                if link in seen_urls:
                    continue
                title = re.sub(r"<[^>]+>", "", raw_title).strip()
                mo = re.search(r"(\d+)月から値上げ", title)
                if not mo:
                    continue
                target_month = int(mo.group(1))
                # 対象年を推定: 当月より前の月番号なら翌年
                target_year = y if target_month >= m else y + 1
                increase_date = f"{target_year}-{target_month:02d}-01"
                default_cat = detect_category(title) or "food"
                seen_urls.add(link)
                articles.append((link, increase_date, default_cat, title))
                print(f"    発見: 「{title}」→ {increase_date}")

        # 前月へ
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    return articles


def load_existing() -> dict:
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def merge(existing: list[dict], scraped: list[dict]) -> list[dict]:
    """
    過去データ（当月より前）は保持。
    当月以降は scraped で上書きし、scraped にないIDの手動データも保持。
    """
    cutoff = TODAY.strftime("%Y-%m-01")
    scraped_ids = {i["id"] for i in scraped}

    kept = [
        i for i in existing
        if i["increaseDate"] < cutoff
        or (i["increaseDate"] >= cutoff and i["id"] not in scraped_ids)
    ]
    merged = kept + scraped

    seen: set[str] = set()
    result: list[dict] = []
    for item in merged:
        if item["id"] not in seen:
            seen.add(item["id"])
            result.append(item)

    return sorted(result, key=lambda x: x["increaseDate"])


def main():
    print(f"[{TODAY.strftime('%Y-%m-%d %H:%M JST')}] 月次データ更新開始")

    existing = load_existing()
    print(f"  現在のデータ: {len(existing['items'])}件")

    articles = scan_archives()
    print(f"  対象記事数: {len(articles)}件")

    new_items: list[dict] = []
    for url, increase_date, default_cat, title in articles:
        print(f"  取得中: 「{title}」")
        items = parse_article_items(url, increase_date, default_cat)
        print(f"    → {len(items)}品目")
        new_items.extend(items)

    print(f"  スクレイプ合計: {len(new_items)}品目")
    merged = merge(existing["items"], new_items)

    output = {
        "lastUpdated": TODAY.strftime("%Y-%m-%d"),
        "items": merged,
    }
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  完了: 合計{len(merged)}件 → prices.json 更新")


if __name__ == "__main__":
    main()
