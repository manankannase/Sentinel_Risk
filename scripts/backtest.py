"""Expanding chronological backtests; each fold trains and tunes without its future test block."""
import argparse
import json
from pathlib import Path

import pandas as pd

from sentinel.features import utc
from sentinel.train import synthetic_rows, train


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path)
    parser.add_argument('--rows', type=int, default=24000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output directory; previous evidence is immutable')
    if not 8000 <= args.rows <= 1_000_000:
        parser.error('rows must be 8000..1000000')
    rows = pd.read_csv(args.csv).to_dict('records') if args.csv else list(synthetic_rows(args.rows))
    rows.sort(key=lambda row: utc(row['occurred_at']))
    results = []
    for fraction in (0.6, 0.8, 1.0):
        prefix = rows[:int(len(rows) * fraction)]
        output = args.output / f'fold-{len(results) + 1}'
        try:
            metrics = train(output, prefix, source='user-supplied CSV' if args.csv else 'synthetic')
            results.append({'rows': len(prefix), 'status': 'passed', 'test': metrics['test'],
                            'cutoffs': metrics['cutoffs'], 'test_by_channel': metrics['test_by_channel']})
        except ValueError as exc:
            results.append({'rows': len(prefix), 'status': 'rejected', 'reason': str(exc)})
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'report.json').write_text(json.dumps({
        'folds': results, 'source': 'supplied CSV' if args.csv else 'synthetic',
        'limitations': 'Overlapping expanding training windows; no population or production guarantee.'}, indent=2) + '\n')
    print(json.dumps([{'rows': r['rows'], 'status': r['status']} for r in results]))
    if any(r['status'] != 'passed' for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
