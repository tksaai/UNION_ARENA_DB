#!/usr/bin/env python3
"""Synchronize card-specific UNION ARENA Q&A from the official Japanese FAQ."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


BASE_URL = "https://www.unionarena-tcg.com"
FAQ_INDEX_URL = f"{BASE_URL}/jp/faq/"
FAQ_LIST_URL = f"{BASE_URL}/jp/faq/list.php?series={{series}}"
USER_AGENT = "UnionArenaCardDB/1.0 (+https://github.com/)"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CARDS = ROOT / "cards.json"
THREAD_LOCAL = threading.local()


@dataclass(frozen=True)
class FaqEntry:
    product_code: str
    card_number: str
    qid: str
    updated_at: str
    question: str
    answer: str
    source_url: str


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET",)),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.5"})
    return session


def get_session() -> requests.Session:
    if not hasattr(THREAD_LOCAL, "session"):
        THREAD_LOCAL.session = create_session()
    return THREAD_LOCAL.session


def request_text(url: str) -> str:
    response = get_session().get(url, timeout=45)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def text_with_breaks(element: Tag | None) -> str:
    if not element:
        return ""
    clone = BeautifulSoup(str(element), "html.parser")
    target = clone.find()
    if not target:
        return ""
    for br in target.select("br"):
        br.replace_with("\n")
    text = target.get_text("\n", strip=True)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def qid_number(qid: str) -> int:
    match = re.search(r"\d+", qid or "")
    return int(match.group()) if match else 0


def discover_faq_product_codes() -> list[str]:
    html = request_text(FAQ_INDEX_URL)
    soup = BeautifulSoup(html, "html.parser")
    codes: list[str] = []
    seen: set[str] = set()
    for link in soup.select('a[href*="list.php?series="]'):
        query = parse_qs(urlparse(urljoin(FAQ_INDEX_URL, link.get("href", ""))).query)
        code = clean_text(unquote(query.get("series", [""])[0]))
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def parse_faq_entries(html: str, product_code: str) -> list[FaqEntry]:
    soup = BeautifulSoup(html, "html.parser")
    source_url = FAQ_LIST_URL.format(series=quote(product_code, safe=""))
    entries: list[FaqEntry] = []

    for unit in soup.select("section.faqUnit"):
        heading = unit.select_one("h2.tit")
        heading_text = clean_text(heading.get_text(" ", strip=True) if heading else "")
        qid_match = re.search(r"Q\d+", heading_text)
        qid = qid_match.group(0) if qid_match else ""
        updated = clean_text(heading.select_one("span").get_text(" ", strip=True) if heading and heading.select_one("span") else "")
        updated = updated.replace("更新", "").strip()

        card_number = clean_text(unit.select_one(".cardID").get_text(" ", strip=True) if unit.select_one(".cardID") else "")
        question = text_with_breaks(unit.select_one(".question p"))
        answer = text_with_breaks(unit.select_one(".answer"))

        if not card_number or not question or not answer:
            continue

        entries.append(
            FaqEntry(
                product_code=product_code,
                card_number=card_number,
                qid=qid,
                updated_at=updated,
                question=question,
                answer=answer,
                source_url=source_url,
            )
        )

    return sorted(entries, key=lambda entry: (entry.card_number, qid_number(entry.qid), entry.question))


def fetch_product_faq(product_code: str) -> list[FaqEntry]:
    url = FAQ_LIST_URL.format(series=quote(product_code, safe=""))
    html = request_text(url)
    entries = parse_faq_entries(html, product_code)
    print(f"[{product_code}] {len(entries)} Q&A")
    return entries


def load_cards(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("cards.json must contain a list")
    return data


def resolve_product_codes(requested: str, cards: list[dict[str, Any]], official_codes: list[str]) -> list[str]:
    card_codes = {clean_text(card.get("productCode")) for card in cards if clean_text(card.get("productCode"))}
    official_lookup = {code.upper(): code for code in official_codes}

    requested = requested.strip()
    if requested.lower() in {"all", "matching"}:
        return [code for code in official_codes if code in card_codes]

    selected: list[str] = []
    unknown: list[str] = []
    for token in requested.split(","):
        key = token.strip()
        if not key:
            continue
        code = official_lookup.get(key.upper(), key)
        if code not in official_codes:
            unknown.append(key)
            continue
        selected.append(code)

    if unknown:
        raise ValueError(f"Unknown FAQ product code: {', '.join(unknown)}")
    return list(dict.fromkeys(selected))


def group_entries(entries: list[FaqEntry]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for entry in sorted(entries, key=lambda item: (item.card_number, qid_number(item.qid), item.question)):
        key = (entry.card_number, entry.qid, entry.question)
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(entry.card_number, []).append(
            {
                "id": entry.qid,
                "updatedAt": entry.updated_at,
                "question": entry.question,
                "answer": entry.answer,
                "sourceUrl": entry.source_url,
            }
        )
    return grouped


def apply_entries(cards: list[dict[str, Any]], entries: list[FaqEntry], selected_codes: set[str]) -> tuple[int, int]:
    grouped = group_entries(entries)
    cards_with_qa = 0
    total_qa = 0

    for card in cards:
        product_code = clean_text(card.get("productCode"))
        if product_code not in selected_codes:
            if isinstance(card.get("qa"), list):
                cards_with_qa += 1
                total_qa += len(card["qa"])
            continue

        qa = grouped.get(clean_text(card.get("cardNumber")), [])
        if qa:
            card["qa"] = qa
            cards_with_qa += 1
            total_qa += len(qa)
        else:
            card.pop("qa", None)

    return cards_with_qa, total_qa


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", type=Path, default=DEFAULT_CARDS)
    parser.add_argument("--series", default="matching", help="matching, all, product code, or comma-separated product codes")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    cards_path = args.cards.resolve()
    cards = load_cards(cards_path)
    official_codes = discover_faq_product_codes()
    selected_codes = resolve_product_codes(args.series, cards, official_codes)
    print(f"Selected {len(selected_codes)} FAQ products")

    entries: list[FaqEntry] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {executor.submit(fetch_product_faq, code): code for code in selected_codes}
        for future in concurrent.futures.as_completed(future_map):
            code = future_map[future]
            try:
                entries.extend(future.result())
            except Exception as error:
                print(f"warning: {code}: {error}", file=sys.stderr)

    cards_with_qa, total_qa = apply_entries(cards, entries, set(selected_codes))
    if args.dry_run:
        print(f"Dry run: {cards_with_qa} cards with {total_qa} Q&A")
    else:
        cards_path.write_text(json.dumps(cards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {cards_with_qa} cards with {total_qa} Q&A to {cards_path}")
    print(f"Completed in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
