import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock, patch

import main as app
import graph
import nodes.download as downloads
from models.tender import TenderDocument, TenderMetadata
from evaluation_cache import evaluator_signature, evaluation_key

PDF = b"%PDF-1.4\nfixture\n%%EOF\n"


def tender(portal="New Delhi", relevant=False):
    item = TenderMetadata(
        portal=portal, tender_id="2026_TEST_1", title="Fountain maintenance",
        source_url="https://example.test/tender",
        documents=[TenderDocument(name="notice.pdf", url="https://example.test/fresh")],
        relevant=relevant, llm_score=0.9 if relevant else 0,
    )
    item.evaluation_cache_key = evaluation_key(item, evaluator_signature())
    return item


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        p = patch.dict(app.PORTALS, {"new_delhi": replace(app.PORTALS["new_delhi"], request_delay_seconds=0)})
        p.start()
        self.addCleanup(p.stop)
        for target, value in (
            ("main.OUTPUT_PATH", self.root / "tenders.jsonl"),
            ("nodes.download.DOWNLOAD_DIR", self.root),
        ):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        self.portal = Mock()
        self.portal.extract_metadata.side_effect = lambda url: tender()
        self.pipeline = Mock()
        def evaluate(state):
            result = state["tender"]
            result.relevant = True
            result.llm_score = 0.9
            return {"tender": result}
        self.pipeline.invoke.side_effect = evaluate
        for target, kwargs in (
            ("main.create_portal", {"return_value": self.portal}),
            ("main.build_tender_graph", {"return_value": self.pipeline}),
            ("main.discover_tenders_node", {"return_value": {"tender_urls": ["https://example.test/current"]}}),
        ):
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def run_app(self):
        with contextlib.redirect_stdout(io.StringIO()):
            app.main("new_delhi")

    def status(self):
        return json.loads((self.root / "2026_TEST_1" / "download_status.json").read_text())

    def test_failed_download_then_resume_without_rescoring(self):
        self.portal.download_document.side_effect = lambda url, path: path.write_bytes(b"<head>CAPTCHA</head>")
        self.run_app()
        self.assertEqual(len(app.load_saved_tenders()), 1)
        self.assertEqual(self.status()["status"], "failed")
        self.assertFalse((self.root / "2026_TEST_1" / "notice.pdf").exists())
        self.portal.download_document.side_effect = lambda url, path: path.write_bytes(PDF)
        self.run_app()
        self.run_app()
        self.assertEqual(self.pipeline.invoke.call_count, 1)
        self.assertEqual(self.portal.download_document.call_count, 2)
        self.assertEqual(self.status()["status"], "complete")
        self.assertEqual(len(app.OUTPUT_PATH.read_text().splitlines()), 1)
        self.assertEqual(self.portal.download_document.call_args.args[0], "https://example.test/fresh")
        self.assertEqual(list(self.root.rglob("*.part")), [])

    def test_cached_irrelevant_skips_evaluation_and_download(self):
        app.save_tender(tender())
        self.run_app()
        self.pipeline.invoke.assert_not_called()
        self.portal.download_document.assert_not_called()

    def test_legacy_html_pdf_is_retried(self):
        old = tender(relevant=True)
        old.documents[0].url = "https://example.test/expired"
        app.save_tender(old)
        folder = self.root / old.tender_id
        folder.mkdir()
        (folder / "notice.pdf").write_text("<html>captcha</html>")
        self.portal.download_document.side_effect = lambda url, path: path.write_bytes(PDF)
        self.run_app()
        self.pipeline.invoke.assert_not_called()
        self.assertEqual(self.portal.download_document.call_args.args[0], "https://example.test/fresh")
        self.assertTrue(downloads.valid_document(folder / "notice.pdf", "notice.pdf"))

    def test_identical_content_reuses_scores_but_keeps_portal_identity(self):
        app.save_tender(tender(portal="Odisha"))
        self.portal.download_document.side_effect = lambda url, path: path.write_bytes(PDF)
        self.run_app()
        self.pipeline.invoke.assert_not_called()
        self.assertEqual(len(app.load_saved_tenders()), 2)

    def test_changed_description_reevaluates_same_id(self):
        app.save_tender(tender())
        def changed(url):
            item = tender()
            item.description = "New musical fountain scope"
            return item
        self.portal.extract_metadata.side_effect = changed
        self.portal.download_document.side_effect = lambda url, path: path.write_bytes(PDF)
        self.run_app()
        self.pipeline.invoke.assert_called_once()

    def test_legacy_unversioned_record_is_reevaluated(self):
        item = tender()
        item.evaluation_cache_key = None
        app.save_tender(item)
        self.portal.download_document.side_effect = lambda url, path: path.write_bytes(PDF)
        self.run_app()
        self.pipeline.invoke.assert_called_once()

    def test_reissued_tender_reuses_persisted_content(self):
        app.save_tender(tender())
        def reissued(url):
            item = tender()
            item.tender_id = "2026_TEST_2"
            return item
        self.portal.extract_metadata.side_effect = reissued
        self.run_app()
        self.pipeline.invoke.assert_not_called()
        self.assertEqual(len(app.load_saved_tenders()), 2)

    def test_identical_tenders_in_one_run_only_evaluate_once(self):
        def item_for_url(url):
            item = tender()
            item.tender_id = url.rsplit("/", 1)[-1]
            return item
        self.portal.extract_metadata.side_effect = item_for_url
        def evaluate(state):
            return {"tender": state["tender"]}
        self.pipeline.invoke.side_effect = evaluate
        with patch("main.discover_tenders_node", return_value={
            "tender_urls": ["https://example.test/A", "https://example.test/B"]
        }):
            self.run_app()
        self.pipeline.invoke.assert_called_once()
        self.assertEqual(len(app.load_saved_tenders()), 2)

    def test_evaluator_change_invalidates_saved_result(self):
        app.save_tender(tender())
        self.pipeline.invoke.side_effect = lambda state: {"tender": state["tender"]}
        with patch("main.evaluator_signature", return_value="new-model-version"):
            self.run_app()
        self.pipeline.invoke.assert_called_once()

    def test_scoring_failure_is_not_cached(self):
        self.pipeline.invoke.side_effect = RuntimeError("LLM unavailable")
        self.run_app()
        self.assertFalse(app.OUTPUT_PATH.exists())
        self.portal.download_document.assert_not_called()

    def test_empty_documents_not_marked_complete(self):
        item = tender(relevant=True)
        item.documents = []
        downloads.download_documents(item, self.portal)
        self.assertEqual(self.status()["status"], "no_documents")

    def test_pdf_truncation_and_html_rejected(self):
        path = self.root / "test.pdf"
        for content in (b"", b"<html>error</html>", b"%PDF-1.4\npartial"):
            path.write_bytes(content)
            self.assertFalse(downloads.valid_document(path, "test.pdf"))

    def test_graph_does_not_refetch_metadata_or_download(self):
        item = tender()
        def passthrough(state):
            return {"tender": state["tender"]}
        with patch.multiple(graph, keyword_node=passthrough, embedding_node=passthrough,
                            llm_node=passthrough, scoring_node=passthrough), \
             patch.object(graph, "needs_llm", return_value=False):
            result = graph.build_tender_graph().invoke({"tender": item, "portal": self.portal})
        self.assertEqual(result["tender"].tender_id, item.tender_id)
        self.portal.extract_metadata.assert_not_called()
        self.portal.download_document.assert_not_called()


if __name__ == "__main__":
    unittest.main()
