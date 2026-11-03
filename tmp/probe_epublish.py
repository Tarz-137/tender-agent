"""Read-only live check of NMDC organisation discovery; no CAPTCHA submission."""
import json
import sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portals.gepnic import GePNICPortal
from portals.portal_config import PortalConfig


def main():
    portal = GePNICPortal(PortalConfig(
        key="cppp_epublish", name="CPPP ePublishing",
        base_url="https://eprocure.gov.in/epublish/app", platform="gepnic",
    ))
    # A fresh requests.Session, with no imported browser cookies.
    index = portal.get_soup(portal.config.base_url +
                            "?page=FrontEndTendersByOrganisation&service=page")
    rows = []
    for row in index.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) == 3 and cells[1].get_text(" ", strip=True) == "NMDC Limited":
            rows.append(row)
    if len(rows) != 1:
        raise RuntimeError("NMDC organisation row unavailable; possible CAPTCHA or layout change")
    link = rows[0].find("a", href=True)
    if link is None:
        raise RuntimeError("NMDC tender-count link unavailable")
    print("Organisation:", rows[0].get_text(" ", strip=True), flush=True)
    soup = portal.get_soup(urljoin(portal.config.base_url, link["href"]))
    target_id = "2026_NMDC_852218_1"
    ids = set(portal.TENDER_ID_PATTERN.findall(soup.get_text(" ", strip=True)))
    print("Tender IDs on NMDC listing:", len(ids), flush=True)
    print("Target discovered:", target_id in ids, flush=True)
    matches = [row for row in soup.find_all("tr")
               if target_id in row.get_text(" ", strip=True)]
    if not matches:
        raise RuntimeError("Target not present on first NMDC page")
    row = min(matches, key=lambda item: len(str(item)))
    detail = row.find("a", href=True)
    tender = portal.extract_metadata(urljoin(portal.config.base_url, detail["href"]))
    if tender.tender_id != target_id:
        raise RuntimeError("Detail tender ID mismatch")
    print(json.dumps({
        "tender_id": tender.tender_id, "title": tender.title,
        "submission_deadline": tender.submission_deadline,
        "documents_found": len(tender.documents),
        "browser_cookies_used": False, "captcha_submitted": False,
    }, indent=2), flush=True)
    try:
        parsed = portal.collect_tender_records(soup)
        assert len(parsed) == len(ids)
        assert target_id in parsed
        print("Production collector records:", len(parsed))
    except ValueError as exc:
        print("Existing listing parser limitation:", str(exc))
        print("Listing link examples:", [a["href"] for a in soup.find_all("a", href=True)
              if "DirectLink" in a["href"]][:2])
        raise


if __name__ == "__main__":
    main()
