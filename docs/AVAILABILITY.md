# Keeping the project reachable for three years

## What was broken

At audit time the repository homepage was null, `has_pages` was false, and GitHub Pages returned 404. The README linked only `127.0.0.1`/`localhost`. Those addresses refer to the visitor's own computer, so they cannot serve as a public deployment link. No separate broken public URL was supplied.

## Durable public demonstration

Live demo: [Sentinel Risk](https://manankannase.github.io/Sentinel_Risk/). Publication succeeded; all eight assets were verified over HTTPS against build checksums.

The public build contains the synthetic INR XGBoost trees, calibration, sample transactions and browser UI. It computes scores in the browser, requires no key, sends no customer input to a backend, and stores changes only in memory. It is explicitly labeled as a browser simulation. Node checks compare browser features/scores against Python and exercise the demo API adapter. The publication branch contains only public build assets and its Pages workflow.

This removes a sleeping API, expiring database trial, local tunnel and forgotten server process from the demo's availability path. It does not promise that a provider/account/repository will exist forever. [GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) still apply; this educational page must not process sensitive transactions or become a commercial payment service.

## Ownership and renewal

- Keep the repository and GitHub account active, protected by MFA and recovery codes. Avoid renaming/deleting the repository without preserving redirects.
- Keep `gh-pages` as a prebuilt recovery copy. The source workflow can regenerate the build on a clean machine.
- Download a release/demo archive and checksum file to a second location. An archive is a recovery mechanism, not a hosted substitute.
- For independence from a GitHub username, register a domain you control, enable auto-renewal, maintain payment/contact details and document a second trusted maintainer. Domain purchase requires an owner and budget.
- A paid backend should use managed PostgreSQL/Redis, image digests, backups/PITR, TLS and an operator who handles incidents and renewals. Free plans and trials are not a three-year service commitment.

## Maintenance cadence

Weekly: review dependency alerts and failed CI; check public URL and scoring demo; verify backup completion for any backend.

Monthly: update tested dependencies/images, review error/latency/backlog graphs, check budget/storage and domain billing, verify a new browser still loads the demo. A scheduled GitHub workflow can itself be disabled after inactivity; do not treat it as the sole external uptime monitor.

Quarterly: restore a backup into an isolated environment; test credential rotation and rollback; verify domain/account recovery; rerun representative shadow evaluation and incident drills.

Annually: renew ownership/budget, review hosting/provider terms, archive the current static build and compatible runtime versions, test moving the demo to a second static host.

## Recovery

Static demo: rebuild with `uv run python scripts/build_demo.py`; run both Node checks; upload `dist/demo/` to an HTTPS static host. Keep the path relative; no API origin is embedded.

Backend: restore database/PITR to a new instance, provision CA files and role-specific credentials, deploy the exact approved image and model/manifest pins, run migrations with owner credentials, run readiness and isolated smoke tests, then switch DNS/load-balancer traffic. Never overwrite a live database as a restore drill.
