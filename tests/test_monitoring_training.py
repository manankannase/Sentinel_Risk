import json

import numpy as np
import pytest

from sentinel.features import FEATURE_NAMES
from sentinel.ml import RiskModel
from sentinel.monitoring import population_stability, reference_histogram
from sentinel.train import calibrate, evaluate, prepare, synthetic_rows, train


def test_train_serve_calibration_parity(artifacts):
    model=RiskModel(artifacts)
    tx=next(synthetic_rows(1))
    from sentinel.features import build_features
    features=build_features(tx,[])
    raw=model.model.inplace_predict(np.array([[features[k] for k in FEATURE_NAMES]],dtype=np.float32))[0]
    c=model.manifest['calibration']
    assert model.predict(features)==pytest.approx(calibrate([raw],c['slope'],c['intercept'])[0])


def test_psI_detects_distribution_change():
    reference={'amount':{'edges':[5], 'counts':[100,100]}}
    equal=[{'amount':x} for x in [0,10]*100]
    shifted=[{'amount':10} for _ in range(200)]
    assert population_stability(equal,reference)[0]['psi']==0
    assert population_stability(shifted,reference)[0]['warning']
    assert population_stability(equal[:10],reference)==[]


@pytest.mark.parametrize('baseline', [[0]*200, [0,1]*100])
def test_discrete_feature_drift_is_visible(baseline):
    reference = {'flag': reference_histogram(baseline)}
    equal = [{'flag': value} for value in baseline]
    shifted = [{'flag': 1} for _ in baseline]
    assert population_stability(equal, reference)[0]['psi'] == 0
    assert population_stability(shifted, reference)[0]['warning']


def test_duplicate_ids_and_unavailable_labels_rejected():
    rows=list(synthetic_rows(10))
    with pytest.raises(ValueError,match='Duplicate'):
        prepare(rows+[rows[0]])
    rows[0]['label_available_at']='2000-01-01T00:00:00Z'
    with pytest.raises(ValueError,match='label'):
        prepare(rows)


def test_existing_model_directory_never_overwritten(artifacts):
    before=(artifacts/'manifest.json').read_bytes()
    with pytest.raises(ValueError,match='immutable'):
        train(artifacts,[])
    assert (artifacts/'manifest.json').read_bytes()==before


def test_small_calibrated_candidate_fails_promotion(tmp_path):
    # Regression: don't weaken validation just to make small synthetic fixtures pass.
    with pytest.raises(ValueError,match='promotion'):
        train(tmp_path/'rejected',list(synthetic_rows(8000,8)),seed=8)
    assert not (tmp_path/'rejected/model.json').exists()


def test_reliability_and_capacity_metrics():
    result=evaluate(np.array([0,1,1,0]),np.array([0,.5,1,.2]),.4)
    assert result['alert_rate']==.5
    assert sum(b['count'] for b in result['reliability'])==4
    assert result['recall']==1


def test_shipped_artifact_is_inr_and_calibrated(artifacts):
    manifest=json.loads((artifacts/'manifest.json').read_text())
    assert manifest['currency']=='INR'
    assert manifest['feature_timezone']=='Asia/Kolkata'
    assert manifest['calibrated']
