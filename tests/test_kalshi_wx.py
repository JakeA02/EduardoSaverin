"""Kalshi HIGH/RAIN ticker parsing and Gaussian contract probabilities."""

from datetime import date

from kalshi_wx.horizons import FAR, NEAR, assign_strategies, eligible_for, tag_candidate
from kalshi_wx.markets import (
    dollars_to_prob,
    implied_tmax,
    parse_high_contract,
    parse_rain_contract,
    parse_ticker_date,
    parse_weekend_rain,
    parse_weekly_hot,
)
from kalshi_wx.probability import (
    contract_edge,
    p_consecutive_ge,
    p_day_above,
    p_integer_tmax,
    p_rain,
    p_rain_range,
    p_tmax_greater_than,
    p_tmax_less_than,
    taker_fee,
)
from kalshi_wx.scan import paper_eligible
from kalshi_wx.stations import station_from_rules


def test_parse_ticker_date() -> None:
    assert parse_ticker_date("KXHIGHNY-26SEP03-B83.5") == date(2026, 9, 3)
    assert parse_ticker_date("KXRAIN-26SEP04-NYC") == date(2026, 9, 4)


def test_bracket_b85_5_is_85_86() -> None:
    spec = parse_high_contract("KXHIGHNY-26SEP03-B85.5", "Will the maximum temperature be 85-86°")
    assert spec is not None
    assert spec.kind == "bracket"
    assert spec.lo == 85
    assert spec.hi == 86


def test_less_than_threshold() -> None:
    spec = parse_high_contract(
        "KXHIGHNY-26SEP03-T83",
        "Will the maximum temperature be <83° on Sep 3, 2026?",
    )
    assert spec is not None
    assert spec.kind == "less"
    assert spec.strike == 83


def test_greater_than_threshold() -> None:
    spec = parse_high_contract(
        "KXHIGHNY-26SEP04-T89",
        "Will the maximum temperature be >89° on Sep 4, 2026?",
    )
    assert spec is not None
    assert spec.kind == "greater"
    assert spec.strike == 89


def test_rain_suffix() -> None:
    spec = parse_rain_contract("KXRAIN-26SEP03-CHI")
    assert spec is not None
    assert spec.rain_code == "CHI"
    assert spec.target_date == date(2026, 9, 3)


def test_integer_bracket_probability_peaks_on_mean() -> None:
    p_on = p_integer_tmax(84.0, 1.5, 83, 84)
    p_off = p_integer_tmax(84.0, 1.5, 87, 88)
    assert p_on > 0.4
    assert p_off < 0.05


def test_less_greater_thresholds_match_kalshi_offset() -> None:
    # <83 YES iff integer ≤ 82. A 82.0 mean should be mostly YES.
    assert p_tmax_less_than(82.0, 1.2, 83) > 0.6
    assert p_tmax_greater_than(91.0, 1.2, 89) > 0.6
    assert p_tmax_greater_than(84.0, 1.2, 89) < 0.05


def test_rain_increases_with_qpf() -> None:
    dry = p_rain(0.0, [0.05] * 8, 1.0)
    wet = p_rain(0.4, [0.6] * 8, 1.0)
    assert wet > dry
    assert wet > 0.7
    assert dry < 0.4


def test_edge_buys_yes_when_model_is_above_ask() -> None:
    edge = contract_edge(0.70, yes_bid=0.40, yes_ask=0.45, min_ev=0.05)
    assert edge.side == "yes"
    assert edge.ev > 0.05


def test_edge_buys_no_when_model_is_below_bid() -> None:
    edge = contract_edge(0.20, yes_bid=0.55, yes_ask=0.60, min_ev=0.05)
    assert edge.side == "no"


def test_edge_skips_inside_spread() -> None:
    edge = contract_edge(0.50, yes_bid=0.48, yes_ask=0.52, min_ev=0.05)
    assert edge.side == "skip"


def test_taker_fee_peaks_near_even() -> None:
    assert taker_fee(0.50) > taker_fee(0.10)
    assert abs(taker_fee(0.50) - 0.0175) < 1e-9


def test_dollars_to_prob_treats_zero_as_missing() -> None:
    assert dollars_to_prob("0.0000") is None
    assert dollars_to_prob("0.8200") == 0.82


def test_implied_tmax_from_brackets() -> None:
    from kalshi_wx.markets import HighContract

    a = HighContract("t", "s", date(2026, 9, 3), "bracket", 83, 84, None, 83.5)
    b = HighContract("t", "s", date(2026, 9, 3), "bracket", 85, 86, None, 85.5)
    implied = implied_tmax([(a, 0.8), (b, 0.2)])
    assert implied is not None
    assert 83.5 < implied < 84.5


def test_station_from_cliny_rules() -> None:
    st = station_from_rules(
        "If the maximum temperature recorded at New York City (CLINYC) for Sep 4"
    )
    assert st is not None
    assert st.city == "NYC"
    assert st.icao == "KNYC"


def test_paper_eligible_rejects_same_day_and_locked_books() -> None:
    today = date(2026, 9, 3)
    base = {
        "kind": "high",
        "side": "yes",
        "target_date": "2026-09-04",
        "mid": 0.40,
        "yes_bid": 0.38,
        "yes_ask": 0.42,
        "volume": 200,
        "ev": 0.10,
    }
    assert paper_eligible(base, today=today)
    assert not paper_eligible({**base, "target_date": "2026-09-03"}, today=today)
    assert not paper_eligible({**base, "target_date": "2026-09-05"}, today=today)
    assert not paper_eligible({**base, "mid": 0.99, "yes_bid": 0.98, "yes_ask": 0.99}, today=today)
    assert not paper_eligible({**base, "yes_bid": None}, today=today)
    assert not paper_eligible({**base, "volume": 10}, today=today)


def test_near_and_far_windows_do_not_overlap() -> None:
    today = date(2026, 9, 3)
    near_row = {
        "kind": "high",
        "side": "yes",
        "target_date": "2026-09-04",
        "mid": 0.40,
        "yes_bid": 0.38,
        "yes_ask": 0.42,
        "volume": 200,
        "ev": 0.10,
    }
    far_row = {**near_row, "target_date": "2026-09-08", "volume": 20, "kind": "rain_weekend"}
    assert eligible_for(near_row, NEAR, today=today)
    assert not eligible_for(near_row, FAR, today=today)
    assert eligible_for(far_row, FAR, today=today)
    assert not eligible_for(far_row, NEAR, today=today)
    tagged = tag_candidate({**far_row}, today=today)
    assert tagged["calendar_lead"] == 5
    assert tagged["strategies"] == ["far"]
    assert tagged["paper_eligible"] is False
    assert tagged["paper_eligible_far"] is True


def test_assign_strategies_splits_by_lead() -> None:
    assert assign_strategies(1, "high") == ["near"]
    assert assign_strategies(4, "high") == ["far"]
    assert assign_strategies(0, "high") == []
    assert assign_strategies(11, "rain") == []
    assert assign_strategies(3, "rain_weekend") == ["far"]
    assert assign_strategies(1, "rain_weekend") == []


def test_parse_weekend_rain_uses_title_span() -> None:
    spec = parse_weekend_rain(
        "KXRAINWKND-26SEP05-HOU",
        "Will it rain in Houston between Sep 5 and Sep 6?",
    )
    assert spec is not None
    assert spec.rain_code == "HOU"
    assert spec.start_date == date(2026, 9, 5)
    assert spec.end_date == date(2026, 9, 6)


def test_parse_weekly_hot_strike() -> None:
    spec = parse_weekly_hot("KXAVGTKPHX-26SEP06-6")
    assert spec is not None
    assert spec.series == "KXAVGTKPHX"
    assert spec.week_ending == date(2026, 9, 6)
    assert spec.n_days == 6
    assert parse_weekly_hot("KXHIGHNY-26SEP04-T89") is None


def test_p_consecutive_ge_certain_and_impossible() -> None:
    assert p_consecutive_ge([1.0, 1.0, 1.0], 3) > 0.98
    assert p_consecutive_ge([1.0, 0.0, 1.0], 2) < 0.02
    assert p_consecutive_ge([0.5, 0.5], 3) == 0.0
    assert p_consecutive_ge([0.8] * 7, 2) > p_consecutive_ge([0.8] * 7, 6)


def test_p_rain_range_is_union() -> None:
    dry = (0.0, [0.05] * 4, 2.0)
    wet = (0.5, [0.7] * 4, 3.0)
    assert p_rain_range([dry, dry]) < p_rain_range([dry, wet])
    assert p_rain_range([wet, wet]) > 0.8


def test_p_day_above_90() -> None:
    assert p_day_above(96.0, 2.0, 90.0) > 0.9
    assert p_day_above(80.0, 2.0, 90.0) < 0.1
