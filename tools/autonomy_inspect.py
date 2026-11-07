"""Offline CLI: inspect authority traces or goals without mutating production."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
from runtime.autonomy.goal_events import GoalEventLedger


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--trace', type=Path, help='JSONL from ERIS_AUTONOMY_TRACE')
    p.add_argument('--goals-db', type=Path, help='explicit isolated goal ledger')
    args = p.parse_args()
    if not args.trace and not args.goals_db:
        p.error('specify --trace and/or --goals-db')
    if args.trace:
        count = Counter()
        spans = {}
        for row in args.trace.read_text(encoding='utf-8').splitlines():
            try:
                item = json.loads(row)
            except ValueError:
                continue
            count[item.get('event', 'unknown')] += 1
            if item.get('correlation_id'):
                spans.setdefault(item['correlation_id'], set()).add(item.get('event', 'unknown'))
        print(json.dumps({'trace_counts': count, 'correlated_spans': len(spans),
                          'v103_selected': count['executive.v103_winner'],
                          'neuro_selected': count['executive.neuro_fallback'],
                          'autonomous_opportunities': count['autonomous.iteration.start']}, indent=2))
    if args.goals_db:
        if not args.goals_db.exists():
            p.error('goal db does not exist; refusing to create during inspection')
        registry = GoalEventLedger(args.goals_db)
        print(json.dumps({'goals': registry.panel_projection(), 'unknown_actions': []}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
