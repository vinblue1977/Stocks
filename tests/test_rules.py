import pytest

from portfolio import rules


def test_validate_rule_normalizes():
    rule = rules.validate_rule({"type": "drawdown_below", "ticker": " vti ", "params": {"pct": "10"}})
    assert rule == {"type": "drawdown_below", "ticker": "VTI", "params": {"pct": 10.0}, "enabled": True}


@pytest.mark.parametrize(
    "body, message",
    [
        ({"type": "nope", "ticker": "VTI"}, "type must be one of"),
        ({"type": "rsi_below", "ticker": "not a ticker!"}, "ticker"),
        ({"type": "rsi_below", "ticker": "VTI", "params": {}}, "params.threshold is required"),
        ({"type": "rsi_below", "ticker": "VTI", "params": {"threshold": 500}}, "between"),
        ({"type": "rsi_below", "ticker": "VTI", "params": {"threshold": "abc"}}, "must be a number"),
        ({"type": "rsi_below", "ticker": "VTI", "params": ["x"]}, "params must be an object"),
    ],
)
def test_validate_rule_rejects(body, message):
    with pytest.raises(rules.RuleValidationError, match=message):
        rules.validate_rule(body)


SIGNAL = {
    "price": 27.9,
    "sma50": 28.34,
    "sma200": 28.5,
    "sma50_prev": 28.6,
    "sma200_prev": 28.5,
    "rsi14": 41.0,
    "drawdown_from_high": -0.089,
    "sentiment_7d": -0.27,
    "smas": {"20": 28.0},
}


def rule(rule_type, **params):
    return {"type": rule_type, "ticker": "SCHD", "params": params}


def test_price_below_sma_uses_stored_and_extra_windows():
    assert rules.evaluate(rule("price_below_sma", window=50), SIGNAL, None)[0] is True
    assert rules.evaluate(rule("price_below_sma", window=20), SIGNAL, None)[0] is True
    assert rules.evaluate(rule("price_above_sma", window=50), SIGNAL, None)[0] is False
    met, detail = rules.evaluate(rule("price_below_sma", window=100), SIGNAL, None)
    assert met is False and "Not enough history" in detail


def test_death_cross_detects_crossing_only():
    assert rules.evaluate(rule("death_cross"), SIGNAL, None)[0] is True
    assert rules.evaluate(rule("golden_cross"), SIGNAL, None)[0] is False
    steady = {**SIGNAL, "sma50_prev": 28.0}  # already below yesterday: no new cross
    assert rules.evaluate(rule("death_cross"), steady, None)[0] is False


def test_drawdown_rsi_weight_sentiment():
    assert rules.evaluate(rule("drawdown_below", pct=8), SIGNAL, None)[0] is True
    assert rules.evaluate(rule("drawdown_below", pct=10), SIGNAL, None)[0] is False
    assert rules.evaluate(rule("rsi_below", threshold=45), SIGNAL, None)[0] is True
    assert rules.evaluate(rule("rsi_above", threshold=70), SIGNAL, None)[0] is False
    assert rules.evaluate(rule("weight_above", pct=40), SIGNAL, 0.447)[0] is True
    assert rules.evaluate(rule("weight_above", pct=40), SIGNAL, None)[0] is False
    assert rules.evaluate(rule("sentiment_below", threshold=-0.2), SIGNAL, None)[0] is True


def test_needed_sma_windows():
    found = rules.needed_sma_windows([rule("price_below_sma", window=20), rule("price_above_sma", window=50)])
    assert found == {20}
