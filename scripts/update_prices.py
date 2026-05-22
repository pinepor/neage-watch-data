#!/usr/bin/env python3
"""
値上がりウォッチ 月次データ更新スクリプト
毎月1日に GitHub Actions から自動実行される

ソース1: neage.hateblo.jp
  月別記事: 「食品｜X月から値上げするもの一覧（2026年版）」
  まとめ記事: 「2026年の値上げまとめ」「2026年1〜9月 値上げまとめ」
  本文:
    カテゴリ名（「食品」「お酒／アルコール」など）
    ■会社名
    製品名1
    製品名2
    ≫ 詳細はこちら
    まとめ記事追加: 月見出し（「1月」「2月」等）→ ■会社名 → 製品名

ソース2: NHK経済 RSS
  URL: https://www3.nhk.or.jp/rss/news/cat5.xml
  非食品の値上げニュースをカテゴリ別に取得
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

NHK_RSS_URL = "https://www3.nhk.or.jp/rss/news/cat5.xml"

CATEGORY_MAP = [
    (["食品", "飲料", "調味料", "缶詰", "冷凍", "乳製品", "菓子", "お米", "パン", "麺"], "food"),
    (["お酒", "アルコール", "ビール", "ワイン", "日本酒", "焼酎", "酒"], "food"),
    (["外食", "ファスト", "レストラン", "牛丼", "ラーメン", "カフェ", "ファミレス", "飲食"], "dining"),
    (["日用品", "洗剤", "シャンプー", "ティッシュ", "おむつ", "化粧品", "衛生",
      "製紙", "紙", "ペーパー", "トイレ", "キッチン", "住設", "住宅設備", "家具"], "daily"),
    (["光熱", "電気", "ガス", "水道", "電力"], "utilities"),
    (["交通", "バス", "電車", "タクシー", "鉄道", "運賃", "郵便", "宅配", "配送", "物流"], "transport"),
]

DEFAULT_PRICES = {
    "food": 250,
    "utilities": 6000,
    "dining": 400,
    "daily": 400,
    "transport": 400,
    "other": 300,
}

# NHK RSS: 除外キーワード（株価・金融・為替ニュース）
NHK_EXCLUDE = [
    "株価", "日経平均", "利上げ", "政策金利", "為替", "円安", "円高",
    "金利", "物価指数", "CPI", "ダウ", "ナスダック", "FOMC",
]

# NHK RSS: 値上げ判定キーワード
NHK_INCLUDE = [
    "値上げ", "値上がり", "引き上げ", "料金改定", "価格改定",
]

# スキップ行パターン
SKIP_PATTERNS = [
    "≫",
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
    "価格改定の詳細",
]

# 品目名として無効な行（単体で出現する場合にスキップ）
INVALID_PRODUCT_PATTERNS = re.compile(
    r"^(など|その他|上記以外|各種|詳細|以上|参照|参考|〃|〜|・)$"
)

# まとめ記事: 月見出しパターン
_MONTH_ONLY = re.compile(r"^(\d{1,2})月$")
_MONTH_IN_LINE = re.compile(r"^(\d{1,2})月(?:から?値上[げがり]|の値上[げがり])")


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


def _extract_body_lines(html: str) -> list[str]:
    """entry-content ブロックをテキスト行に変換"""
    body_match = re.search(
        r'class="entry-content[^"]*"(.*?)(?:class="entry-footer|id="comments"|<footer)',
        html, re.DOTALL
    )
    if not body_match:
        return []
    text = re.sub(r"<[^>]+>", "\n", body_match.group(1))
    lines = []
    for l in text.split("\n"):
        l = l.strip()
        if not l:
            continue
        # HTML残骸・URLは除外
        if l.startswith("<") or l.startswith("http"):
            continue
        lines.append(l)
    return lines


def parse_article_items(url: str, increase_date: str, default_category: str) -> list[dict]:
    """月別記事から品目リストを抽出（■会社名 + 製品名行の構造）"""
    html = fetch_html(url)
    if not html:
        return []

    lines = _extract_body_lines(html)
    items: list[dict] = []
    current_company: str | None = None
    current_category = default_category

    for line in lines:
        if len(line) < 2:
            continue
        if any(p in line for p in SKIP_PATTERNS):
            current_company = None
            continue
        if line.startswith("■"):
            current_company = line[1:].strip()
            continue
        if current_company:
            if not INVALID_PRODUCT_PATTERNS.match(line):
                items.append(_make_item(line, current_company, current_category,
                                        increase_date, "値上げまとめ（neage.hateblo.jp）"))
            continue
        cat = detect_category(line)
        if cat:
            current_category = cat

    return items


def parse_summary_article(url: str, article_year: int) -> list[dict]:
    """まとめ記事（「2026年の値上げまとめ」等）から全月の品目リストを抽出
    neage.hateblo.jp は食品専門のためカテゴリは常に food"""
    html = fetch_html(url)
    if not html:
        return []

    lines = _extract_body_lines(html)
    items: list[dict] = []
    current_company: str | None = None
    current_month: int | None = None

    for line in lines:
        if len(line) < 2:
            continue
        if any(p in line for p in SKIP_PATTERNS):
            current_company = None
            continue

        # 月見出し検出（「1月」「3月から値上げ」等）
        m = _MONTH_ONLY.match(line) or _MONTH_IN_LINE.match(line)
        if m:
            current_month = int(m.group(1))
            current_company = None
            continue

        if line.startswith("■"):
            current_company = line[1:].strip()
            continue

        if current_company and current_month:
            if not INVALID_PRODUCT_PATTERNS.match(line):
                increase_date = f"{article_year}-{current_month:02d}-01"
                items.append(_make_item(line, current_company, "food",
                                        increase_date, "値上げまとめ（neage.hateblo.jp）"))

    return items


def _make_item(product_line: str, company: str, category: str,
               increase_date: str, source: str) -> dict:
    return {
        "id": str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            company + "|" + product_line + "|" + increase_date
        )),
        "productName": product_line[:40],
        "company": company[:30],
        "category": category,
        "increaseRate": 0.08,
        "increaseDate": increase_date,
        "reason": "原材料・物流費高騰",
        "source": source,
        "referencePrice": DEFAULT_PRICES.get(category, 300),
    }


def fetch_nhk_rss() -> list[dict]:
    """NHK経済 RSS から非食品値上げ情報を抽出"""
    print("  NHK RSS 取得中...")
    raw = fetch_html(NHK_RSS_URL)
    if not raw:
        print("  [warn] NHK RSS 取得失敗")
        return []

    # NHK RSS は名前空間付きでET が失敗するため正規表現でパース
    item_blocks = re.findall(r"<item>(.*?)</item>", raw, re.DOTALL)

    items: list[dict] = []
    for block in item_blocks:
        title_m = re.search(r"<title[^>]*>(.*?)</title>", block, re.DOTALL)
        if not title_m:
            continue
        title = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", title_m.group(1)).strip()

        # 除外・包含フィルター
        if any(kw in title for kw in NHK_EXCLUDE):
            continue
        if not any(kw in title for kw in NHK_INCLUDE):
            continue

        # カテゴリ判定（食品は neage.hateblo.jp でカバー済み → 除外）
        cat = detect_category(title)
        if cat == "food":
            continue
        if cat is None:
            cat = "other"

        # 会社名を抽出（タイトル先頭）
        company_m = re.match(r"^(.+?)[　 　](?=\d+月|が|は|も|と|など|の[値料価])", title)
        if company_m:
            company = company_m.group(1).strip()
        else:
            company = re.split(r"[　 　が]", title)[0]
        company = company[:30]
        if len(company) < 2:
            continue

        # 値上げ月を抽出
        month_m = re.search(r"(\d{1,2})月", title)
        if month_m:
            target_month = int(month_m.group(1))
            target_year = TODAY.year
            # 既に過ぎた月番号なら翌年
            if target_month < TODAY.month:
                target_year = TODAY.year + 1
        else:
            # 月不明 → 翌月
            target_month = TODAY.month + 1
            target_year = TODAY.year
            if target_month > 12:
                target_month = 1
                target_year += 1

        increase_date = f"{target_year}-{target_month:02d}-01"

        # 製品名: タイトルから会社名部分を除いた説明
        product = re.sub(r"^.+?[　 　]", "", title, count=1)
        product = product[:40] if product else title[:40]

        items.append({
            "id": str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                company + "|" + title + "|" + increase_date
            )),
            "productName": product,
            "company": company,
            "category": cat,
            "increaseRate": 0.05,
            "increaseDate": increase_date,
            "reason": "原材料・物流費高騰",
            "source": "NHK経済ニュース",
            "referencePrice": DEFAULT_PRICES.get(cat, 300),
        })

    print(f"    → {len(items)}件")
    return items


def scan_archives() -> list[tuple[str, str, str, str, bool]]:
    """過去4ヶ月のアーカイブから値上げ関連記事を収集
    Returns: (url, meta, default_cat, title, is_summary)
      月別記事: meta = increase_date ("YYYY-MM-DD")
      まとめ記事: meta = article_year ("2026")
    """
    articles: list[tuple[str, str, str, str, bool]] = []
    seen_urls: set[str] = set()

    y, m = TODAY.year, TODAY.month
    for _ in range(4):
        archive_url = f"https://neage.hateblo.jp/archive/{y:04d}/{m:02d}"
        print(f"  スキャン: {y:04d}/{m:02d}")
        html = fetch_html(archive_url)
        if html:
            pattern = (
                r'href="(https://neage\.hateblo\.jp/entry/\d{4}/\d{2}/\d{2}/[^"]+)"'
                r'[^>]*class="entry-title-link"[^>]*>(.*?)</a>'
            )
            matches = re.findall(pattern, html, re.DOTALL)
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

                # 月別記事
                mo = re.search(r"(\d+)月から値上げ", title)
                if mo:
                    target_month = int(mo.group(1))
                    target_year = y if target_month >= m else y + 1
                    increase_date = f"{target_year}-{target_month:02d}-01"
                    default_cat = detect_category(title) or "food"
                    seen_urls.add(link)
                    articles.append((link, increase_date, default_cat, title, False))
                    print(f"    月別: 「{title}」→ {increase_date}")
                    continue

                # まとめ記事（「2026年の値上げまとめ」「2026年1〜9月値上げまとめ」等）
                yr_m = re.search(r"(\d{4})年", title)
                if yr_m and re.search(r"値上[げがり]まとめ", title):
                    article_year = yr_m.group(1)
                    seen_urls.add(link)
                    articles.append((link, article_year, "food", title, True))
                    print(f"    まとめ: 「{title}」（{article_year}年）")

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

    # --- neage.hateblo.jp ---
    articles = scan_archives()
    print(f"  対象記事数: {len(articles)}件")

    new_items: list[dict] = []
    for url, meta, default_cat, title, is_summary in articles:
        print(f"  取得中: 「{title}」")
        if is_summary:
            items = parse_summary_article(url, int(meta))
        else:
            items = parse_article_items(url, meta, default_cat)
        print(f"    → {len(items)}品目")
        new_items.extend(items)

    print(f"  neage 合計: {len(new_items)}品目")

    # --- NHK経済 RSS ---
    nhk_items = fetch_nhk_rss()
    new_items.extend(nhk_items)

    print(f"  全ソース合計: {len(new_items)}品目")

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
