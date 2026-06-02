"""Conservative AlphaScoreEngine invariance baseline (pre-discovery).

Loads ``tests/fixtures/alpha_conservative_fixture.json`` — synthetic rows only,
no live vendor data. Used to prove later gated changes do not alter default scoring.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tyche.strategy.alpha_engine import AlphaScoreEngine

_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "alpha_conservative_fixture.json"


def _load_fixture() -> dict:
    with _FIXTURE_PATH.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def fixture() -> dict:
    return _load_fixture()


@pytest.fixture(scope="module")
def engine(fixture: dict) -> AlphaScoreEngine:
    cfg = fixture["engine"]
    return AlphaScoreEngine(
        strong_buy_threshold=cfg["strong_buy_threshold"],
        buy_threshold=cfg["buy_threshold"],
        watch_threshold=cfg["watch_threshold"],
    )


@pytest.mark.parametrize("case_name", [c["name"] for c in _load_fixture()["cases"]])
def test_conservative_fixture_case(case_name: str, fixture: dict, engine: AlphaScoreEngine) -> None:
    case = next(c for c in fixture["cases"] if c["name"] == case_name)
    row = {"ticker": "SYN", **case["features"]}
    df = pd.DataFrame([row])

    probs = None
    if case.get("breakout_probs"):
        probs = {k: np.array([v]) for k, v in case["breakout_probs"].items()}

    signal = engine.score_from_features(df, breakout_probs=probs)[0]
    expected = case["expected"]

    assert signal.alpha_score == expected["alpha_score"]
    assert signal.signal == expected["signal"]
    assert signal.regime == expected["regime"]
    assert signal.horizon == expected["horizon"]
    assert signal.overextension_penalty == expected["overextension_penalty"]
    assert signal.demand_multiplier == expected["demand_multiplier"]
    assert signal.demand.net == expected["demand_net"]
