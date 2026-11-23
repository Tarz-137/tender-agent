"""Cumulative opportunities assembled from immutable discovery run snapshots."""

import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from discovery_mvp.engine import canonical_url
from discovery_mvp.values import prepare_report, with_tender_value

LIBRARY_VERSION = 2

# Ignore procurement phrasing, but retain scope, place, year and package details.
TITLE_BOILERPLATE = set('a an the and or of for at in on with including to by design designing supply supplying installation installing commissioning'.split())
SCOPE_WORDS = set('musical fountain multimedia water screen show integrated audio visual projection mapping laser lighting system construction work development'.split())
TOKEN_ALIASES = {'fountains': 'fountain', 'screens': 'screen', 'shows': 'show', 'systems': 'system'}
DETAIL_MARKER = re.compile(
    r'\b(ward|package|lot|phase|zone|sector|block|part)\b\s*(?:no\.?|number|:|-)?\s*'
    r'([a-z]?\d+[a-z]?|[ivx]+|[a-z])\b', re.I)

TENDER_ID = re.compile(r"\b20\d{2}[_-][a-z][a-z0-9]*[_-][a-z0-9]+[_-]\d+\b", re.I)
GEM_ID = re.compile(r"\bGEM/20\d{2}/[A-Z]/\d+\b", re.I)
DEPARTMENT_REF = re.compile(r"\b\d+/(?:EE|GM|SM|SE|CE)\b[\w ()/.-]{3,90}?/20\d{2}[-/]\d{2,4}\b", re.I)


def identifiers(item):
    text = ' '.join([item['title'], item['url'], *item.get('snippets', [])])
    ids = {match.group().upper().replace('-', '_') for match in TENDER_ID.finditer(text)}
    gems = {match.group().upper() for match in GEM_ID.finditer(text)}
    # A summary naming several notices isn't a reliable identity bridge.
    refs = {'REF:' + re.sub(r'\s+', '', match.group().upper()) for match in DEPARTMENT_REF.finditer(text)}
    return (ids | gems | refs) if len(ids) <= 1 and len(gems) <= 1 and len(refs) <= 1 else set()


def normalized_title(title):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', title).casefold()))


def title_details(item):
    return {TOKEN_ALIASES.get(token, token) for token in normalized_title(item['title']).split()
            if token not in TITLE_BOILERPLATE}


def detail_markers(item):
    text = ' '.join([item['title'], *item.get('snippets', [])]).lower()
    return set(DETAIL_MARKER.findall(text))


def compatible_values(left, right, required=False):
    valued = [with_tender_value(item) for item in (left, right)]
    if any(item['tender_value_status'] == 'conflicting' for item in valued):
        return False
    lv, rv = (item['tender_value_inr'] for item in valued)
    if lv is None or rv is None:
        return not required
    # Allow only rounding such as 3.7314 crore versus 3.73 crore.
    return abs(lv - rv) / max(lv, rv) <= .001


def missing_id_match(left, right):
    """Corroborated title match, never an unrestricted fuzzy similarity merge."""
    if not left.get('deadline') or left['deadline'] != right.get('deadline'):
        return False
    if not compatible_values(left, right):
        return False
    if (normalized_title(left['title']) == normalized_title(right['title'])
            and len(normalized_title(left['title']).split()) >= 8):
        lm, rm = detail_markers(left), detail_markers(right)
        return not lm or not rm or lm == rm
    core = title_details(left)
    return (detail_markers(left) == detail_markers(right)
            and core == title_details(right) and len(core) >= 6
            and len({token for token in core - SCOPE_WORDS if not token.isdigit()}) >= 2
            and compatible_values(left, right, required=True))


def build_library(reports):
    reports = sorted({report['created_at']: report for report in reports if report}.values(),
                     key=lambda report: report['created_at'])
    if not reports:
        return None
    latest = reports[-1]
    observations = [(report['created_at'], item) for report in reports for item in report['candidates']]
    parents = list(range(len(observations)))
    group_ids = [identifiers(item) for _, item in observations]
    group_markers = [detail_markers(item) for _, item in observations]

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(left, right, title_match=False):
        left, right = find(left), find(right)
        if left == right:
            return
        if title_match and group_markers[left] and group_markers[right] and group_markers[left] != group_markers[right]:
            return
        left_ids = {value for value in group_ids[left] if not value.startswith('REF:')}
        right_ids = {value for value in group_ids[right] if not value.startswith('REF:')}
        if left_ids and right_ids and not left_ids & right_ids:
            return
        if group_ids[left] and group_ids[right] and not group_ids[left] & group_ids[right]:
            return  # Same title/URL can be reused for a separate tender.
        parents[right] = left
        group_ids[left] |= group_ids[right]
        group_markers[left] |= group_markers[right]

    id_index, url_index = {}, {}
    for index, (_, item) in enumerate(observations):
        for ident in group_ids[index].copy():
            if ident in id_index:
                join(index, id_index[ident])
            id_index[ident] = index
        url = canonical_url(item['url'])
        if url in url_index:
            join(index, url_index[url])
        url_index[url] = index

    # Retain the existing exact-title fallback for unidentified mirrors. Known
    # tenders are resolved below, after all possible targets are collected.
    exact_index = {}
    for index, (_, item) in enumerate(observations):
        if group_ids[find(index)] or not item.get('deadline'):
            continue
        title = normalized_title(item['title'])
        if len(title.split()) < 8:
            continue
        key = (title, item['deadline'])
        for other in exact_index.get(key, []):
            if missing_id_match(item, observations[other][1]):
                join(index, other, title_match=True)
        exact_index.setdefault(key, []).append(index)

    # Attach an unidentified group only to one unambiguous identified group.
    # Compute all proposals before joining: a missing-ID listing cannot become
    # a transitive bridge between different official tenders.
    strong_groups = {}
    for index in range(len(observations)):
        strong_groups.setdefault(find(index), []).append(index)
    identified = [root for root in strong_groups if group_ids[root]]
    proposals = []
    for root, members in strong_groups.items():
        if group_ids[root]:
            continue
        targets = set()
        for target in identified:
            for left in members:
                item = observations[left][1]
                # Multi-notice summaries are not identity evidence.
                text = ' '.join([item['title'], item['url'], *item.get('snippets', [])])
                if len(set(TENDER_ID.findall(text))) > 1 or len(set(GEM_ID.findall(text))) > 1:
                    continue
                for right in strong_groups[target]:
                    other = observations[right][1]
                    if missing_id_match(item, other):
                        targets.add(target)
        if len(targets) == 1:
            proposals.append((root, targets.pop()))
    for root, target in proposals:
        join(target, root, title_match=True)

    groups = {}
    for index, observation in enumerate(observations):
        groups.setdefault(find(index), []).append(observation)
    candidates = []
    for root, group in groups.items():
        timestamps = sorted({timestamp for timestamp, _ in group})
        first_seen, last_seen = timestamps[0], timestamps[-1]
        current = [item for timestamp, item in group if timestamp == last_seen]
        def official(item):
            host = urlsplit(item['url']).hostname or ''
            return host.endswith(('.gov.in', '.nic.in')) or host in ('gem.gov.in', 'bidplus.gem.gov.in')
        representative = max(current, key=official)
        aliases = sorted(group_ids[find(root)])
        ids = [value for value in aliases if not value.startswith('REF:')]
        refs = [value[4:] for value in aliases if value.startswith('REF:')]
        identity = 'tender:' + (ids or aliases)[0] if aliases else 'url:' + canonical_url(group[0][1]['url'])
        opportunity_id = 'opp_' + hashlib.sha256(identity.encode()).hexdigest()[:20]
        sources = {}
        for timestamp, item in group:
            url = canonical_url(item['url'])
            source = sources.setdefault(url, {'url': item['url'], 'title': item['title'],
                                             'first_seen': timestamp, 'last_seen': timestamp})
            source.update(last_seen=timestamp, title=item['title'])
        candidate = {**representative, 'id': opportunity_id, 'opportunity_id': opportunity_id,
                     'tender_ids': ids, 'tender_references': refs,
                     'record_ids': sorted({item['id'] for _, item in group}),
                     'sources': list(sources.values()), 'first_seen': first_seen, 'last_seen': last_seen,
                     'seen_in_runs': len(timestamps),
                     'discovery_status': 'not_seen' if last_seen != latest['created_at'] else
                                         'new' if first_seen == last_seen else 'rediscovered',
                     'change': 'new' if first_seen == latest['created_at'] else 'unchanged',
                     'groups': sorted({value for _, item in group for value in item.get('groups', [])}),
                     'providers': sorted({value for _, item in group for value in item.get('providers', [])})}
        if with_tender_value(candidate)['tender_value_inr'] is None:
            for timestamp, item in reversed(group):
                valued = with_tender_value(item)
                if valued['tender_value_inr'] is not None:
                    candidate['value_fallback'] = {'text': valued['tender_value_evidence'][0],
                                                   'source_url': item['url'], 'observed_at': timestamp}
                    break
        candidates.append(candidate)
    summary = {status: sum(item['discovery_status'] == status for item in candidates)
               for status in ('new', 'rediscovered', 'not_seen')}
    return prepare_report({**latest, 'scope': 'library', 'candidates': candidates,
                           'library_version': LIBRARY_VERSION,
                           'library_summary': {**summary, 'total': len(candidates), 'run_count': len(reports),
                                               'latest_run_listings': len(latest['candidates'])}})


def saved_reports(directory):
    directory = Path(directory)
    files = sorted((directory / 'runs').glob('*.json'))
    if (directory / 'latest.json').exists():
        files.append(directory / 'latest.json')
    reports, issues = [], []
    for path in files:
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(report.get('candidates'), list) or not isinstance(report.get('created_at'), str):
                raise ValueError('invalid report')
            if any(not isinstance(item, dict) or not all(key in item for key in ('title', 'url', 'id', 'score'))
                   for item in report['candidates']):
                raise ValueError('invalid candidates')
            reports.append(report)
        except (ValueError, OSError, AttributeError):
            issues.append(f'Could not read saved discovery run: {path.name}')
    return reports, issues


def load_library(directory):
    from discovery_mvp.storage import Store
    return Store(directory).library()


def persist_library(directory):
    return load_library(directory)
