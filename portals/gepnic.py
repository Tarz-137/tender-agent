import re
import json
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from models.tender import TenderDocument, TenderMetadata
from portals.base import TenderPortal
from portals.portal_config import PortalConfig


class GePNICPortal(TenderPortal):
    MAX_REQUEST_ATTEMPTS = 5
    CLOSING_DATE_PAGE = "?page=FrontEndListTendersbyDate&service=page"
    CLOSING_WINDOW_ACTIONS = {
        7: "LinkSubmit_0",
        14: "LinkSubmit_1",
    }
    TENDER_ID_PATTERN = re.compile(
        # Organisation codes are portal data: DD M and IIT-M are both valid.
        # Keep the year/number/item structure and never cross bracketed fields.
        r"\b20\d{2}_[^_\[\]\r\n]+_\d+_\d+\b"
    )

    def __init__(self, config: PortalConfig) -> None:
        if config.platform != "gepnic":
            raise ValueError(
                "GePNICPortal cannot handle "
                f"platform {config.platform!r}"
            )
        if config.closing_window_days not in {0, 7, 14}:
            raise ValueError(
                "closing_window_days must be 0, 7, or 14"
            )

        self.config = config
        self.discovery_issues: list[dict[str, str]] = []
        self.discovery_context = ""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/140 Safari/537.36"
                )
            }
        )

    def request_soup(
        self,
        method: str,
        url: str,
        *,
        form_data: list[tuple[str, str]] | None = None,
    ) -> BeautifulSoup:
        """Request a page, retrying transient portal disconnects."""

        for attempt in range(1, self.MAX_REQUEST_ATTEMPTS + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    data=form_data,
                    timeout=(15, 60),
                )
                response.raise_for_status()
                return BeautifulSoup(response.text, "lxml")
            except requests.RequestException as exc:
                if attempt == self.MAX_REQUEST_ATTEMPTS:
                    raise

                wait_seconds = 2 ** (attempt - 1)
                print(
                    "[DISCOVER WARNING] Portal request failed; "
                    f"retrying in {wait_seconds}s "
                    f"({attempt}/{self.MAX_REQUEST_ATTEMPTS}): {exc}"
                )
                sleep(wait_seconds)

        raise RuntimeError("Portal request retry loop ended unexpectedly")

    def get_soup(self, url: str) -> BeautifulSoup:
        return self.request_soup("GET", url)

    def post_soup(
        self,
        url: str,
        form_data: list[tuple[str, str]],
    ) -> BeautifulSoup:
        return self.request_soup("POST", url, form_data=form_data)

    def record_discovery_issue(self, context: str, error: object) -> None:
        context = f"{self.discovery_context}: {context}" if self.discovery_context else context
        self.discovery_issues.append({"context": context, "error": str(error)})
        print(f"[DISCOVER WARNING] {context}: {error}")

    def write_discovery_report(self, urls: list[str], status: str) -> None:
        path = Path("data/discovery") / f"{self.config.key}.json"
        report = {
            "portal": self.config.name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "tender_count": len(urls),
            "tender_urls": urls,
            "issues": self.discovery_issues,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"[DISCOVER] {status}: {len(urls)} URLs; "
                  f"{len(self.discovery_issues)} issue(s). Report: {path}")
        except OSError as exc:
            print(f"[DISCOVER WARNING] Could not save report: {exc}")

    def discover(self, limit: int | None = None) -> list[str]:
        self.discovery_issues = []
        self.discovery_context = ""
        urls: list[str] = []
        status = "failed"
        try:
            urls = self._discover(limit)
            status = "partial" if self.discovery_issues else "limited" if limit else "complete"
            return urls
        except Exception as exc:
            self.record_discovery_issue("Discovery", exc)
            raise
        finally:
            self.write_discovery_report(urls, status)

    def _discover(self, limit: int | None = None) -> list[str]:
        """Combine new homepage tenders with the closing-date safety net."""

        if limit is not None and limit < 1:
            raise ValueError("Discovery limit must be at least 1")

        if self.config.discovery_strategy =="organisation":
            return self.discover_by_organisation(limit=limit)
        if self.config.discovery_strategy != "latest_and_closing":
            raise ValueError(
                f"Unknown discovery strategy: {self.config.discovery_strategy}"
            )
        latest_urls = []
        closing_urls = []
        try:
            latest_urls = self.discover_latest_tenders()
        except Exception as exc:
            self.record_discovery_issue("Latest tenders", exc)
        try:
            closing_urls = self.discover_closing_tenders(max_results=limit)
        except Exception as exc:
            self.record_discovery_issue("Closing tenders", exc)

        combined_urls: list[str] = []
        seen_urls: set[str] = set()
        for tender_url in latest_urls + closing_urls:
            if tender_url in seen_urls:
                continue
            seen_urls.add(tender_url)
            combined_urls.append(tender_url)
            if limit is not None and len(combined_urls) >= limit:
                break

        print(
            f"[DISCOVER] {self.config.name}: "
            f"{len(latest_urls)} latest, "
            f"{len(closing_urls)} closing, "
            f"{len(combined_urls)} combined URLs"
        )
        return combined_urls

    def discover_latest_tenders(self) -> list[str]:
        """Parse only the homepage's Latest Tenders table."""

        soup = self.get_soup(self.config.base_url)
        table = self.find_latest_tenders_table(soup)

        tender_urls: list[str] = []
        seen_urls: set[str] = set()

        for row in table.find_all("tr"):
            detail_link = None
            for link in row.find_all("a", href=True):
                href = str(link["href"])
                if (
                    "DirectLink" in href
                    and "CorrViewsDetails" not in href
                    and "WebRightMenu" not in href
                    and "Security_Audit" not in href
                ):
                    detail_link = link
                    break

            if detail_link is None:
                continue

            tender_url = urljoin(
                self.config.base_url,
                str(detail_link["href"]),
            )
            if tender_url not in seen_urls:
                seen_urls.add(tender_url)
                tender_urls.append(tender_url)

        print(
            f"[DISCOVER] {self.config.name}: "
            f"{len(tender_urls)} latest tender(s)"
        )
        return tender_urls

    def discover_closing_tenders(
        self,
        max_results: int | None = None,
    ) -> list[str]:
        """Discover active tenders closing inside the configured window."""

        closing_page_url = self.config.base_url + self.CLOSING_DATE_PAGE
        soup = self.get_soup(closing_page_url)

        if self.config.closing_window_days:
            soup = self.select_closing_window(soup)

        tender_urls = self.collect_tender_urls(
            soup,
            max_results=max_results,
        )
        print(
            f"[DISCOVER] {self.config.name}: "
            f"{len(tender_urls)} tender(s) closing within "
            f"{self.config.closing_window_days} day(s)"
        )
        return tender_urls

    def select_closing_window(self, soup: BeautifulSoup) -> BeautifulSoup:
        """Submit the Tapestry action for the configured closing window."""

        form = soup.find("form", id="ListTendersbyDate")
        if not isinstance(form, Tag):
            raise ValueError("Could not find the ListTendersbyDate form")

        action_name = self.CLOSING_WINDOW_ACTIONS[
            self.config.closing_window_days
        ]

        form_data: list[tuple[str, str]] = []
        for field in form.find_all("input"):
            name = field.get("name")
            if not name or name in {"submitmode", "submitname"}:
                continue

            field_type = str(field.get("type", "text")).lower()
            if field_type in {"submit", "button", "image"}:
                continue

            form_data.append((str(name), str(field.get("value", ""))))

        form_data.extend(
            [
                ("submitmode", "link"),
                ("submitname", action_name),
            ]
        )

        form_action = form.get("action")
        if not form_action:
            raise ValueError("Closing-date form has no action")

        result_soup = self.post_soup(
            urljoin(self.config.base_url, str(form_action)),
            form_data,
        )

        expected_text = (
            f"closing within {self.config.closing_window_days} days"
        )
        page_text = result_soup.get_text(" ", strip=True).lower()
        if expected_text not in page_text:
            raise ValueError(
                "GePNIC did not return the expected closing-date listing"
            )

        return result_soup

    # def collect_tender_urls(
    #     self,
    #     first_page: BeautifulSoup,
    #     max_results: int | None = None,
    # ) -> list[str]:
    #     """Parse every results page and deduplicate by tender ID."""

    #     urls_by_tender_id: dict[str, str] = {}
    #     visited_pages: set[str] = set()
    #     current_soup = first_page
    #     page_number = 1

    #     while True:
    #         discovered = self.parse_listing_page(current_soup)
    #         new_count = 0

    #         for tender_id, tender_url in discovered:
    #             if tender_id in urls_by_tender_id:
    #                 continue
    #             urls_by_tender_id[tender_id] = tender_url
    #             new_count += 1

    #         print(
    #             f"[DISCOVER] {self.config.name}: "
    #             f"page {page_number}, {new_count} new tender(s)"
    #         )

    #         if (
    #             max_results is not None
    #             and len(urls_by_tender_id) >= max_results
    #         ):
    #             break

    #         next_page_url = self.find_next_page_url(current_soup)
    #         if next_page_url is None:
    #             break
    #         if next_page_url in visited_pages:
    #             raise RuntimeError(
    #                 f"Pagination loop detected at {next_page_url}"
    #             )

    #         visited_pages.add(next_page_url)
    #         sleep(self.config.request_delay_seconds)
    #         current_soup = self.get_soup(next_page_url)
    #         page_number += 1

    #     tender_urls = list(urls_by_tender_id.values())
    #     if max_results is not None:
    #         return tender_urls[:max_results]
    #     return tender_urls
    def collect_tender_urls(
            self,
            first_page: BeautifulSoup,
            max_results: int | None = None,
    )-> list[str]:
        return list(
            self.collect_tender_records(
                first_page,
                max_results = max_results,
            ).values()
        )
    def collect_tender_records(
            self,
            first_page: BeautifulSoup,
            max_results: int | None = None,
    )-> dict[str,str]:
        records: dict[str,str] = {}
        visited_pages: set[str] = set()
        current_soup = first_page
        page_number = 1

        while True:
            try:
                discovered = self.parse_listing_page(current_soup)
            except Exception as exc:
                self.record_discovery_issue(f"Listing page {page_number}", exc)
                return records
            new_count = 0
            for tender_id,tender_url in discovered:
                if tender_id not in records:
                    records[tender_id] = tender_url
                    new_count += 1
            print(
                f"[DISCOVER] {self.config.name}: "
                f"page {page_number}, {new_count} new tender(s)"
            )
            if page_number > 1 and new_count == 0:
                self.record_discovery_issue(f"Listing page {page_number}",
                    "Pagination returned no new tenders; "
                    "possible repeated page or expired session"
                )
                return records
            if max_results is not None and len(records) >= max_results:
                return dict(list(records.items())[:max_results])
            next_url = self.find_next_page_url(current_soup)
            if next_url is None:
                return records
            if next_url in visited_pages:
                self.record_discovery_issue(next_url, "Pagination loop detected")
                return records
            visited_pages.add(next_url)
            sleep(self.config.request_delay_seconds)
            try:
                current_soup = self.get_soup(next_url)
            except Exception as exc:
                self.record_discovery_issue(next_url, exc)
                return records
            page_number += 1

    # def parse_listing_page(
    #     self,
    #     soup: BeautifulSoup,
    # ) -> list[tuple[str, str]]:
    #     table = self.find_tender_table(soup)
    #     discovered: list[tuple[str, str]] = []

    #     for row in table.find_all("tr"):
    #         detail_link = None
    #         for link in row.find_all("a", href=True):
    #             href = str(link["href"])
    #             if (
    #                 "DirectLink" in href
    #                 and "FrontEndListTendersbyDate" in href
    #             ):
    #                 detail_link = link
    #                 break

    #         if detail_link is None:
    #             continue

    #         row_text = row.get_text(" ", strip=True)
    #         tender_id_match = self.TENDER_ID_PATTERN.search(row_text)
    #         if tender_id_match is None:
    #             print(
    #                 "[DISCOVER WARNING] Skipped listing row without "
    #                 f"a tender ID: {row_text[:160]}"
    #             )
    #             continue

    #         discovered.append(
    #             (
    #                 tender_id_match.group(0),
    #                 urljoin(
    #                     self.config.base_url,
    #                     str(detail_link["href"]),
    #                 ),
    #             )
    #         )

    #     return discovered

    def parse_listing_page(
            self,
            soup: BeautifulSoup,
    )-> list[tuple[str,str]]:
        table = self.find_tender_table(soup)
        discovered = []
        allowed_pages = {
            "FrontEndListTendersbyDate",
            "FrontEndTendersByOrganisation",
            "FrontEndViewTender",
        }
        for row in table.find_all("tr"):
            cells = row.find_all(["td","th"],recursive=False)
            # GePNIC closing listings use "1."; ePublishing also uses "1".
            if not cells or not re.fullmatch(r"\d+\.?", cells[0].get_text(strip=True)):
                continue
            row_text = row.get_text(" ",strip=True)
            match = self.TENDER_ID_PATTERN.search(row_text)
            if match is None:
                self.record_discovery_issue("Tender row", f"No tender ID: {row_text}")
                continue
            detail_link = None
            for link in row.find_all("a",href = True):
                href = str(link["href"])
                query = parse_qs(urlparse(href).query)
                page = query.get("page",[""])[0]
                component = query.get("component",[""])[0]

                if page in allowed_pages and "DirectLink" in component:
                    detail_link = href
                    break
            if detail_link is None:
                self.record_discovery_issue(match.group(0), "No recognised detail link")
                continue
            discovered.append(
                (
                    match.group(0),
                    urljoin(self.config.base_url, detail_link),
                )
            )
        return discovered
    @staticmethod
    def find_latest_tenders_table(soup: BeautifulSoup) -> Tag:
        required_markers = (
            "tender title",
            "reference no",
            "closing date",
            "bid opening date",
        )
        candidates: list[Tag] = []

        for table in soup.find_all("table"):
            table_text = table.get_text(" ", strip=True).lower()
            if "corrigendum title" in table_text:
                continue
            if all(marker in table_text for marker in required_markers):
                candidates.append(table)

        if not candidates:
            raise ValueError("Could not find the Latest Tenders table")

        return min(candidates, key=lambda table: len(table.find_all("tr")))

    @staticmethod
    # def find_tender_table(soup: BeautifulSoup) -> Tag:
    #     required_markers = (
    #         "e-published date",
    #         "bid submission closing date",
    #         "tender opening date",
    #         "title and ref.no./tender id",
    #         "organisation chain",
    #     )
    #     candidates: list[Tag] = []

    #     for table in soup.find_all("table"):
    #         table_text = table.get_text(" ", strip=True).lower()
    #         if all(marker in table_text for marker in required_markers):
    #             candidates.append(table)

    #     if not candidates:
    #         raise ValueError("Could not find the active-tender results table")

    #     return min(candidates, key=lambda table: len(table.find_all("tr")))

    def find_tender_table(soup: BeautifulSoup) -> Tag:
        candidates = []
        for table in soup.find_all("table"):
            text = table.get_text(" ",strip=True).lower()
            common = (
            "e-published date",
            "title and ref.no./tender id",
            "organisation chain",
            )
            dates_present = (
                "closing date" in text
                and "opening date" in text
            )
            if dates_present and all( marker in text for marker in common):
                candidates.append(table)
        if not candidates:
            raise GePNICPortal.missing_listing_error(
            soup, "Tender results"
        )
        return min(candidates,key=  lambda item: len(item.find_all("tr")))
    
    # def find_next_page_url(self, soup: BeautifulSoup) -> str | None:
    #     for link in soup.find_all("a", href=True):
    #         href = str(link["href"])
    #         label = link.get_text(" ", strip=True)
    #         if label == ">" and "TablePages.linkFwd" in href:
    #             return urljoin(self.config.base_url, href)
    #     return None
    def find_next_page_url(self, soup: BeautifulSoup) -> str | None:
        for link in soup.find_all("a", href=True):
            href = str(link["href"])
            if "TablePages.linkFwd" in unquote(href):
                return urljoin(self.config.base_url, href)
        return None

    @staticmethod
    def clean(text: str | None) -> str | None:
        if not text:
            return None
        return re.sub(r"\s+", " ", text).strip()

    def extract_table(self, soup: BeautifulSoup) -> dict[str, str]:
        data: dict[str, str] = {}

        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            for index in range(0, len(cells) - 1, 2):
                key = self.clean(cells[index].get_text(" ", strip=True))
                value = self.clean(
                    cells[index + 1].get_text(" ", strip=True)
                )
                if key and value:
                    data[key.rstrip(":").lower()] = value

        return data

    def extract_documents(
        self,
        soup: BeautifulSoup,
        page_url: str,
    ) -> list[TenderDocument]:
        documents: list[TenderDocument] = []
        seen_urls: set[str] = set()

        for link in soup.find_all("a", href=True):
            href = str(link["href"])
            text = self.clean(link.get_text(" ", strip=True))
            filename = text or href.split("/")[-1]

            if not filename.lower().endswith(
                (".pdf", ".xls", ".xlsx", ".doc", ".docx", ".zip")
            ):
                continue

            document_url = urljoin(page_url, href)
            if document_url in seen_urls:
                continue

            seen_urls.add(document_url)
            documents.append(
                TenderDocument(name=filename, url=document_url)
            )

        return documents

    def extract_metadata(self, url: str) -> TenderMetadata:
        soup = self.get_soup(url)
        table = self.extract_table(soup)
        documents = self.extract_documents(soup, page_url=url)

        page_title = (
            soup.title.get_text(strip=True) if soup.title else None
        )
        title = (
            table.get("title")
            or table.get("work description")
            or page_title
            or "Untitled tender"
        )
        tender_id = table.get("tender id")
        if not tender_id:
            raise ValueError("The detail page did not contain a tender ID")

        return TenderMetadata(
            portal=self.config.name,
            tender_id=tender_id,
            reference_number=table.get("tender reference number"),
            title=title,
            description=table.get("work description"),
            organisation=table.get("organisation chain"),
            location=table.get("location"),
            pincode=table.get("pincode"),
            tender_type=table.get("tender type"),
            tender_category=table.get("tender category"),
            product_category=table.get("product category"),
            published_date=table.get("published date"),
            submission_deadline=table.get("bid submission end date"),
            bid_opening_date=table.get("bid opening date"),
            source_url=url,
            documents=documents,
        )

    def download_document(self, url: str, destination: Path) -> None:
        """Download through the same session used for portal discovery."""

        with self.session.get(url, timeout=60, stream=True) as response:
            response.raise_for_status()
            with destination.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)

    @staticmethod
    def missing_listing_error(soup:BeautifulSoup,context:str)->ValueError:
        if soup.find("input", attrs={"name": "captchaText"}) is not None:
            return ValueError(
                f"{context}: no usable listing;CAPTCHA interaction required")
        return ValueError(
            f"{context}:expected listing missing;"
            "possible expired session or portal layout change"
        )

    def discover_by_organisation(
            self,
            limit:int | None = None,
    )-> list[str]:
        index_url = (
            self.config.base_url + "?page=FrontEndTendersByOrganisation&service=page"
        )
        soup = self.get_soup(index_url)

        candidates = []
        for table in soup.find_all("table"):
            text = table.get_text(" ", strip = True).lower()
            if "organisation name" in text and "tender count" in text:
                candidates.append(table)

        if not candidates:
            raise self.missing_listing_error(soup,"Organisation index")
        table = min(candidates, key=  lambda item: len(item.find_all("tr")))
        organisations = []
        seen_links = set()

        for row in table.find_all("tr"):
            cells = row.find_all(["td","th"], recursive = False)
            if len(cells) != 3:
                continue
            serial = cells[0].get_text(" ",strip = True)
            if not re.fullmatch(r"\d+\.?", serial):
                continue
            name = cells[1].get_text(" ", strip = True)
            count_text = cells[2].get_text(" ", strip = True).replace(",","")
            if not count_text.isdigit():
                self.record_discovery_issue(name, f"Invalid tender count: {count_text}")
                continue
            expected_count = int(count_text)
            if expected_count == 0:
                continue
            link = cells[2].find("a",href = True)
            if link is None:
                self.record_discovery_issue(name, "Missing tender-list link")
                continue
            href = str(link["href"])
            query = parse_qs(urlparse(href).query)
            if query.get("page") != ["FrontEndTendersByOrganisation"]:
                self.record_discovery_issue(name, f"Unexpected organisation link: {href}")
                continue

            url = urljoin(self.config.base_url,href)
            if url not in seen_links:
                seen_links.add(url)
                organisations.append((name,expected_count,url))
        if not organisations:
            raise self.missing_listing_error(
                soup,"Organisation index contains no usable links"
            ) 
        print(
        f"[DISCOVER] {self.config.name}: "
        f"{len(organisations)} organisations"
    )

        urls_by_id: dict[str, str] = {}
        for name,expected_count,url in organisations:
            remaining = None if limit is None else limit - len(urls_by_id)
            if remaining is not None and remaining <= 0:
                break
            sleep(self.config.request_delay_seconds)
            self.discovery_context = name
            try:
                listing = self.get_soup(url)
                records = self.collect_tender_records(listing, max_results=remaining)
            except Exception as exc:
                self.record_discovery_issue(url, exc)
                continue
            finally:
                self.discovery_context = ""
            # A limit intentionally produces incomplete coverage.
            limited = remaining is not None and len(records) >= remaining
            if not limited and len(records) != expected_count:
                self.record_discovery_issue(name,
                    f"Expected {expected_count} tenders, "
                    f"collected {len(records)}. "
                    "Counts may have changed or pagination may be incomplete; "
                    "discovery has not been treated as complete."
                )

            for tender_id, tender_url in records.items():
                urls_by_id.setdefault(tender_id, tender_url)

            print(
                f"[DISCOVER] {name}: {len(records)} collected; "
                f"{len(urls_by_id)} unique tenders overall"
            )

        urls = list(urls_by_id.values())
        return urls if limit is None else urls[:limit]
         
