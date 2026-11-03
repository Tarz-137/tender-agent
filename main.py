import argparse

from pathlib import Path
from evaluation_cache import evaluator_signature, evaluation_key, reuse_evaluation
from metadata_prefetch import MetadataPrefetch

from graph import build_tender_graph
from metrics_tracker import metrics_tracker
from models.tender import TenderMetadata
from nodes.discover import discover_tenders_node
from portals.base import TenderPortal
from portals.gepnic import GePNICPortal
from portals.portal_config import PORTALS, PortalConfig


OUTPUT_PATH = Path("data/downloads/tenders.jsonl")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover and evaluate tenders from a configured portal."
    )
    parser.add_argument(
        "--portal",
        choices=sorted(PORTALS),
        default="cppp",
        help="Portal to process. Defaults to cppp.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Optional maximum number of tender URLs to process. "
            "When omitted, all discovered tenders are processed."
        ),
    )
    return parser.parse_args()


def create_portal(config: PortalConfig) -> TenderPortal:
    if not config.enabled:
        raise ValueError(f"Portal {config.key!r} is disabled")

    if config.platform == "gepnic":
        return GePNICPortal(config)

    raise ValueError(f"Unsupported platform: {config.platform}")


def load_saved_tenders() -> dict[tuple[str, str], TenderMetadata]:
    """Load completed evaluations, including legacy records."""
    saved = {}
    if not OUTPUT_PATH.exists():
        return saved
    with OUTPUT_PATH.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                tender = TenderMetadata.model_validate_json(line)
            except ValueError as exc:
                print(f"[MAIN WARNING] Invalid record at line {line_number}: {exc}")
                continue
            if tender.tender_id:
                saved[(tender.portal, tender.tender_id)] = tender
    return saved


def load_saved_tender_keys() -> set[tuple[str, str]]:
    return set(load_saved_tenders())

def save_tender(tender: TenderMetadata) -> None:
    """Append one processed tender to the JSONL output."""

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("a", encoding="utf-8") as file:
        file.write(tender.model_dump_json() + "\n")


def main(portal_key: str, limit: int | None = None) -> None:
    if limit is not None and limit < 1:
        raise ValueError("Tender limit must be at least 1")

    try:
        portal_config = PORTALS[portal_key]
    except KeyError as exc:
        raise ValueError(f"Unknown portal key: {portal_key!r}") from exc

    portal = create_portal(portal_config)
    print(f"[MAIN] Discovering tenders from {portal_config.name}")

    discovery_result = discover_tenders_node(
        {
            "portal": portal,
            "discovery_limit": limit,
        }
    )
    if discovery_result.get("error"):
        raise RuntimeError(
            "Tender discovery failed: " + discovery_result["error"]
        )

    tender_urls = discovery_result.get("tender_urls", [])
    if not tender_urls:
        print("[MAIN] No tender URLs discovered")
        return

    discovered_count = len(tender_urls)
    if limit is not None:
        tender_urls = tender_urls[:limit]

    if limit is not None and discovered_count > limit:
        print(
            f"[MAIN] Test limit applied: processing {limit} of "
            f"{discovered_count} discovered tender URLs"
        )

    print(f"[MAIN] Processing {len(tender_urls)} tender URLs")
    tender_graph = build_tender_graph()
    saved_tenders = load_saved_tenders()
    signature = evaluator_signature()
    content_cache = {
        item.evaluation_cache_key: item
        for item in saved_tenders.values()
        if item.evaluation_cache_key == evaluation_key(item, signature)
    }
    print(f"[MAIN] Loaded {len(saved_tenders)} existing tender key(s)")

    processed_count = 0
    relevant_count = 0
    duplicate_count = 0
    failed_count = 0

    with MetadataPrefetch(portal, tender_urls, portal_config.request_delay_seconds) as prefetch:
        for index, (tender_url, metadata) in enumerate(prefetch, start=1):
            print(f"[MAIN] Tender {index}/{len(tender_urls)}")
            try:
                state = {
                    "portal": portal,
                    "current_tender": {"url": tender_url},
                    "error": None,
                }
                if metadata.get("error") or metadata.get("tender") is None:
                    raise ValueError(metadata.get("error") or "Missing metadata")
                tender = metadata["tender"]
                tender_key = (tender.portal, tender.tender_id)
                key = evaluation_key(tender, signature)
                previous = saved_tenders.get(tender_key)
                cached = content_cache.get(key)
                if cached is not None:
                    duplicate_count += 1
                    print(f"[MAIN] Content cache hit; skipping scoring {tender.tender_id}")
                    reuse_evaluation(tender, cached, key)
                else:
                    result = tender_graph.invoke({**state, **metadata})
                    if result.get("error") or result.get("tender") is None:
                        raise ValueError(result.get("error") or "No tender returned")
                    tender = result["tender"]
                    tender.evaluation_cache_key = key
                    content_cache[key] = tender.model_copy(deep=True)
                    processed_count += 1
                # Save each reissued tender's own identity and fresh links, even on a hit.
                # Older records without a fingerprint are reevaluated once.
                if previous is None or previous.evaluation_cache_key != key:
                    save_tender(tender)
                    if tender.tender_id:
                        saved_tenders[tender_key] = tender.model_copy(deep=True)
                if tender.relevant:
                    relevant_count += 1
                    download_result = prefetch.download(state, tender)
                    if download_result.get("error"):
                        raise ValueError(download_result["error"])
            except Exception as exc:
                failed_count += 1
                print(f"[MAIN ERROR] {tender_url}: {exc}")
                continue
            print(
                f"[MAIN] Relevant={tender.relevant} "
                f"score={tender.final_score:.3f} "
                f"title={tender.title}"
            )

    print()
    print("[MAIN] Processing complete")
    print(f"[MAIN] Fresh evaluations: {processed_count}")
    print(f"[MAIN] Relevant tenders: {relevant_count}")
    print(f"[MAIN] Cached evaluations reused: {duplicate_count}")
    print(f"[MAIN] Failed tenders: {failed_count}")
    print(f"[MAIN] Results saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    arguments = parse_args()
    metrics_tracker.reset()

    try:
        main(arguments.portal, arguments.limit)
    finally:
        metrics_tracker.print_summary()
        metrics_tracker.save_json(Path("data/metrics.json"))

