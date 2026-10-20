"""Build a standalone public demo: synthetic data and model only, never credentials or a live database."""
import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path

from sentinel.features import build_features
from sentinel.ml import RiskModel
from sentinel.train import synthetic_rows

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/demo')
    args = parser.parse_args()
    target = args.output
    target.mkdir(parents=True, exist_ok=True)
    model = RiskModel(ROOT/'artifacts/inr-v2')
    if model.manifest.get('data_source') != 'synthetic':
        raise ValueError('Only synthetic models may be exported to the public demo')
    config = json.loads(model.model.save_config())
    base = json.loads(config['learner']['learner_model_param']['base_score'])
    base = base[0] if isinstance(base,list) else base
    bundle = {'manifest': model.manifest, 'metrics': model.metrics,
              'base_margin': math.log(base/(1-base)),
              'trees': [json.loads(tree) for tree in model.model.get_dump(dump_format='json')]}
    (target/'model.json').write_text(json.dumps(bundle, separators=(',',':')))
    samples, history, parity = [], {}, []
    # Keep the actual generated event times/features; browser shifts the whole replay by whole days.
    for tx in synthetic_rows(400):
        tx.pop('label')
        tx.pop('label_available_at')
        past = history.setdefault(tx['customer_id'], [])
        values = build_features(tx, past)
        parity.append({'transaction': tx.copy(), 'history': past.copy(), 'features': values, 'score': model.predict(values)})
        past.append(tx)
        samples.append(tx)
    (target/'samples.json').write_text(json.dumps(samples, separators=(',',':')))
    # Parity fixtures are development evidence, not shipped to visitors.
    parity_path = target.parent/'demo-parity.json'
    parity_path.write_text(json.dumps(parity, separators=(',',':')))
    for name in ('app.js','app.css'):
        shutil.copyfile(ROOT/'src/sentinel/static'/name,target/name)
    for name in ('engine.js','api.js'):
        shutil.copyfile(ROOT/'demo'/name,target/f'demo-{name}')
    html = (ROOT/'src/sentinel/static/index.html').read_text()
    html = html.replace('/static/app.css','./app.css').replace('/static/app.js','./app.js')
    html = html.replace('<script src="./app.js" defer></script>',
        '<script src="./demo-engine.js" defer></script><script src="./demo-api.js" defer></script><script src="./app.js" defer></script>')
    html = html.replace('Sign in with your workspace API key. Credentials remain in this browser tab\'s memory only.',
        'Public synthetic demo. Runs entirely in this tab. No account, payment data, or API key required.')
    html = html.replace('type="password" required autocomplete="off"', 'type="text" value="public-demo" readonly autocomplete="off"')
    html = html.replace('Workspace key</label>', 'Demo access</label>')
    html = html.replace('Local setup: use the key generated in <code>.secrets/admin_token</code>. Never publish or share this file.',
        'Uses a synthetic INR model. Changes disappear when this tab reloads.')
    html = html.replace('Simulation environment · No payments are processed', 'Browser-only demo · No payments are processed')
    html = html.replace('Synthetic environment', 'Public browser demo')
    html = html.replace('One workspace for transaction risk, model evidence, and a traceable decision history.',
        'Explore a synthetic INR fraud model. For backend setup, research, and deployment instructions, visit the project repository.')
    html = html.replace('<footer>Sentinel Risk', '<footer><a href="https://github.com/manankannase/Sentinel_Risk">Source &amp; deployment guide</a> · Sentinel Risk')
    html = html.replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\'; style-src \'self\'; img-src \'self\' data:; connect-src \'self\'; base-uri \'none\'; form-action \'none\'">')
    (target/'index.html').write_text(html)
    (target/'.nojekyll').write_text('')
    files = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir() if p.is_file() and p.name!='checksums.json'}
    (target/'checksums.json').write_text(json.dumps(files, indent=2)+'\n')
    print(f'Public demo built at {target}; synthetic assets only.')


if __name__ == '__main__':
    main()
