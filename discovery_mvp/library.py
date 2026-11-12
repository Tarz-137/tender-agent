"""Cumulative opportunities assembled from immutable discovery run snapshots."""

import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from discovery_mvp.engine import canonical_url
from discovery_mvp.values import prepare_report, with_tender_value

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


def build_library(reports):
    reports = sorted({report['created_at']: report for report in reports if report}.values(),
                     key=lambda report: report['created_at'])
    if not reports:
        return None
    latest = reports[-1]
    observations = [(report['created_at'], item) for report in reports for item in report['candidates']]
    parents = list(range(len(observations)))
    group_ids = [identifiers(item) for _, item in observations]

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(left, right):
        left, right = find(left), find(right)
        if left == right:
            return
        left_ids = {value for value in group_ids[left] if not value.startswith('REF:')}
        right_ids = {value for value in group_ids[right] if not value.startswith('REF:')}
        if left_ids and right_ids and not left_ids & right_ids:
            return
        if group_ids[left] and group_ids[right] and not group_ids[left] & group_ids[right]:
            return  # Same title/URL can be reused for a separate tender.
        parents[right] = left
        group_ids[left] |= group_ids[right]

    id_index, url_index, title_index = {}, {}, {}
    for index, (_, item) in enumerate(observations):
        for ident in group_ids[index].copy():
            if ident in id_index:
                join(index, id_index[ident])
            id_index[ident] = index
        url = canonical_url(item['url'])
        if url in url_index:
            join(index, url_index[url])
        url_index[url] = index
        title_key = (normalized_title(item['title']), item.get('deadline'))
        if title_key[1] and len(title_key[0].split()) >= 8:
            if title_key in title_index:
                join(index, title_index[title_key])
            title_index[title_key] = index

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
                           'library_version': 1,
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
