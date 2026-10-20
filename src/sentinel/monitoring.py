"""Distribution diagnostics. PSI is descriptive, not a significance test."""
import numpy as np


def reference_histogram(values):
    quantiles = np.unique(np.quantile(values, np.linspace(0, 1, 11)))
    # Binary and constant features need boundaries too, or every observation
    # lands in one bin and a complete distribution shift becomes invisible.
    if len(quantiles) == 1:
        edges = np.array([quantiles[0] - 0.5, quantiles[0] + 0.5])
    elif len(quantiles) == 2:
        edges = np.array([(quantiles[0] + quantiles[1]) / 2])
    else:
        edges = quantiles[1:-1]
    counts = np.histogram(values, bins=np.r_[-np.inf, edges, np.inf])[0]
    return {"edges": edges.tolist(), "counts": counts.tolist()}


def population_stability(rows, reference):
    results = []
    if len(rows) < 100:
        return results
    for name, spec in reference.items():
        bins = np.r_[-np.inf, spec['edges'], np.inf]
        counts = np.histogram([row[name] for row in rows], bins=bins)[0]
        expected = np.asarray(spec['counts'], dtype=float) + 0.5
        observed = counts.astype(float) + 0.5
        expected /= expected.sum()
        observed /= observed.sum()
        psi = float(np.sum((observed - expected) * np.log(observed / expected)))
        results.append({'feature': name, 'psi': round(psi, 4), 'warning': psi >= 0.2})
    return results
