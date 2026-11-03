import unittest
from threading import Event, get_ident
from unittest.mock import Mock, patch

from evaluation_cache import evaluation_key, INPUT_FIELDS, reuse_evaluation
from metadata_prefetch import MetadataPrefetch
from models.tender import TenderMetadata, TenderDocument


def tender(name="A"):
    return TenderMetadata(portal="Test", tender_id=name, title="Fountain",
                          source_url=name, documents=[])


class ContentKeyTests(unittest.TestCase):
    def test_all_evaluator_inputs_and_version_invalidate(self):
        item = tender()
        base = evaluation_key(item, "v1")
        for field in INPUT_FIELDS:
            changed = item.model_copy(deep=True)
            setattr(changed, field, 100.0 if field == "tender_value" else "changed")
            self.assertNotEqual(base, evaluation_key(changed, "v1"), field)
        self.assertNotEqual(base, evaluation_key(item, "v2"))

    def test_identity_and_session_links_do_not_invalidate(self):
        first, second = tender("A"), tender("B")
        second.documents = [TenderDocument(name="notice.pdf", url="fresh")]
        self.assertEqual(evaluation_key(first, "v1"), evaluation_key(second, "v1"))
        first.relevant = True
        first.matched_keywords = ["fountain"]
        reuse_evaluation(second, first, "key")
        self.assertEqual(second.tender_id, "B")
        self.assertEqual(second.documents[0].url, "fresh")
        second.matched_keywords.append("extra")
        self.assertEqual(first.matched_keywords, ["fountain"])


class PrefetchTests(unittest.TestCase):
    def test_fetch_overlaps_processing_but_is_bounded_and_serial(self):
        second_started = Event()
        release = Event()
        calls = []
        threads = []
        def fetch(url):
            calls.append(url)
            threads.append(get_ident())
            if url == "B":
                second_started.set()
                if not release.wait(3):
                    raise RuntimeError("Test timed out")
            return tender(url)
        portal = Mock()
        portal.extract_metadata.side_effect = fetch
        with MetadataPrefetch(portal, ["A", "B", "C"], delay=0) as queue:
            iterator = iter(queue)
            self.assertEqual(next(iterator)[0], "A")
            try:
                self.assertTrue(second_started.wait(2))
                self.assertEqual(calls, ["A", "B"])
            finally:
                release.set()
            self.assertEqual([url for url, result in iterator], ["B", "C"])
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], get_ident())

    def test_download_refreshes_current_tender_after_next_prefetch(self):
        current = []
        calls = []
        def fetch(url):
            current[:] = [url]
            calls.append(url)
            item = tender(url)
            item.documents = [TenderDocument(name="notice.pdf", url=f"fresh-{url}")]
            return item
        portal = Mock()
        portal.extract_metadata.side_effect = fetch
        def download(state):
            self.assertEqual(current, ["A"])
            self.assertEqual(state["tender"].documents[0].url, "fresh-A")
            return {"tender": state["tender"]}
        with patch("metadata_prefetch.download_node", side_effect=download):
            with MetadataPrefetch(portal, ["A", "B"], delay=0) as queue:
                iterator = iter(queue)
                url, metadata = next(iterator)
                item = metadata["tender"]
                item.relevant = True
                queue.download({"portal": portal, "current_tender": {"url": url}}, item)
                self.assertEqual(calls, ["A", "B", "A"])
                self.assertEqual(next(iterator)[0], "B")

    def test_metadata_failure_does_not_stop_later_fetches(self):
        portal = Mock()
        portal.extract_metadata.side_effect = [ValueError("bad page"), tender("B")]
        with MetadataPrefetch(portal, ["A", "B"], delay=0) as queue:
            results = list(queue)
        self.assertEqual(results[0][1]["error"], "bad page")
        self.assertEqual(results[1][1]["tender"].tender_id, "B")

    def test_changed_identity_blocks_download(self):
        portal = Mock()
        portal.extract_metadata.return_value = tender("wrong")
        item = tender("A")
        item.relevant = True
        with patch("metadata_prefetch.download_node") as download:
            with MetadataPrefetch(portal, [], delay=0) as queue:
                with self.assertRaisesRegex(ValueError, "identity changed"):
                    queue.download({"current_tender": {"url": "A"}}, item)
            download.assert_not_called()
