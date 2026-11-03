import unittest
import tempfile
import json
from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from portals.gepnic import GePNICPortal
from portals.portal_config import PORTALS


def soup(html):
    return BeautifulSoup(html, "lxml")


def listing(ids, *, closing=False, next_page=False, detail_page=None, serial_suffix=""):
    page = "FrontEndListTendersbyDate" if closing else "FrontEndTendersByOrganisation"
    page = detail_page or page
    closing_label = "Bid Submission Closing Date" if closing else "Closing Date"
    opening_label = "Tender Opening Date" if closing else "Opening Date"
    rows = "".join(
        f'<tr><td>{i}{serial_suffix}</td><td>date</td><td>date</td><td>date</td>'
        f'<td><a href="?page={page}&amp;component=%24DirectLink&amp;sp={tid}">'
        f'Fountain</a> [{tid}]</td><td>Organisation</td></tr>'
        for i, tid in enumerate(ids, 1)
    )
    pagination = (
        '<a href="?component=TablePages.linkFwd&amp;page=listing">Next</a>'
        if next_page else ""
    )
    return soup(
        '<input name="captchaText"><table><tr><th>S.No</th>'
        f'<th>e-Published Date</th><th>{closing_label}</th><th>{opening_label}</th>'
        '<th>Title and Ref.No./Tender ID</th><th>Organisation Chain</th></tr>'
        f'{rows}</table>{pagination}'
    )


def organisation_index():
    return soup(
        '<input name="captchaText"><table><tr><th>S.No</th>'
        '<th>Organisation Name</th><th>Tender Count</th></tr>'
        '<tr><td>1</td><td>First</td><td><a href="?page=FrontEndTendersByOrganisation'
        '&amp;component=%24DirectLink&amp;sp=first">2</a></td></tr>'
        '<tr><td>2</td><td>Second</td><td><a href="?page=FrontEndTendersByOrganisation'
        '&amp;component=%24DirectLink&amp;sp=second">2</a></td></tr></table>'
    )


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.portal = GePNICPortal(replace(
            PORTALS["cppp_epublish"], request_delay_seconds=0,
        ))
        self.portal.write_discovery_report = Mock()
        self.ids = [f"2026_TEST_{i}_1" for i in range(1, 5)]

    def test_both_listing_formats_keep_all_rows_despite_captcha_form(self):
        for closing in (False, True):
            with self.subTest(closing=closing):
                records = self.portal.collect_tender_records(
                    listing(self.ids, closing=closing)
                )
                self.assertEqual(list(records), self.ids)

    def test_pagination_processes_whole_page_before_following_next(self):
        self.portal.get_soup = Mock(return_value=listing(self.ids[1:]))
        records = self.portal.collect_tender_records(
            listing(self.ids[:2], next_page=True)
        )
        self.assertEqual(list(records), self.ids)
        self.portal.get_soup.assert_called_once()

    def test_dotted_serial_numbers_in_both_listing_formats(self):
        for closing in (False, True):
            with self.subTest(closing=closing):
                records = self.portal.collect_tender_records(
                    listing(self.ids, closing=closing, serial_suffix=".")
                )
                self.assertEqual(list(records), self.ids)
                self.assertEqual(self.portal.discovery_issues, [])

    def test_kerala_closing_discovery_continues_past_ten(self):
        portal = GePNICPortal(replace(PORTALS["kerala"], request_delay_seconds=0))
        portal.write_discovery_report = Mock()
        ids = [f"2026_PWD_{i}_1" for i in range(1, 21)]
        first = listing(ids[:10], closing=True, next_page=True, serial_suffix=".")
        second = listing(ids[10:], closing=True, serial_suffix=".")
        portal.discover_latest_tenders = Mock(return_value=["homepage"])
        portal.select_closing_window = Mock(side_effect=lambda page: page)
        portal.get_soup = Mock(side_effect=[first, second])

        urls = portal.discover()

        self.assertEqual(len(urls), 21)
        self.assertTrue(all(any(tid in url for url in urls) for tid in ids))
        self.assertEqual(portal.get_soup.call_count, 2)
        self.assertEqual(portal.discovery_issues, [])
        self.assertEqual(portal.write_discovery_report.call_args.args[1], "complete")

    def test_organisation_index_accepts_dotted_serial_numbers(self):
        index = organisation_index()
        for row in index.find_all("tr")[1:]:
            cell = row.find("td")
            cell.string = cell.get_text(strip=True) + "."
        self.portal.get_soup = Mock(side_effect=[
            index, listing(self.ids[:2]), listing(self.ids[2:]),
        ])
        self.assertEqual(len(self.portal.discover()), 4)
        self.assertEqual(self.portal.discovery_issues, [])

    def test_epublishing_view_tender_links(self):
        records = self.portal.collect_tender_records(
            listing(self.ids, detail_page="FrontEndViewTender")
        )
        self.assertEqual(list(records), self.ids)

    def test_organisation_code_with_space_is_preserved(self):
        ids = ["2026_DD M_850001_1", "2026_NMDC_852218_1", "2026_IIT-M_852494_1"]
        for closing in (False, True):
            with self.subTest(closing=closing):
                page = listing(ids, closing=closing)
                records = self.portal.collect_tender_records(page)
                self.assertEqual(list(records), ids)

    def test_tender_id_match_does_not_cross_reference_brackets(self):
        text = "[2026_reference][other_123_1] [2026_IIT-M_852494_1]"
        self.assertEqual(
            self.portal.TENDER_ID_PATTERN.findall(text),
            ["2026_IIT-M_852494_1"],
        )

    def test_bracketed_reference_does_not_hide_spaced_tender_id(self):
        page = listing(["2026_DD M_850001_1"])
        cell = page.find_all("tr")[1].find_all("td")[4]
        cell.insert(0, "[Repair of chairs for Editing section (40) nos.] "
                       "[GEM 2026 B 7870763 DT 07 09 2026]")
        self.assertEqual(
            list(self.portal.collect_tender_records(page)),
            ["2026_DD M_850001_1"],
        )

    def test_repeated_page_is_rejected(self):
        page = listing(self.ids, next_page=True)
        self.portal.get_soup = Mock(return_value=page)
        self.assertEqual(list(self.portal.collect_tender_records(page)), self.ids)
        self.assertIn("no new tenders", self.portal.discovery_issues[0]["error"])

    def test_empty_result_table_returns_without_looping(self):
        self.assertEqual(self.portal.collect_tender_records(listing([])), {})

    def test_captcha_without_results_is_reported(self):
        self.assertEqual(self.portal.collect_tender_records(soup('<input name="captchaText">')), {})
        self.assertIn("CAPTCHA", self.portal.discovery_issues[0]["error"])

    def test_strategy_merges_and_deduplicates_organisations(self):
        self.portal.get_soup = Mock(side_effect=[
            organisation_index(), listing(self.ids[:2]), listing(self.ids[1:3]),
        ])
        urls = self.portal.discover()
        self.assertEqual(len(urls), 3)
        self.assertTrue(all(any(tid in url for url in urls) for tid in self.ids[:3]))

    def test_limit_stops_before_fetching_another_organisation(self):
        self.portal.get_soup = Mock(side_effect=[organisation_index(), listing(self.ids[:2])])
        self.assertEqual(len(self.portal.discover(limit=1)), 1)
        self.assertEqual(self.portal.get_soup.call_count, 2)

    def test_count_mismatch_on_first_organisation_is_not_ignored(self):
        self.portal.get_soup = Mock(side_effect=[
            organisation_index(), listing(self.ids[:1]), listing(self.ids[2:]),
        ])
        self.assertEqual(len(self.portal.discover()), 3)
        self.assertEqual(self.portal.discovery_issues[0]["context"], "First")
        self.assertIn("Expected 2", self.portal.discovery_issues[0]["error"])
        self.assertEqual(self.portal.write_discovery_report.call_args.args[1], "partial")

    def test_existing_strategy_still_routes_to_latest_and_closing(self):
        portal = GePNICPortal(PORTALS["new_delhi"])
        portal.write_discovery_report = Mock()
        portal.discover_latest_tenders = Mock(return_value=["latest"])
        portal.discover_closing_tenders = Mock(return_value=["closing"])
        self.assertEqual(portal.discover(), ["latest", "closing"])

    def test_failed_organisation_does_not_block_later_ones(self):
        self.portal.get_soup = Mock(side_effect=[
            organisation_index(), RuntimeError("network unavailable"), listing(self.ids[:2]),
        ])
        self.assertEqual(len(self.portal.discover()), 2)
        self.assertIn("First", self.portal.discovery_issues[0]["context"])
        self.assertIn("network unavailable", self.portal.discovery_issues[0]["error"])

    def test_bad_rows_do_not_hide_good_rows(self):
        page = listing(self.ids)
        rows = page.find_all("tr")
        rows[1].find_all("td")[4].string = "Unrecognised tender ID"
        rows[2].find("a")["href"] = "?page=unexpected"
        self.assertEqual(list(self.portal.collect_tender_records(page)), self.ids[2:])
        self.assertEqual(len(self.portal.discovery_issues), 2)

    def test_later_page_failure_preserves_collected_records(self):
        self.portal.get_soup = Mock(side_effect=RuntimeError("connection failed"))
        records = self.portal.collect_tender_records(listing(self.ids, next_page=True))
        self.assertEqual(list(records), self.ids)
        self.assertIn("connection failed", self.portal.discovery_issues[0]["error"])

    def test_report_persists_partial_results_and_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.portal.discovery_issues = [{"context": "First", "error": "CAPTCHA"}]
            with patch("portals.gepnic.Path", return_value=root):
                GePNICPortal.write_discovery_report(self.portal, ["https://example.test/tender"], "partial")
            report = json.loads((root / "cppp_epublish.json").read_text())
            self.assertEqual(report["status"], "partial")
            self.assertEqual(report["tender_count"], 1)
            self.assertEqual(report["issues"], self.portal.discovery_issues)


if __name__ == "__main__":
    unittest.main()
