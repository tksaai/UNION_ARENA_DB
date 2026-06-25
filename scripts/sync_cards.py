#!/usr/bin/env python3
"""Synchronize UNION ARENA card data from the official Japanese card list."""

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
from typing import Any, Iterable
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
CARDLIST_URL = f"{BASE_URL}/jp/cardlist/"
SEARCH_URL = f"{BASE_URL}/jp/cardlist/index.php?search=true"
DETAIL_URL = f"{BASE_URL}/jp/cardlist/detail_iframe.php?card_no={{card_no}}"
USER_AGENT = "UnionArenaCardDB/1.0 (+https://github.com/)"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "cards.json"
DEFAULT_IMAGE_DIR = ROOT / "Cards"
THREAD_LOCAL = threading.local()


@dataclass(frozen=True)
class Series:
    series_code: str
    product: str
    product_code: str
    title: str


@dataclass(frozen=True)
class ListingCard:
    variant_id: str
    name: str
    image_url: str


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
        allowed_methods=frozenset(("GET", "POST")),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.5"})
    return session


def get_session() -> requests.Session:
    if not hasattr(THREAD_LOCAL, "session"):
        THREAD_LOCAL.session = create_session()
    return THREAD_LOCAL.session


def request_text(url: str, *, method: str = "GET", data: dict[str, str] | None = None) -> str:
    response = get_session().request(method, url, data=data, timeout=45)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def product_code_from_name(product: str) -> str:
    match = re.search(r"【\s*([^】]+?)\s*】", product)
    return clean_text(match.group(1)) if match else ""


def title_from_product(product: str) -> str:
    return clean_text(re.sub(r"【[^】]+】", "", product))


def parse_series(html: str) -> list[Series]:
    soup = BeautifulSoup(html, "html.parser")
    select = soup.select_one("select#series")
    if not select:
        raise ValueError("Official series selector was not found")
    results: list[Series] = []
    for option in select.select("option[value]"):
        code = clean_text(option.get("value"))
        product = clean_text(option.get_text(" ", strip=True))
        if not code.isdigit():
            continue
        results.append(
            Series(
                series_code=code,
                product=product,
                product_code=product_code_from_name(product),
                title=title_from_product(product),
            )
        )
    return results


def choose_latest(series_list: list[Series]) -> Series:
    numbered_boosters: list[tuple[int, Series]] = []
    for series in series_list:
        match = re.fullmatch(r"UA(\d+)BT", series.product_code, re.IGNORECASE)
        if match:
            numbered_boosters.append((int(match.group(1)), series))
    if numbered_boosters:
        return max(numbered_boosters, key=lambda item: item[0])[1]
    normal = [series for series in series_list if not series.series_code.startswith(("5708", "5709"))]
    if not normal:
        raise ValueError("No normal product series was found")
    return normal[-1]


def resolve_series(requested: str, series_list: list[Series]) -> list[Series]:
    requested = requested.strip()
    if requested.lower() == "all":
        return series_list
    if requested.lower() == "latest":
        return [choose_latest(series_list)]

    lookup: dict[str, Series] = {}
    for series in series_list:
        lookup[series.series_code.upper()] = series
        if series.product_code:
            lookup[series.product_code.upper()] = series

    selected: list[Series] = []
    unknown: list[str] = []
    for token in requested.split(","):
        key = token.strip().upper()
        if not key:
            continue
        if key in lookup:
            selected.append(lookup[key])
        else:
            unknown.append(token.strip())
    if unknown:
        raise ValueError(f"Unknown series: {', '.join(unknown)}")
    return list(dict.fromkeys(selected))


def parse_listing(html: str) -> list[ListingCard]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[ListingCard] = []
    seen: set[str] = set()
    for link in soup.select("li.cardImgCol a.modalCardDataOpen[href]"):
        query = parse_qs(urlparse(link.get("href", "")).query)
        variant_id = unquote(query.get("card_no", [""])[0])
        image = link.select_one("img")
        image_url = ""
        if image:
            image_url = urljoin(BASE_URL, image.get("data-src") or image.get("src") or "")
        alt = clean_text(image.get("alt") if image else "")
        name = alt[len(variant_id):].strip() if variant_id and alt.startswith(variant_id) else alt
        if variant_id and variant_id not in seen:
            seen.add(variant_id)
            results.append(ListingCard(variant_id=variant_id, name=name, image_url=image_url))
    return results


def text_with_icons(element: Tag | None) -> str:
    if not element:
        return ""
    clone = BeautifulSoup(str(element), "html.parser")
    target = clone.find()
    if not target:
        return ""
    for image in target.select("img"):
        image.replace_with(f"【{clean_text(image.get('alt'))}】" if image.get("alt") else "")
    for br in target.select("br"):
        br.replace_with("\n")
    text = target.get_text("", strip=True)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def field(soup: BeautifulSoup, class_name: str) -> Tag | None:
    wrapper = soup.select_one(f".{class_name}")
    return wrapper.select_one(".cardDataContents") if wrapper else None


def parse_need_energy(element: Tag | None) -> tuple[list[str], int | None]:
    if not element:
        return [], None
    labels = [clean_text(image.get("alt")) for image in element.select("img") if image.get("alt")]
    colors: list[str] = []
    energy: int | None = None
    for label in labels:
        match = re.match(r"(.+?)(\d+|-)$", label)
        if match:
            color, value = match.groups()
            colors.append(color)
            energy = 0 if value == "-" else int(value)
    return list(dict.fromkeys(colors)), energy


def parse_generated_energy(element: Tag | None) -> list[dict[str, Any]]:
    if not element:
        return []
    results: list[dict[str, Any]] = []
    for image in element.select("img"):
        color = clean_text(image.get("alt"))
        src = image.get("src", "")
        count_match = re.search(r"(\d+)\.[a-z]+(?:\?.*)?$", src, re.IGNORECASE)
        count = int(count_match.group(1)) if count_match else 1
        if color:
            results.append({"color": color, "count": count})
    return results


def parse_int(value: str) -> int | None:
    value = clean_text(value)
    return int(value) if re.fullmatch(r"\d+", value) else None


def parse_number_or_text(value: str) -> int | str | None:
    value = clean_text(value)
    if not value or value == "-":
        return None
    return int(value) if re.fullmatch(r"\d+", value) else value


def variant_number(variant_id: str) -> int:
    match = re.search(r"_p(\d+)$", variant_id, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def parse_detail(html: str, listing: ListingCard, series: Series) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one(".cardNameCol")
    name = ""
    if heading:
        name = clean_text("".join(str(node) for node in heading.find_all(string=True, recursive=False)))
    furigana = clean_text(soup.select_one(".rubyData").get_text(" ", strip=True) if soup.select_one(".rubyData") else "")
    card_number = clean_text(soup.select_one(".cardNumData").get_text(" ", strip=True) if soup.select_one(".cardNumData") else "")
    rarity = clean_text(soup.select_one(".rareData").get_text(" ", strip=True) if soup.select_one(".rareData") else "")
    title_image = soup.select_one(".cardDataTitleCol img")
    title = clean_text(title_image.get("alt") if title_image else series.title)
    image = soup.select_one(".cardDataImgCol img")
    image_url = urljoin(BASE_URL, image.get("src") if image else listing.image_url)
    colors, need_energy = parse_need_energy(field(soup, "needEnergyData"))
    generated_energy = parse_generated_energy(field(soup, "generatedEnergyData"))
    card_type = text_with_icons(field(soup, "categoryData"))
    features_text = text_with_icons(field(soup, "attributeData"))
    features = [part.strip() for part in re.split(r"[/／]", features_text) if part.strip() and part.strip() != "-"]
    trigger_element = field(soup, "triggerData")
    trigger_image = trigger_element.select_one("img") if trigger_element else None
    trigger_type = clean_text(trigger_image.get("alt")) if trigger_image else ""
    product_element = soup.select_one(".cardDataProductsTxt")
    product = clean_text(product_element.get_text(" ", strip=True) if product_element else series.product)
    source_url = f"{BASE_URL}/jp/cardlist/detail.php?card_no={quote(listing.variant_id, safe='/')}"
    parallel_index = variant_number(listing.variant_id)

    return {
        "variantId": listing.variant_id,
        "cardNumber": card_number or re.sub(r"_p\d+$", "", listing.variant_id, flags=re.IGNORECASE),
        "cardName": name or listing.name,
        "furigana": furigana,
        "rarity": rarity,
        "cardType": card_type,
        "title": title,
        "product": product,
        "productCode": series.product_code,
        "seriesCode": series.series_code,
        "color": colors,
        "needEnergy": need_energy,
        "ap": parse_int(text_with_icons(field(soup, "apData"))),
        "bp": parse_number_or_text(text_with_icons(field(soup, "bpData"))),
        "features": features,
        "generatedEnergy": generated_energy,
        "effectText": text_with_icons(field(soup, "effectData")),
        "trigger": text_with_icons(trigger_element),
        "triggerType": trigger_type,
        "imageUrl": image_url,
        "sourceUrl": source_url,
        "parallel": parallel_index > 0,
        "parallelIndex": parallel_index,
    }


def fetch_detail(listing: ListingCard, series: Series) -> dict[str, Any]:
    url = DETAIL_URL.format(card_no=quote(listing.variant_id, safe=""))
    html = request_text(url)
    return parse_detail(html, listing, series)


def safe_filename(variant_id: str, image_url: str) -> str:
    extension = Path(urlparse(image_url).path).suffix.lower() or ".png"
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", variant_id) + extension


def download_image(card: dict[str, Any], image_root: Path) -> str:
    product_dir = image_root / (card.get("productCode") or card["seriesCode"])
    product_dir.mkdir(parents=True, exist_ok=True)
    destination = product_dir / safe_filename(card["variantId"], card["imageUrl"])
    if not destination.exists():
        response = get_session().get(card["imageUrl"], timeout=60)
        response.raise_for_status()
        destination.write_bytes(response.content)
    return destination.relative_to(ROOT).as_posix()


def group_variants(details: Iterable[dict[str, Any]], *, download_images: bool, image_root: Path) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for detail in details:
        grouped.setdefault((detail["seriesCode"], detail["cardNumber"]), []).append(detail)

    cards: list[dict[str, Any]] = []
    for (series_code, card_number), variants in grouped.items():
        variants.sort(key=lambda item: (item["parallel"], item["parallelIndex"], item["variantId"]))
        base = variants[0]
        variant_data: list[dict[str, Any]] = []
        for variant in variants:
            image_path = download_image(variant, image_root) if download_images else ""
            parallel_index = variant["parallelIndex"]
            if parallel_index:
                label = f"{variant['rarity'] or 'パラレル'} / P{parallel_index}"
            else:
                label = variant["rarity"] or "通常"
            variant_data.append(
                {
                    "id": variant["variantId"],
                    "label": label,
                    "rarity": variant["rarity"],
                    "parallel": variant["parallel"],
                    "imageUrl": variant["imageUrl"],
                    **({"imagePath": image_path} if image_path else {}),
                }
            )

        card = {
            key: value
            for key, value in base.items()
            if key not in {"variantId", "imageUrl", "parallel", "parallelIndex"}
        }
        card["uniqueId"] = f"{series_code}:{card_number}"
        card["variants"] = variant_data
        cards.append(card)
    return sorted(cards, key=lambda card: (card["cardNumber"], card["seriesCode"]))


def fetch_series(series: Series, *, workers: int, limit: int | None, download_images: bool, image_root: Path) -> list[dict[str, Any]]:
    print(f"[{series.series_code}] {series.product}")
    listing_html = request_text(SEARCH_URL, method="POST", data={"series": series.series_code})
    listing = parse_listing(listing_html)
    if limit is not None:
        listing = listing[:limit]
    print(f"  variants: {len(listing)}")
    if not listing:
        raise RuntimeError(f"No cards were returned for series {series.series_code}")
    details: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(fetch_detail, item, series): item for item in listing}
        total = len(future_map)
        for index, future in enumerate(concurrent.futures.as_completed(future_map), start=1):
            item = future_map[future]
            try:
                details.append(future.result())
            except Exception as error:
                print(f"  warning: {item.variant_id}: {error}", file=sys.stderr)
            if index == total or index % 25 == 0:
                print(f"  details: {index}/{total}")
    if len(details) != len(listing):
        raise RuntimeError(
            f"Series {series.series_code} was incomplete: "
            f"{len(details)}/{len(listing)} card variants were parsed"
        )
    return group_variants(details, download_images=download_images, image_root=image_root)


def load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def merge_cards(existing: list[dict[str, Any]], updates: list[dict[str, Any]], selected_codes: set[str], replace_all: bool) -> list[dict[str, Any]]:
    del selected_codes  # Kept in the signature for compatibility with callers and future pruning modes.
    merged = ([] if replace_all else existing) + updates
    return sorted(
        {str(card.get("uniqueId")): card for card in merged if card.get("uniqueId")}.values(),
        key=lambda card: (str(card.get("cardNumber", "")), str(card.get("seriesCode", ""))),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", default="latest", help="latest, all, series ID, product code, or comma-separated values")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--image-dir", type=Path, default=DEFAULT_IMAGE_DIR)
    parser.add_argument("--download-images", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, help="Limit variants per series for parser testing")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    index_html = request_text(CARDLIST_URL)
    available = parse_series(index_html)
    selected = resolve_series(args.series, available)
    print(f"Selected {len(selected)} series")

    updates: list[dict[str, Any]] = []
    for series in selected:
        updates.extend(
            fetch_series(
                series,
                workers=max(1, args.workers),
                limit=args.limit,
                download_images=args.download_images and not args.dry_run,
                image_root=args.image_dir,
            )
        )

    output = args.output.resolve()
    existing = load_existing(output)
    merged = merge_cards(
        existing,
        updates,
        {series.series_code for series in selected},
        replace_all=args.series.lower() == "all",
    )
    if args.dry_run:
        print(f"Dry run: {len(updates)} updated cards, {len(merged)} total cards")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(merged)} cards to {output}")
    print(f"Completed in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
