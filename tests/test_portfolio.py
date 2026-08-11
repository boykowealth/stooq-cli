"""Weighting methodologies, checked by their defining properties."""

import numpy as np
import pandas as pd
import pytest

from stooq_cli import portfolio


def returns_frame(vols=(0.01, 0.02, 0.04), n=500, seed=0, corr=0.0) -> pd.DataFrame:
    """Synthetic daily returns with known volatilities."""
    rng = np.random.default_rng(seed)
    common = rng.normal(0, 1, n)
    cols = {}
    for i, v in enumerate(vols):
        idiosyncratic = rng.normal(0, 1, n)
        mixed = np.sqrt(corr) * common + np.sqrt(1 - corr) * idiosyncratic
        cols[f"A{i}"] = mixed * v
    return pd.DataFrame(cols, index=pd.date_range("2024-01-01", periods=n, freq="B"))


ALL_METHODS = portfolio.METHODS


@pytest.mark.parametrize("method", ALL_METHODS)
def test_weights_are_valid_for_every_method(method):
    rets = returns_frame()
    scores = pd.Series({"A0": 0.3, "A1": 0.1, "A2": 0.2})
    result = portfolio.compute_weights(method, rets, scores=scores, long_only=True)
    assert len(result.weights) == 3
    assert result.weights.notna().all()
    assert (result.weights >= -1e-9).all(), "long-only must not go short"
    assert result.weights.sum() == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("method", ALL_METHODS)
def test_single_asset_gets_everything(method):
    rets = returns_frame(vols=(0.02,))
    result = portfolio.compute_weights(method, rets, scores=pd.Series({"A0": 0.5}))
    assert result.weights.iloc[0] == pytest.approx(1.0)


def test_equal_weight_is_equal():
    rets = returns_frame()
    w = portfolio.compute_weights("equal", rets).weights
    assert w.std() == pytest.approx(0.0, abs=1e-12)


def test_inverse_vol_favours_the_quiet_asset():
    """A0 has half A1's vol and a quarter of A2's, so it must get the most."""
    rets = returns_frame(vols=(0.01, 0.02, 0.04))
    w = portfolio.compute_weights("inverse_vol", rets).weights
    assert w["A0"] > w["A1"] > w["A2"]
    # Weights should be roughly proportional to 1/vol.
    assert w["A0"] / w["A2"] == pytest.approx(4.0, rel=0.25)


def test_min_variance_beats_equal_weight_on_variance():
    """The defining property: nothing may have lower forecast variance."""
    rets = returns_frame(vols=(0.01, 0.02, 0.04), corr=0.3)
    cov = portfolio.shrunk_covariance(rets)
    mv = portfolio.compute_weights("min_variance", rets).weights
    eq = portfolio.compute_weights("equal", rets).weights
    assert portfolio.portfolio_vol(mv, cov) <= portfolio.portfolio_vol(eq, cov) + 1e-9


def test_risk_parity_equalizes_risk_contributions():
    rets = returns_frame(vols=(0.01, 0.02, 0.04), corr=0.2)
    cov = portfolio.shrunk_covariance(rets)
    w = portfolio.compute_weights("risk_parity", rets).weights
    contrib = portfolio.risk_contributions(w, cov)
    assert contrib.max() - contrib.min() < 0.02, f"contributions not equal: {contrib.to_dict()}"


def test_max_sharpe_beats_equal_weight_on_sharpe():
    rng = np.random.default_rng(3)
    n = 600
    # A1 is the standout: same vol as A0 but a much better drift.
    data = {
        "A0": rng.normal(0.0002, 0.01, n),
        "A1": rng.normal(0.0012, 0.01, n),
        "A2": rng.normal(0.0001, 0.02, n),
    }
    rets = pd.DataFrame(data, index=pd.date_range("2024-01-01", periods=n, freq="B"))
    ms = portfolio.compute_weights("max_sharpe", rets)
    eq = portfolio.compute_weights("equal", rets)
    assert ms.diagnostics["sharpe"] >= eq.diagnostics["sharpe"] - 1e-9
    assert ms.weights["A1"] > ms.weights["A0"]


def test_momentum_weighting_follows_the_scores():
    rets = returns_frame()
    scores = pd.Series({"A0": 0.6, "A1": 0.3, "A2": 0.1})
    w = portfolio.compute_weights("momentum", rets, scores=scores).weights
    assert w["A0"] > w["A1"] > w["A2"]
    assert w["A0"] == pytest.approx(0.6, abs=1e-6)


def test_momentum_weighting_drops_negatives_when_long_only():
    rets = returns_frame()
    scores = pd.Series({"A0": 0.5, "A1": -0.4, "A2": 0.5})
    w = portfolio.compute_weights("momentum", rets, scores=scores, long_only=True).weights
    assert w["A1"] == pytest.approx(0.0)
    assert w["A0"] == pytest.approx(0.5, abs=1e-6)


def test_shorts_allowed_can_produce_negative_weights():
    rets = returns_frame()
    scores = pd.Series({"A0": 0.5, "A1": -0.4, "A2": 0.5})
    w = portfolio.compute_weights("momentum", rets, scores=scores, long_only=False).weights
    assert w["A1"] < 0


def test_vol_target_overlay_scales_down_and_holds_cash():
    rets = returns_frame(vols=(0.03, 0.03, 0.03))  # high vol, ~48% annualized
    result = portfolio.compute_weights(
        "equal", rets, overlay="vol_target", vol_target=0.10
    )
    assert result.gross < 1.0
    assert result.cash > 0.0
    assert result.diagnostics["vol"] == pytest.approx(0.10, rel=0.05)


def test_overlay_never_levers_above_fully_invested():
    """A very quiet portfolio must not be geared up to reach the target."""
    rets = returns_frame(vols=(0.001, 0.001, 0.001))
    result = portfolio.compute_weights(
        "equal", rets, overlay="vol_target", vol_target=0.50
    )
    assert result.gross == pytest.approx(1.0)
    assert result.cash == pytest.approx(0.0)


def test_var_overlay_hits_its_budget():
    rets = returns_frame(vols=(0.03, 0.03, 0.03))
    result = portfolio.compute_weights(
        "equal", rets, overlay="var_target", var_target=0.01
    )
    assert result.diagnostics["var_95_1d"] == pytest.approx(0.01, rel=0.05)


def test_max_weight_cap_is_respected():
    rets = returns_frame(vols=(0.005, 0.05, 0.05))
    w = portfolio.compute_weights("min_variance", rets, max_weight=0.5).weights
    assert w.max() <= 0.5 + 1e-6


def test_degenerate_input_does_not_raise():
    """Identical columns make covariance singular; shrinkage must save us."""
    n = 60
    same = np.random.default_rng(1).normal(0, 0.01, n)
    rets = pd.DataFrame(
        {"A": same, "B": same, "C": same},
        index=pd.date_range("2024-01-01", periods=n, freq="B"),
    )
    for method in ALL_METHODS:
        result = portfolio.compute_weights(method, rets, scores=pd.Series(dtype=float))
        assert result.weights.notna().all()
        assert result.weights.sum() == pytest.approx(1.0, abs=1e-6)


def test_empty_input_is_handled():
    result = portfolio.compute_weights("min_variance", pd.DataFrame())
    assert result.weights.empty
    assert "no symbols" in result.note


def test_unknown_method_falls_back_and_says_so():
    rets = returns_frame()
    result = portfolio.compute_weights("nonsense", rets)
    assert result.weights.sum() == pytest.approx(1.0)
    assert "unknown method" in result.note
