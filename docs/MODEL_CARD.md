# INR model card

The shipped `artifacts/inr-v2` model is a synthetic XGBoost classifier with sigmoid calibration. It is suitable for simulation and engineering shadow tests, not autonomous payment decisions. Its output is conditional on the synthetic generator, not a demonstrated probability of Indian payment fraud.

## Contract and provenance

- Currency: INR. Amounts: integer paise; ₹123.45 is `12345`.
- Channels: ecommerce, POS, ATM, UPI, IMPS, NEFT, RTGS. The API's ₹10 lakh cap is an application bound, not an official limit for any rail.
- All event storage uses UTC. Time-of-day features use Asia/Kolkata.
- Opaque `merchant_id` represents merchant/payee. Never supply UPI PINs, OTPs, card/account numbers or direct personal identifiers.
- Sixteen features: log amount, relative amount, 1h/24h counts, log 24h volume, 24h device/country novelty, cyclic hour, ecommerce/ATM indicators, 5-minute count, cold start, new payee, UPI and bank-transfer indicators.
- Only strictly earlier INR events for the same customer enter the 24h window. Tenant filtering occurs before feature construction in the API.
- Manifest records feature version, model/dataset/metrics hashes, calibration and thresholds. Production independently pins the manifest and model. Original v1 USD artifacts remain for historical reference and are rejected by the new loader.

## Evaluation

24,000 synthetic events, seed 42; sorted chronologically. First 50% trains XGBoost, next 10% fits calibration, next 20% chooses thresholds, last 20% is held out. Two-hour delayed labels are purged at classifier/calibration/validation cutoffs. Final test labels are retrospective evaluation labels; they are not fed into training. A supplied CSV must include label availability and already matured outcomes.

Baseline: standardized logistic regression. Candidate must meet validation AP >= baseline AP and illustrative cost <= baseline cost. Cost is 1 per false alarm, 50 per missed fraud; these are illustrative units, not INR or estimated business losses. Decline threshold targets <=1% validation FPR, which is not a guarantee on future traffic.

The shipped synthetic holdout has AP about 0.578 versus baseline 0.504, recall about 79.5%, precision about 25.1%, and false-positive rate about 4.44% at the review threshold. Alert rate is about 5.81%; precision among top 100 scores is 55%. These are **model-only** results. Cold-start/late/failure policy and production shadow mode change final decision counts; they are not included in those model metrics.

Run `uv run python scripts/backtest.py --output runtime/new-backtest` for three expanding chronological folds. Fold test windows differ; overlapping training data means results are not independent confidence-interval samples. Read individual fold/channel/reliability results; do not report only the best run.

## Limits and promotion requirements

The generator has no real recipient network, account age, SIM/device binding, geolocation, compromised-device evidence, consent signals, chargebacks or fraud-case provenance. It encodes a small set of known interactions. Adding channel names does not establish coverage of UPI scams, mule rings, AML or RTGS fraud.

Calibration was fitted on synthetic data. Reliability is sparse in high-score bins. PSI/mean drift are descriptive; neither establishes fraud recall. Feedback is selective and may be wrong. Corrections retain history but are not automatically used to retrain.

Before any live decisioning: agree on the target/use case; obtain representative lawful data; validate trusted feature collection; measure mature labels across multiple time and customer cohorts; compare a rule baseline and model; choose business/queue-capacity thresholds; evaluate value recall, false declines and uncertainty; document approval and rollback. The current code intentionally has no production live-decision mode.
