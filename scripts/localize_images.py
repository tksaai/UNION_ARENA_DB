#!/usr/bin/env python3
"""Download existing UNION ARENA card images and store them as local WebP files."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from pathlib import Path
from typing import Any

from sync_cards import DEFAULT_IMAGE_DIR, DEFAULT_OUTPUT, DEFAULT_WEBP_QUALITY, ROOT, download_image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_cards(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise TypeError(f"{path} must contain a JSON array")
    return data


def has_local_webp(variant: dict[str, Any]) -> bool:
    image_path = variant.get("imagePath")
    if not image_path or Path(str(image_path)).suffix.lower() != ".webp":
        return False
    return (ROOT / str(image_path)).exists()


def build_tasks(cards: list[dict[str, Any]], limit: int) -> list[tuple[int, int, dict[str, Any]]]:
    tasks: list[tuple[int, int, dict[str, Any]]] = []
    for card_index, card in enumerate(cards):
        variants = card.get("variants") or []
        for variant_index, variant in enumerate(variants):
            if not isinstance(variant, dict) or has_local_webp(variant):
                continue
            image_url = variant.get("imageUrl")
            variant_id = variant.get("id")
            if not image_url or not variant_id:
                continue
            tasks.append(
                (
                    card_index,
                    variant_index,
                    {
                        "variantId": variant_id,
                        "imageUrl": image_url,
                        "productCode": card.get("productCode"),
                        "seriesCode": card.get("seriesCode") or "unknown",
                    },
                )
            )
            if limit and len(tasks) >= limit:
                return tasks
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--image-dir", type=Path, default=DEFAULT_IMAGE_DIR)
    parser.add_argument("--webp-quality", type=int, default=DEFAULT_WEBP_QUALITY)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    started = time.monotonic()
    cards_path = args.cards.resolve()
    image_dir = args.image_dir.resolve()
    cards = load_cards(cards_path)
    tasks = build_tasks(cards, args.limit)
    print(f"Cards: {len(cards):,}")
    print(f"Images to localize: {len(tasks):,}")
    if args.dry_run or not tasks:
        print(f"Completed in {time.monotonic() - started:.1f}s")
        return 0

    completed = 0
    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(download_image, image_card, image_dir, webp_quality=args.webp_quality): (card_index, variant_index)
            for card_index, variant_index, image_card in tasks
        }
        total = len(future_map)
        for future in concurrent.futures.as_completed(future_map):
            card_index, variant_index = future_map[future]
            try:
                cards[card_index]["variants"][variant_index]["imagePath"] = future.result()
            except Exception as error:
                failed += 1
                variant = cards[card_index]["variants"][variant_index]
                print(f"warning: {variant.get('id')}: {error}", file=sys.stderr)
            completed += 1
            if completed == total or completed % 100 == 0:
                print(f"  images: {completed}/{total} (failed: {failed})")

    cards_path.write_text(json.dumps(cards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {cards_path}")
    print(f"Completed in {time.monotonic() - started:.1f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
