#!/usr/bin/env python3
"""
Binance 15m Price Action Session Fetcher
========================================
Pulls 15-minute OHLC data from the Binance public API for a fixed 5:00 PM
to 5:00 PM EST/EDT session, outputs a machine-readable manual labeling
template, and renders a simple candlestick chart.

Usage
-----
  python v2.0_jsonschema.py "1/20/26 (EST)"
  python v2.0_jsonschema.py "1/20/26 EDT" --symbol ETHUSDT
  python v2.0_jsonschema.py "1/20/26 (EST)" --output ctx.json --no-chart

Dependencies
------------
  pip install requests pandas plotly
"""

import json
import re
import sys
import argparse
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import plotly.graph_objects as go


# JSON Output â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

BINANCE_URL = "https://fapi.binance.com/fapi/v1/klines"
GRANULARITY = 900
MAX_CANDLES = 1000
TIMEFRAME = "15m"
DEFAULT_SYMBOL = "BTCUSDT"
SESSION_START_HOUR = 17
SESSION_END_HOUR = 17
SCHEMA_NAME = "PriceActionOnlySession15m"
SCHEMA_VERSION = 18
SCHEMA_CREATED_AT = "2026-04-27"
SCHEMA_RELEASE = "price_action_only_v1"
RAW_PRICE_OUTCOME_WINDOWS = (8, 16, 24, 48)

TZ_OFFSETS: dict[str, int] = {"EST": -5, "EDT": -4}


def _tz(abbr: str) -> timezone:
    key = abbr.strip("()").upper()
    if key not in TZ_OFFSETS:
        raise ValueError(f"Unknown timezone abbreviation: {abbr!r}. Supported: {sorted(TZ_OFFSETS)}")
    return timezone(timedelta(hours=TZ_OFFSETS[key]))


def _parse_session_date(date_str: str, tz_str: str) -> tuple[datetime, datetime]:
    m, d, y = date_str.split("/")
    if len(y) == 2:
        y = "20" + y
    session_date = datetime.strptime(f"{m}/{d}/{y}", "%m/%d/%Y")
    tz = _tz(tz_str)
    start = session_date.replace(hour=SESSION_START_HOUR, minute=0, second=0, microsecond=0, tzinfo=tz)
    end = (session_date + timedelta(days=1)).replace(hour=SESSION_END_HOUR, minute=0, second=0, microsecond=0, tzinfo=tz)
    return start, end


def parse_session_range(raw: str) -> tuple[datetime, datetime, str, str]:
    date_pattern = r"\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})"
    tz_pattern = r"(?:\()?(EST|EDT)(?:\))?"
    match = re.match(rf"^\s*({date_pattern})\s*(?:[;,]\s*)?{tz_pattern}\s*$", raw.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Cannot parse session: {raw!r}\nExpected format: '1/20/26 (EST)' or '1/20/26 EDT'")
    date_str, tz_str = match.groups()
    start, end = _parse_session_date(date_str, tz_str)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), start.strftime("%Y-%m-%d"), tz_str.upper()


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list:
    all_rows: list = []
    chunk_ms = MAX_CANDLES * GRANULARITY * 1000
    cur_start_ms = start_ms
    while cur_start_ms < end_ms:
        cur_end_ms = min(cur_start_ms + chunk_ms, end_ms)
        resp = requests.get(
            BINANCE_URL,
            params={
                "symbol": symbol,
                "interval": TIMEFRAME,
                "startTime": cur_start_ms,
                "endTime": cur_end_ms - 1,
                "limit": MAX_CANDLES,
            },
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows or isinstance(rows, dict):
            break
        all_rows.extend(rows)
        cur_start_ms = rows[-1][0] + GRANULARITY * 1000
        if len(rows) < MAX_CANDLES:
            break

    seen: set[int] = set()
    deduped = []
    for row in all_rows:
        if row[0] not in seen:
            seen.add(row[0])
            deduped.append(row)
    return sorted(deduped, key=lambda row: row[0])


def klines_to_df(rows: list) -> pd.DataFrame:
    cols = [
        "open_time_ms", "open", "high", "low", "close", "volume",
        "close_time_ms", "quote_asset_volume", "num_trades",
        "taker_buy_base_vol", "taker_buy_quote_vol", "ignore",
    ]
    df = pd.DataFrame(rows, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time_ms"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    return df[["open_time", "open", "high", "low", "close"]]


def _r(v, decimals: int):
    """Round a value, returning None if NaN or infinite."""
    if v is None or pd.isna(v):
        return None
    return round(float(v), decimals)


def _empty_conversion() -> dict:
    return {"converted": False, "candle_idx": None, "auction_id": None}


def _empty_candle_range() -> dict:
    return {"start_idx": None, "confirmation_candle_idx": None, "end_idx": None}


def _base_level(level_id: str, role: str | None = None) -> dict:
    level = {
        "id": level_id,
        "price": None,
        "candle_idx_range": {"start_idx": None, "end_idx": None},
        "holds_at_session_end": False,
    }
    if role is not None:
        level["level_role"] = role
    return level


def _empty_support_level(level_id: str) -> dict:
    level = _base_level(level_id, "support")
    level.update(
        {
            "label_confidence": None,
            "confidence_reason_codes": [],
            "weakness_reason_codes": [],
            "level_converted_to_macro_auction_lower_bound": _empty_conversion(),
            "level_converted_to_micro_auction_lower_bound": _empty_conversion(),
        }
    )
    return level


def _empty_resistance_level(level_id: str) -> dict:
    level = _base_level(level_id, "resistance")
    level.update(
        {
            "label_confidence": None,
            "confidence_reason_codes": [],
            "weakness_reason_codes": [],
            "level_converted_to_macro_auction_upper_bound": _empty_conversion(),
            "level_converted_to_micro_auction_upper_bound": _empty_conversion(),
        }
    )
    return level


def _empty_micro_level(level_id: str) -> dict:
    role = "micro_support" if level_id.startswith("micro_support_") else "micro_resistance"
    level = _base_level(level_id, role)
    level.update(
        {
            "label_confidence": None,
            "confidence_reason_codes": [],
            "weakness_reason_codes": [],
        }
    )
    return level


def _empty_level(level_id: str, role: str) -> dict:
    if role == "support":
        return _empty_support_level(level_id)
    if role == "resistance":
        return _empty_resistance_level(level_id)
    return _empty_micro_level(level_id)


def _empty_support_resistance_negative_example(example_id: str) -> dict:
    return {
        "id": example_id,
        "candidate_role": None,
        "candidate_price": None,
        "candle_idx_range": {"start_idx": None, "end_idx": None},
        "rejected_as_valid_level": True,
        "rejection_reason_codes": [],
    }


def _empty_micro_support_resistance_negative_example(example_id: str) -> dict:
    return {
        "id": example_id,
        "candidate_role": None,
        "candidate_price": None,
        "candle_idx_range": {"start_idx": None, "end_idx": None},
        "rejected_as_valid_micro_level": True,
        "rejection_reason_codes": [],
    }


def _empty_raw_price_outcome_window() -> dict:
    return {
        "outcome_measured": False,
        "bars_measured": None,
        "lookahead_end_idx": None,
        "max_favorable_excursion_pct": None,
        "max_adverse_excursion_pct": None,
        "continuation_occurred": False,
        "invalidation_occurred": False,
        "structure_reclaimed": False,
        "bars_until_max_favorable": None,
        "bars_until_max_adverse": None,
    }


def _empty_raw_price_outcome() -> dict:
    return {
        "standard_windows": {
            str(window): _empty_raw_price_outcome_window()
            for window in RAW_PRICE_OUTCOME_WINDOWS
        }
    }


def _empty_support_resistance_borderline_example(example_id: str) -> dict:
    return {
        "id": example_id,
        "candidate_role": None,
        "candidate_price": None,
        "candle_idx_range": {"start_idx": None, "end_idx": None},
        "borderline_as_valid_level": True,
        "label_confidence": "borderline",
        "supporting_reason_codes": [],
        "weakness_reason_codes": [],
    }


def _empty_event_outcome_label(event_id: str) -> dict:
    return {
        "id": event_id,
        "event_type": None,
        "event_candle_idx": None,
        "expected_direction": None,
        "referenced_structure": {
            "structure_type": None,
            "structure_id": None,
            "structure_role": None,
            "structure_price": None,
        },
        "context_refs": {
            "micro_trend_id": None,
            "auction_id": None,
            "confluence_ids": [],
        },
        "human_interpretation": {
            "read": None,
            "confidence": None,
            "reason_codes": [],
            "counterevidence_codes": [],
        },
        "raw_price_outcome": _empty_raw_price_outcome(),
    }


def _empty_micro_trend(trend_id: str) -> dict:
    return {
        "id": trend_id,
        "trend": None,
        "candle_idx_range": {"start_idx": None, "end_idx": None},
        "accelerated": False,
        "acceleration_candle_idx_range": {"start_idx": None, "end_idx": None},
        "trend_break_candle_idx": None,
        "trend_reclaimed_after_break": False,
        "trend_reclaim_candle_idx": None,
        "trend_break_confirmation_candle_idx": None,
    }


def _relative_sequence(prev: dict | None, row: dict) -> dict | None:
    if prev is None:
        return None
    return {
        "compared_to_idx": prev["idx"],
        "high_relation": "higher_high" if row["high"] > prev["high"] else "lower_high" if row["high"] < prev["high"] else "equal_high",
        "low_relation": "higher_low" if row["low"] > prev["low"] else "lower_low" if row["low"] < prev["low"] else "equal_low",
        "close_relation": "higher_close" if row["close"] > prev["close"] else "lower_close" if row["close"] < prev["close"] else "equal_close",
        "open_relation": "higher_open" if row["open"] > prev["open"] else "lower_open" if row["open"] < prev["open"] else "equal_open",
        "is_higher_high_higher_low": row["high"] > prev["high"] and row["low"] > prev["low"],
        "is_lower_high_lower_low": row["high"] < prev["high"] and row["low"] < prev["low"],
        "is_inside_candle": row["high"] <= prev["high"] and row["low"] >= prev["low"],
        "is_outside_candle": row["high"] >= prev["high"] and row["low"] <= prev["low"],
    }


def _auto_sequence_type(prev: dict, row: dict) -> str | None:
    if row["high"] > prev["high"] and row["low"] > prev["low"]:
        return "higher_high_higher_low"
    if row["high"] < prev["high"] and row["low"] < prev["low"]:
        return "lower_high_lower_low"
    return None


def _auto_price_action_sequences(candle_rows: list[dict]) -> list[dict]:
    sequences = []
    active_type = None
    start_idx = None
    end_idx = None

    for i in range(1, len(candle_rows)):
        sequence_type = _auto_sequence_type(candle_rows[i - 1], candle_rows[i])
        if sequence_type is None:
            if active_type is not None:
                sequences.append((active_type, start_idx, end_idx))
            active_type = None
            start_idx = None
            end_idx = None
            continue

        if sequence_type == active_type and end_idx == i - 1:
            end_idx = i
        else:
            if active_type is not None:
                sequences.append((active_type, start_idx, end_idx))
            active_type = sequence_type
            start_idx = i - 1
            end_idx = i

    if active_type is not None:
        sequences.append((active_type, start_idx, end_idx))

    filtered_sequences = [
        (sequence_type, start_idx, end_idx)
        for sequence_type, start_idx, end_idx in sequences
        if end_idx - start_idx + 1 >= 4
    ]

    return [
        {
            "id": f"sequence_{i}",
            "sequence_type": sequence_type,
            "candle_idx_range": {"start_idx": start_idx, "end_idx": end_idx},
            "length": end_idx - start_idx + 1,
        }
        for i, (sequence_type, start_idx, end_idx) in enumerate(filtered_sequences, start=1)
    ]


def _auto_micro_range_engulfings(candle_rows: list[dict]) -> list[dict]:
    patterns = []
    for idx in range(3, len(candle_rows)):
        current = candle_rows[idx]
        prior_three = candle_rows[idx - 3:idx]
        prior_opens = [row["open"] for row in prior_three]
        prior_highs = [row["high"] for row in prior_three]
        prior_lows = [row["low"] for row in prior_three]

        if all(current["open"] <= open_price for open_price in prior_opens) and current["close"] > max(prior_highs):
            classification = "bullish_micro_range_engulfing"
            directional_implication = "buyside"
        elif all(current["open"] >= open_price for open_price in prior_opens) and current["close"] < min(prior_lows):
            classification = "bearish_micro_range_engulfing"
            directional_implication = "sellside"
        else:
            continue

        patterns.append(
            {
                "id": f"micro_range_engulfing_{len(patterns) + 1}",
                "classification": classification,
                "candle_idx_range": {"start_idx": idx - 3, "end_idx": idx},
                "candles_involved": [idx - 3, idx - 2, idx - 1, idx],
                "directional_implication": directional_implication,
                "auto_detected": True,
            }
        )
    return patterns


def _candle_classification(row: dict) -> dict:
    body = abs(row["close"] - row["open"])
    return {
        "body_size": _r(body, 2),
    }


def _last_24h_range(df_24h: pd.DataFrame) -> dict:
    if df_24h.empty:
        return {"lookback_hours": 24, "high": None, "low": None, "range_points": None, "range_percent": None}
    high = float(df_24h["high"].max())
    low = float(df_24h["low"].min())
    return {
        "lookback_hours": 24,
        "high": _r(high, 2),
        "low": _r(low, 2),
        "range_points": _r(high - low, 2),
        "range_percent": _r(((high - low) / low) * 100, 4) if low else None,
    }


def _schema_metadata(generated_at: datetime) -> dict:
    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "schema_release": SCHEMA_RELEASE,
        "created_at": SCHEMA_CREATED_AT,
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "intended_marketplace_use": "AI ingestion for independent 5PM-to-5PM OHLC price-action labeling and review.",
        "raw_price_outcome_workflow": {
            "population_method": "autopopulated_by_helper_script",
            "helper_script": "populate_raw_price_outcomes.py",
            "output": "completed JSON copy with event_outcome_labels.raw_price_outcome filled after manual event labeling",
        },
        "evidence_boundary": {
            "included_evidence": ["ohlc", "manual_price_action_labels", "auto_ohlc_relationships"],
            "excluded_evidence": [
                "volume",
                "volume_delta",
                "cvd",
                "vwap",
                "open_interest",
                "funding_rate",
                "order_flow",
                "technical_indicators",
                "cross_session_context",
            ],
            "sample_independence": True,
        },
    }


def _required_sections() -> dict:
    return {
        "session_metadata": {"required": True, "top_level_keys": ["session_metadata"]},
        "instrument_metadata": {"required": True, "top_level_keys": ["instrument_metadata"]},
        "objective_price_action_fields": {
            "required": True,
            "top_level_keys": ["objective_price_action_fields"],
        },
        "subjective_market_structure_fields": {
            "required": True,
            "top_level_keys": ["subjective_market_structure_fields"],
        },
        "structural_events": {"required": True, "top_level_keys": ["structural_events"]},
        "setup_tags": {"required": True, "top_level_keys": ["setup_tags"]},
        "data_quality": {"required": True, "top_level_keys": ["data_quality"]},
        "validation_rules": {"required": True, "top_level_keys": ["validation_rules"]},
    }


def _data_quality(df: pd.DataFrame, start_utc: datetime, end_utc: datetime) -> dict:
    expected_candles = int((end_utc - start_utc).total_seconds() // GRANULARITY)
    actual_candles = len(df)
    return {
        "expected_candles": expected_candles,
        "actual_candles": actual_candles,
        "missing_candles": max(expected_candles - actual_candles, 0),
        "is_complete": actual_candles == expected_candles,
        "timeframe_seconds": GRANULARITY,
        "ohlc_fields_present": ["open", "high", "low", "close"],
    }


def _validation_rules() -> dict:
    return {
        "required_top_level_sections": list(_required_sections().keys()),
        "session_must_be_24_hours": True,
        "expected_candle_count": 96,
        "timezone_values_allowed": ["EST", "EDT"],
        "null_means_unlabeled": True,
        "manual_labels_must_reference_existing_ids": True,
    }


def _allowed_values() -> dict:
    return {
        "auto_price_action_sequences.sequence_type": [
            "higher_high_higher_low",
            "lower_high_lower_low",
        ],
        "micro_trends.trend": ["micro_buyside", "micro_sellside", "micro_ranging"],
        "micro_candlestick_patterns.classification": [
            "bullish_engulfing",
            "bearish_engulfing",
            "morning_star",
            "evening_star",
            "rejection_wick",
            "inside_bar",
            "outside_bar",
            "bullish_micro_range_engulfing",
            "bearish_micro_range_engulfing",
        ],
        "micro_candlestick_patterns.directional_implication": ["buyside", "sellside", "neutral"],
        "price_action_levels.level_role": ["support", "resistance", "micro_support", "micro_resistance"],
        "price_action_levels.label_confidence": ["high", "medium", "low"],
        "price_action_levels.confidence_reason_codes": [
            "multiple_clean_reactions",
            "formed_after_buyside_impulse",
            "formed_after_sellside_impulse",
            "no_m15_close_below_level",
            "no_m15_close_above_level",
        ],
        "price_action_levels.weakness_reason_codes": [
            "single_reaction_only",
            "messy_overlap",
            "formed_inside_chop",
            "no_clean_impulse_from_level",
            "inside_larger_auction",
        ],
        "support_resistance_negative_examples.candidate_role": ["support", "resistance"],
        "support_resistance_negative_examples.rejection_reason_codes": [
            "single_reaction_only",
            "formed_inside_chop",
            "too_close_to_stronger_level",
            "no_clear_defensive_response",
        ],
        "micro_support_resistance_negative_examples.candidate_role": ["micro_support", "micro_resistance"],
        "micro_support_resistance_negative_examples.rejection_reason_codes": [
            "single_reaction_only",
            "formed_inside_chop",
            "too_close_to_stronger_level",
            "no_clear_defensive_response",
        ],
        "support_resistance_borderline_examples.candidate_role": ["support", "resistance"],
        "support_resistance_borderline_examples.label_confidence": ["borderline"],
        "support_resistance_borderline_examples.supporting_reason_codes": [
            "two_minor_reactions",
            "near_prior_rejection_area",
        ],
        "support_resistance_borderline_examples.weakness_reason_codes": [
            "messy_overlap",
            "no_clean_impulse_from_level",
            "inside_larger_auction",
        ],
        "auction_ranges.type": ["macro", "micro"],
        "event_outcome_labels.event_type": [
            "support_level_breach",
            "resistance_level_breach",
            "support_level_retest",
            "resistance_level_retest",
            "support_level_bounce",
            "resistance_level_rejection",
            "macro_auction_upper_bound_breach",
            "macro_auction_lower_bound_breach",
            "micro_auction_upper_bound_sweep",
            "micro_auction_lower_bound_sweep",
            "macro_auction_upper_bound_sweep",
            "macro_auction_lower_bound_sweep",
            "micro_auction_upper_bound_breach",
            "micro_auction_lower_bound_breach",
            "auction_reentry",
            "failed_reclaim",
        ],
        "event_outcome_labels.expected_direction": ["long", "short", "neutral"],
        "event_outcome_labels.referenced_structure.structure_type": [
            "support",
            "resistance",
            "macro_auction_upper_bound",
            "macro_auction_lower_bound",
            "micro_auction_upper_bound",
            "micro_auction_lower_bound",
            "price",
        ],
        "event_outcome_labels.referenced_structure.structure_role": [
            "support",
            "resistance",
            "macro_auction_upper_bound",
            "macro_auction_lower_bound",
            "micro_auction_upper_bound",
            "micro_auction_lower_bound",
            "price",
        ],
        "event_outcome_labels.human_interpretation.read": [
            "support_breach_with_continuation_potential",
            "resistance_breach_with_continuation_potential",
            "support_hold_with_bounce_potential",
            "resistance_hold_with_rejection_potential",
            "failed_breakout",
            "failed_breakdown",
            "auction_reentry_with_continuation_potential",
        ],
        "event_outcome_labels.human_interpretation.confidence": ["high", "medium", "low"],
        "event_outcome_labels.human_interpretation.reason_codes": [
            "clean_close_below_support",
            "clean_close_above_resistance",
            "prior_retest_failed",
            "micro_trend_aligned_sellside",
            "micro_trend_aligned_buyside",
            "rejection_from_level",
            "quick_reclaim_failed",
            "auction_bound_accepted",
        ],
        "event_outcome_labels.human_interpretation.counterevidence_codes": [
            "messy_overlap",
            "weak_close_through_level",
            "opposing_micro_trend",
            "inside_larger_auction",
            "immediate_structure_reclaim",
            "limited_follow_through",
        ],
        "confluence.classification": [
            "pattern_at_support",
            "pattern_at_resistance",
            "pattern_at_macro_auction_bound",
            "pattern_at_micro_auction_bound",
            "trend_change_at_level",
            "pattern_within_trend",
            "pattern_at_trend_break"
        ],
    }

def _field_definitions() -> dict:
    return {
        "schema_metadata": "Top-level schema identity, version, release, intended AI use, and evidence boundary.",
        "required_sections": "Machine-readable section contract describing required schema areas and the top-level keys that satisfy each area.",
        "session_metadata": "Session date, timezone, local 5PM-to-5PM boundaries, and UTC boundaries.",
        "instrument_metadata": "Tradable instrument, exchange, market type, and timeframe metadata.",
        "objective_price_action_fields": "Index of automatically generated objective OHLC-only sections.",
        "subjective_market_structure_fields": "Index of manually populated market-structure labeling sections.",
        "structural_events": "Index of structural event sections that link levels, patterns, event outcomes, and candle ranges.",
        "candles": "Raw 15-minute OHLC candles for the requested 5PM-to-5PM session.",
        "candles.candlestick.body_size": "Absolute candle body size, calculated as abs(close - open).",
        "candles.relative_to_previous_candle": "Auto-derived relationship between the current candle and the immediately prior candle. Omitted for candle 0.",
        "auto_price_action_sequences": "Automatically derived consecutive OHLC relationship runs for higher-high/higher-low and lower-high/lower-low sequences.",
        "auto_price_action_sequences.candle_idx_range": "Range includes the baseline candle used for the first comparison and the final candle in the consecutive run. Only higher-high/higher-low and lower-high/lower-low runs of at least four candles are emitted.",
        "auto_price_action_sequences.length": "Number of candles in the sequence range, calculated as end_idx - start_idx + 1.",
        "last_24h_percent_range": "High-low range for this 24-hour session, expressed in points and percent of the session low.",
        "price_action_levels": "Manual support, resistance, micro support, and micro resistance levels identified from price action only.",
        "price_action_levels.candle_idx_range": "Candle range where the level was observed or established.",
        "price_action_levels.holds_at_session_end": "Boolean marker for whether the level remains valid through the final candle of the session.",
        "price_action_levels.label_confidence": "Controlled confidence label for manually identified support, resistance, micro support, and micro resistance levels.",
        "price_action_levels.confidence_reason_codes": "Controlled reasons supporting the validity of a manually identified support, resistance, micro support, or micro resistance level.",
        "price_action_levels.weakness_reason_codes": "Controlled reasons describing weaknesses in a manually identified support, resistance, micro support, or micro resistance level. Empty list means no weaknesses assigned.",
        "price_action_levels.level_converted_to_*": "Structured flag for whether a support level later became a lower auction bound or a resistance level later became an upper auction bound, with the candle index and auction id if applicable.",
        "support_resistance_negative_examples": "Standalone manual negative examples for candidate support or resistance levels that should not be labeled as valid levels.",
        "micro_support_resistance_negative_examples": "Standalone manual negative examples for candidate micro support or micro resistance levels that should not be labeled as valid micro levels.",
        "support_resistance_borderline_examples": "Standalone manual borderline examples for candidate support or resistance levels that are ambiguous or weak.",
        "support_resistance_negative_examples.rejection_reason_codes": "Controlled reasons describing why the candidate support or resistance example was rejected as a valid level.",
        "micro_support_resistance_negative_examples.rejection_reason_codes": "Controlled reasons describing why the candidate micro support or micro resistance example was rejected as a valid micro level.",
        "support_resistance_borderline_examples.supporting_reason_codes": "Controlled reasons supporting a borderline candidate support or resistance level.",
        "support_resistance_borderline_examples.weakness_reason_codes": "Controlled reasons describing weaknesses in a borderline candidate support or resistance level.",
        "auction_ranges": "Manual macro and micro auction ranges. Macro ranges can contain multiple participation windows; micro ranges use one candle range.",
        "micro_trends": "Manual short-range directional sequences, such as micro buyside, micro sellside, or micro ranging.",
        "micro_trends.accelerated": "Boolean marker for whether the micro trend had a distinct acceleration phase.",
        "micro_trends.acceleration_candle_idx_range": "Optional candle range where the micro trend accelerated. Leave indexes null when accelerated is false.",
        "micro_trends.trend_break_candle_idx": "Candle index where the micro trend breaks. This should match candle_idx_range.end_idx.",
        "micro_trends.trend_reclaimed_after_break": "Boolean marker for whether the broken micro trend was reclaimed before trend break confirmation.",
        "micro_trends.trend_reclaim_candle_idx": "Optional candle index where the broken micro trend was reclaimed. Use only when it occurs before trend_break_confirmation_candle_idx.",
        "micro_trends.trend_break_confirmation_candle_idx": "Optional later candle index used to confirm the micro trend break.",
        "micro_candlestick_patterns": "Candlestick pattern labels with candle ranges and optionally explicit candles involved. Micro range engulfings are auto-detected from OHLC.",
        "micro_candlestick_patterns.candles_involved": "Optional explicit candle indexes involved in the pattern, useful when the pattern is non-contiguous.",
        "micro_candlestick_patterns.auto_detected": "Boolean marker for patterns generated directly from OHLC rules rather than manual labeling.",
        "event_outcome_labels": "Manual event logs linking a structural price-action event to its referenced structure, context, human interpretation, and raw lookahead outcome.",
        "event_outcome_labels.referenced_structure": "Level, auction bound, or price structure referenced by the event.",
        "event_outcome_labels.context_refs": "Optional links from the event to micro trend, auction, and confluence labels.",
        "event_outcome_labels.human_interpretation": "Manual interpretation of the event, including controlled read, confidence, supporting reasons, and counterevidence.",
        "event_outcome_labels.raw_price_outcome": "Autopopulated by populate_raw_price_outcomes.py after manual event labeling. Contains raw lookahead outcome measurements across standard 8, 16, 24, and 48 candle windows in the completed JSON copy.",
        "confluence": "Manual combined events where a pattern or trend change aligns with a level or auction structure.",
        "setup_tags": "Optional controlled setup tags for retrieval or AI filtering; empty list means no setup tags assigned.",
        "data_quality": "Automatically generated completeness metadata for the OHLC session.",
        "validation_rules": "Machine-readable validation rules for required sections, session length, candle count, timezone values, and manual-label references.",
    }

def build_json(
    df: pd.DataFrame,
    df_24h: pd.DataFrame,
    symbol: str,
    start_utc: datetime,
    end_utc: datetime,
    session_date: str,
    session_tz: str,
    generated_at: datetime | None = None,
) -> dict:
    """Construct the price-action-only session JSON object."""
    generated_at = generated_at or datetime.now(timezone.utc)
    candles = []
    candle_rows = []
    previous = None
    for idx, row in enumerate(df.itertuples(index=False)):
        row_dict = {
            "idx": idx,
            "open_time": row.open_time,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
        }
        candle_rows.append(row_dict)
        candle = {
            "idx": idx,
            "t": row.open_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "o": _r(row.open, 2),
            "h": _r(row.high, 2),
            "l": _r(row.low, 2),
            "c": _r(row.close, 2),
            "candlestick": _candle_classification(row_dict),
        }
        if previous is not None:
            candle["relative_to_previous_candle"] = _relative_sequence(previous, row_dict)
        candles.append(candle)
        previous = row_dict

    local_offset = start_utc.astimezone(_tz(session_tz)).strftime("%z")
    session_metadata = {
        "date": session_date,
        "timezone": session_tz,
        "start_local": f"{session_date}T17:00:00{local_offset}",
        "end_local_exclusive": f"{end_utc.astimezone(_tz(session_tz)).strftime('%Y-%m-%d')}T17:00:00{local_offset}",
        "start_time_utc": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time_utc_exclusive": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    instrument_metadata = {
        "symbol": symbol,
        "timeframe": TIMEFRAME,
        "exchange": "Binance",
        "market_type": "perpetual_futures",
    }
    payload = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "schema_metadata": _schema_metadata(generated_at),
        "required_sections": _required_sections(),
        "title": SCHEMA_NAME,
        "symbol": symbol,
        "timeframe": TIMEFRAME,
        "session_metadata": session_metadata,
        "instrument_metadata": instrument_metadata,
        "allowed_values": _allowed_values(),
        "field_definitions": _field_definitions(),
        "session": session_metadata,
        "last_24h_percent_range": _last_24h_range(df_24h),
        "setup_tags": [],
        "data_quality": _data_quality(df, start_utc, end_utc),
        "validation_rules": _validation_rules(),
        "objective_price_action_fields": {
            "section_type": "objective",
            "section_keys": ["candles", "auto_price_action_sequences", "last_24h_percent_range"],
        },
        "subjective_market_structure_fields": {
            "section_type": "subjective_manual_labels",
            "section_keys": [
                "price_action_levels",
                "auction_ranges",
                "micro_trends",
                "micro_candlestick_patterns",
            ],
        },
        "structural_events": {
            "section_type": "manual_structural_events",
            "section_keys": ["confluence", "event_outcome_labels"],
        },
        "provenance": {
            "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": "jsonschema_scraper.py",
            "source": {
                "exchange": instrument_metadata["exchange"],
                "market_type": instrument_metadata["market_type"],
                "endpoints": {"klines": BINANCE_URL},
                "included_fields": ["open_time", "open", "high", "low", "close"],
            },
        },
        "candles": candles,
        "auto_price_action_sequences": _auto_price_action_sequences(candle_rows),
        "price_action_levels": {
            "support": [_empty_support_level(f"support_{i}") for i in range(1, 4)],
            "resistance": [_empty_resistance_level(f"resistance_{i}") for i in range(1, 4)],
            "micro_support": [_empty_micro_level(f"micro_support_{i}") for i in range(1, 4)],
            "micro_resistance": [_empty_micro_level(f"micro_resistance_{i}") for i in range(1, 4)],
        },
        "support_resistance_negative_examples": [
            _empty_support_resistance_negative_example(f"support_resistance_negative_{i}") for i in range(1, 4)
        ],
        "micro_support_resistance_negative_examples": [
            _empty_micro_support_resistance_negative_example(f"micro_support_resistance_negative_{i}") for i in range(1, 4)
        ],
        "support_resistance_borderline_examples": [
            _empty_support_resistance_borderline_example(f"support_resistance_borderline_{i}") for i in range(1, 4)
        ],
        "auction_ranges": {
            "macro": [{"id": f"macro_auction_{i}", "low": None, "high": None, "candles_within": [_empty_candle_range()]} for i in range(1, 4)],
            "micro": [{"id": f"micro_auction_{i}", "low": None, "high": None, "candle_idx_range": _empty_candle_range()} for i in range(1, 4)],
        },
        "micro_trends": [
            _empty_micro_trend(f"micro_trend_{i}") for i in range(1, 6)
        ],
        "micro_candlestick_patterns": _auto_micro_range_engulfings(candle_rows) + [
            {
                "id": "candlestick_pattern_1",
                "classification": None,
                "candle_idx_range": {"start_idx": None, "end_idx": None},
                "candles_involved": []
            },
            {
                "id": "candlestick_pattern_2",
                "classification": None,
                "candle_idx_range": {"start_idx": None, "end_idx": None},
                "candles_involved": []
            },
            {
                "id": "candlestick_pattern_3",
                "classification": None,
                "candle_idx_range": {"start_idx": None, "end_idx": None},
                "candles_involved": []
            }
        ],
        "confluence": [
            {
                "id": "confluence_1",
                "pattern_id": None,
                "level_id": None,
                "candle_idx_range": {"start_idx": None, "end_idx": None},
                "classification": None,
            },
            {
                "id": "confluence_2",
                "pattern_id": None,
                "level_id": None,
                "candle_idx_range": {"start_idx": None, "end_idx": None},
                "classification": None,
            },
            {
                "id": "confluence_3",
                "pattern_id": None,
                "level_id": None,
                "candle_idx_range": {"start_idx": None, "end_idx": None},
                "classification": None,
            }
        ],
        "event_outcome_labels": [
            _empty_event_outcome_label(f"event_{i}") for i in range(1, 7)
        ],
    }
    return payload


_BG    = "#131722"
_GRID  = "rgba(255,255,255,0.06)"
_TEXT  = "#b2b5be"
_GREEN = "#26a69a"
_RED   = "#ef5350"


def plot_chart(df: pd.DataFrame, symbol: str, has_futures: bool = False) -> None:
    """Render an interactive OHLC candlestick chart."""
    ts = df["open_time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=ts,
                open=df["open"], high=df["high"],
                low=df["low"], close=df["close"],
                name="Price",
                increasing_line_color=_GREEN, increasing_fillcolor=_GREEN,
                decreasing_line_color=_RED, decreasing_fillcolor=_RED,
                line_width=1,
                whiskerwidth=0,
            )
        ]
    )
    n = len(df)
    t0 = df["open_time"].iloc[0].strftime("%Y-%m-%d %H:%M")
    t1 = df["open_time"].iloc[-1].strftime("%H:%M")
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        title=f"<b style='font-size:14px'>{symbol} - 15m OHLC</b><span style='font-size:11px;color:{_TEXT}'>  {t0} UTC to {t1} UTC ({n} candles)</span>",
        height=720,
        margin=dict(l=70, r=20, t=50, b=40),
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(showgrid=True, gridcolor=_GRID, tickfont=dict(size=10, color=_TEXT))
    fig.update_yaxes(title_text="Price", showgrid=True, gridcolor=_GRID, tickfont=dict(size=10, color=_TEXT))
    fig.show()
    return


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Pull Binance 15m OHLC data for a fixed 5PM-5PM EST/EDT session.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python binance_datascraper.py "1/20/26 (EST)"
  python binance_datascraper.py "1/20/26 EDT" --symbol ETHUSDT
  python binance_datascraper.py "1/20/26 (EST)" --output ctx.json --no-chart
        """,
    )
    ap.add_argument(
        "range",
        help="Quoted session date, e.g. '1/20/26 (EST)' or '1/20/26 EDT'",
    )
    ap.add_argument("--symbol",     default=DEFAULT_SYMBOL,
                    help=f"Binance symbol (default: {DEFAULT_SYMBOL})")
    ap.add_argument("--output",     default=None,
                    help="Write JSON to this path instead of stdout")
    ap.add_argument("--no-chart",   action="store_true",
                    help="Skip interactive chart rendering")
    args = ap.parse_args()

    # 1 - Parse the fixed local session
    print(f"â†’ Parsing range: {args.range!r}")
    start_utc, end_utc, session_date, session_tz = parse_session_range(args.range)
    print(f"  UTC: {start_utc.strftime('%Y-%m-%d %H:%M')}  â†’  {end_utc.strftime('%Y-%m-%d %H:%M')}")

    # 2 - Fetch the full 24-hour session.
    fetch_start_ms = int(start_utc.timestamp() * 1000)
    fetch_end_ms   = int(end_utc.timestamp()   * 1000)

    # 3 â”€â”€ Fetch from Binance
    print(f"\nâ†’ Fetching {args.symbol} 15m candles from Binance â€¦")
    raw = fetch_klines(args.symbol, fetch_start_ms, fetch_end_ms)
    print(f"  Retrieved {len(raw)} candles")

    if not raw:
        print("ERROR: Binance returned no data. Check symbol and date range.", file=sys.stderr)
        sys.exit(1)

    # 4 - Build DataFrame over the OHLC-only fetch window
    df_full = klines_to_df(raw)

    # 5 â”€â”€ Slice to the requested range (end is exclusive)
    mask     = (df_full["open_time"] >= start_utc) & (df_full["open_time"] < end_utc)
    df_range = df_full[mask].copy().reset_index(drop=True)

    df_24h = df_range
    print(f"  Candles in requested range: {len(df_range)}")

    if df_range.empty:
        print(
            "ERROR: No candles found in the specified range.\n"
            "  Check that the symbol is valid on Binance (e.g. BTCUSDT) and the date/time is in the past.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 6 â”€â”€ Build and emit JSON
    output   = build_json(
        df_range, df_24h, args.symbol, start_utc, end_utc, session_date, session_tz,
        generated_at=datetime.now(timezone.utc),
    )
    json_str = json.dumps(output, indent=2, default=str)

    if not args.output:
        symbol_safe = args.symbol.replace("-", "")
        ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = f"ctx_{symbol_safe}_{ts_tag}.json"

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(json_str)
    print(f"\nâ†’ JSON saved â†’ {args.output}")

    # 7 â”€â”€ Render chart
    if not args.no_chart:
        print("\nâ†’ Rendering chart â€¦")
        plot_chart(df_range, args.symbol)


if __name__ == "__main__":
    main()
