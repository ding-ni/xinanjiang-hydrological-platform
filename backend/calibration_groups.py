"""Physical flood groups with one trajectory and one unique-hour objective."""
from __future__ import annotations

from datetime import datetime, timedelta


def _time(value):
    stamp = datetime.fromisoformat(str(value))
    if stamp.tzinfo is not None or stamp.minute or stamp.second or stamp.microsecond:
        raise ValueError('洪水组时刻须为本地整点')
    return stamp


def group_windows(rows):
    """Transitive closed-interval overlap, including a shared ending hour.

    Never change a user's split. A conflicting assignment must be corrected
    before queueing; even indirect overlap cannot straddle training/holdout.
    """
    groups = []
    selected = [row for row in rows if row.get('purpose') != '未使用'
                and row.get('status') != 'inactive']
    for row in sorted(selected, key=lambda r: (r['start'], r['end'], r['id'])):
        start, end = _time(row['start']), _time(row['end'])
        if groups and start <= _time(groups[-1]['end']):
            current = groups[-1]
            if current['purpose'] != row['purpose']:
                raise ValueError('重叠洪水不能跨率定、验证或独立检验用途：'
                                 + '、'.join(current['memberEventIds'] + [row['id']])
                                 + '；请将这些窗口设为同一用途')
            current['end'] = max(end, _time(current['end'])).isoformat()
            current['memberEventIds'].append(row['id'])
        else:
            groups.append({'id': 'FLOOD-GROUP-%03d' % (len(groups) + 1),
                           'purpose': row['purpose'], 'start': start.isoformat(),
                           'end': end.isoformat(), 'memberEventIds': [row['id']]})
    for group in groups:
        group['uniqueHours'] = int((_time(group['end']) - _time(group['start'])).total_seconds() / 3600) + 1
    return groups


def scoring_events(rows, groups, group_names):
    """Create union runs; singleton IDs stay compatible with historic results."""
    result, membership = [], {}
    for group in groups:
        identifier = group['memberEventIds'][0] if len(group['memberEventIds']) == 1 else group['id']
        result.append({'event_id': identifier, 'year': _time(group['start']).year,
                       'start': group['start'], 'end': group['end'],
                       'group': group_names[group['purpose']]})
        membership.update({event_id: identifier for event_id in group['memberEventIds']})
    return result, membership


def continuous_origin(catalog, warmup_hours):
    if not catalog:
        raise ValueError('连续原点需要已登记的洪水目录')
    first = min(_time(row['start']) for row in catalog)
    return (datetime(first.year, 1, 1) - timedelta(hours=warmup_hours)).isoformat()
