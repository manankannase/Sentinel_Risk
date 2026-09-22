# Model card

## Model

Gradient-boosted decision trees trained with XGBoost and stored only in XGBoost native JSON format. Serving verifies the SHA-256 digest in artifacts/manifest.json before loading. It does not deserialize Python pickles.

## Intended use

Student demonstration of transaction risk scoring. Output is a simulated recommendation: APPROVE, REVIEW, or DECLINE. It cannot authorize or deny a real payment.

## Data and evaluation

The shipped model uses a reproducible synthetic transaction generator with noisy labels. Events are sorted by event time. Training ends at 60% of the timeline, validation at 80%, and the last 20% is held out. Rows whose labels were unavailable at their split cutoff are removed. Thresholds are chosen on validation only. Exact numbers and assumptions are in artifacts/metrics.json.

Features use prior customer transactions only: log amount, amount relative to historical mean, 1-hour/24-hour count and volume, device/country novelty, time of day, and channel. Labels, raw identifier encodings, future events, and same-timestamp events are excluded.

## Limits

- Synthetic metrics do not establish real-world fraud performance.
- False positives can harm legitimate users.
- Feedback is delayed and selective.
- Device and country novelty can correlate with travel or operational changes.
- Reason codes are observable rules, not causal model explanations.
- Feature mean-shift monitoring is exploratory, not a statistical drift guarantee.
