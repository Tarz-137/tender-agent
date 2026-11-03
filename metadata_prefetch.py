"""One pending metadata fetch; all portal/session I/O stays on one worker."""
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep

from nodes.metadata import metadata_node
from nodes.download import download_node


class MetadataPrefetch:
    def __init__(self, portal, urls, delay=1.0):
        self.portal = portal
        self.urls = urls
        self.delay = delay
        self.last_fetch = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="portal-io")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.executor.shutdown(wait=True, cancel_futures=True)

    def _fetch(self, url):
        if self.last_fetch is not None:
            sleep(max(0, self.delay - (monotonic() - self.last_fetch)))
        state = {"portal": self.portal, "current_tender": {"url": url}, "error": None}
        try:
            return metadata_node(state)
        finally:
            self.last_fetch = monotonic()

    def __iter__(self):
        iterator = iter(self.urls)
        url = next(iterator, None)
        if url is None:
            return
        pending = self.executor.submit(self._fetch, url)
        while True:
            metadata = pending.result()
            next_url = next(iterator, None)
            if next_url is not None:
                pending = self.executor.submit(self._fetch, next_url)
            yield url, metadata
            if next_url is None:
                break
            url = next_url

    def _download(self, state, tender):
        if tender.relevant:
            # Prefetch may have moved the server session to another tender.
            # Re-establish the current document context on the same worker.
            fresh = self._fetch(state["current_tender"]["url"])
            if fresh.get("error") or fresh.get("tender") is None:
                raise ValueError(fresh.get("error") or "Missing refreshed metadata")
            current = fresh["tender"]
            if current.tender_id != tender.tender_id or current.portal != tender.portal:
                raise ValueError("Tender identity changed before document download")
            from evaluation_cache import INPUT_FIELDS
            if any(getattr(current, name) != getattr(tender, name) for name in INPUT_FIELDS):
                raise ValueError("Tender content changed before download; rerun to reevaluate")
            tender.documents = current.documents
        return download_node({**state, "tender": tender})

    def download(self, state, tender):
        return self.executor.submit(self._download, state, tender).result()
