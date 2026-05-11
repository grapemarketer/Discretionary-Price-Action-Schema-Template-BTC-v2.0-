#!/usr/bin/env python3
"""
Populate raw_price_outcome fields for manually labeled event_outcome_labels.

Usage:
  python populate_raw_price_outcomes.py ctx_BTCUSDT_20260506_230000.json
  python populate_raw_price_outcomes.py ctx.json --output ctx_completed.json
  python populate_raw_price_outcomes.py ctx.json --overwrite
"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


RAW_PRICE_OUTCOME_WINDOWS = (1, 2, 4, 6, 8, 10, 16, 24, 48)
CONTINUATION_THRESHOLD_PCT = 0.2
FAVORABLE_THRESHOLDS_PCT = (0.15, 0.25, 0.35, 0.5, 0.65, 0.75, 0.85, 1.0, 1.5, 2.0)
PCT_DECIMALS = 4
BINANCE_URL = "https://fapi.binance.com/fapi/v1/klines"
TIMEFRAME = "15m"
GRANULARITY_SECONDS = 900
EXTRA_LOOKAHEAD_HOURS = 12
MAX_CANDLES = 1000


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON value must be an object.")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def _default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_completed{input_path.suffix}")


def _candle_by_idx(candles: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    indexed = {}
    for candle in candles:
        idx = candle.get("idx")
        if isinstance(idx, int):
            indexed[idx] = candle
    return indexed


def _parse_utc_timestamp(raw: Any, field_name: str) -> datetime:
    if not isinstance(raw, str):
        raise ValueError(f"Input JSON is missing {field_name}.")
    normalized = raw.replace("Z", "+00:00")
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Cannot parse {field_name}: {raw!r}") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _session_end_utc(data: dict[str, Any]) -> datetime:
    session_metadata = data.get("session_metadata") or data.get("session") or {}
    if not isinstance(session_metadata, dict):
        raise ValueError("Input JSON is missing session metadata.")
    return _parse_utc_timestamp(session_metadata.get("end_time_utc_exclusive"), "session_metadata.end_time_utc_exclusive")


def _symbol(data: dict[str, Any]) -> str:
    instrument_metadata = data.get("instrument_metadata") or {}
    symbol = instrument_metadata.get("symbol") if isinstance(instrument_metadata, dict) else None
    symbol = symbol or data.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("Input JSON is missing symbol metadata.")
    return symbol


def _fetch_binance_klines(symbol: str, start_utc: datetime, end_utc: datetime) -> list[list[Any]]:
    all_rows: list[list[Any]] = []
    chunk_ms = MAX_CANDLES * GRANULARITY_SECONDS * 1000
    cur_start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(end_utc.timestamp() * 1000)

    while cur_start_ms < end_ms:
        cur_end_ms = min(cur_start_ms + chunk_ms, end_ms)
        params = urlencode(
            {
                "symbol": symbol,
                "interval": TIMEFRAME,
                "startTime": cur_start_ms,
                "endTime": cur_end_ms - 1,
                "limit": MAX_CANDLES,
            }
        )
        with urlopen(f"{BINANCE_URL}?{params}", timeout=15) as response:
            rows = json.loads(response.read().decode("utf-8"))
        if not rows or isinstance(rows, dict):
            break
        all_rows.extend(rows)
        cur_start_ms = int(rows[-1][0]) + GRANULARITY_SECONDS * 1000
        if len(rows) < MAX_CANDLES:
            break

    seen: set[int] = set()
    deduped = []
    for row in all_rows:
        open_time_ms = int(row[0])
        if open_time_ms not in seen:
            seen.add(open_time_ms)
            deduped.append(row)
    return sorted(deduped, key=lambda row: int(row[0]))


def _klines_to_extra_candles(rows: list[list[Any]], start_idx: int) -> list[dict[str, Any]]:
    candles = []
    for offset, row in enumerate(rows):
        open_time = datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc)
        candles.append(
            {
                "idx": start_idx + offset,
                "t": open_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "o": round(float(row[1]), 2),
                "h": round(float(row[2]), 2),
                "l": round(float(row[3]), 2),
                "c": round(float(row[4]), 2),
                "lookahead_only": True,
            }
        )
    return candles


def _measurable_event_idxs(events: list[Any]) -> list[int]:
    event_idxs = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_idx = event.get("event_candle_idx")
        direction = event.get("expected_direction")
        if isinstance(event_idx, int) and direction in {"long", "short"}:
            event_idxs.append(event_idx)
    return event_idxs


def _extra_lookahead_candles_if_needed(
    data: dict[str, Any],
    candles: list[dict[str, Any]],
    events: list[Any],
) -> list[dict[str, Any]]:
    event_idxs = _measurable_event_idxs(events)
    if not event_idxs:
        return []

    max_existing_idx = max((candle.get("idx") for candle in candles if isinstance(candle.get("idx"), int)), default=-1)
    max_window = max(RAW_PRICE_OUTCOME_WINDOWS)
    if max(event_idx + max_window for event_idx in event_idxs) <= max_existing_idx:
        return []

    start_utc = _session_end_utc(data)
    end_utc = start_utc + timedelta(hours=EXTRA_LOOKAHEAD_HOURS)
    rows = _fetch_binance_klines(_symbol(data), start_utc, end_utc)
    return _klines_to_extra_candles(rows, max_existing_idx + 1)


def _record_lookahead_population_metadata(
    data: dict[str, Any],
    extra_candles: list[dict[str, Any]],
) -> None:
    metadata = {
        "standard_windows_bars": list(RAW_PRICE_OUTCOME_WINDOWS),
        "continuation_threshold_pct": CONTINUATION_THRESHOLD_PCT,
        "favorable_thresholds_pct": list(FAVORABLE_THRESHOLDS_PCT),
        "manual_labeling_candles_unchanged": True,
        "extra_lookahead_candles_fetched": len(extra_candles),
        "extra_lookahead_data_usage": (
            "Extra candles are fetched by populate_raw_price_outcomes.py only when raw outcome lookahead "
            "extends beyond the 24-hour manual labeling session. They are used only to populate "
            "event_outcome_labels.raw_price_outcome. They are written separately at the end of the completed "
            "JSON for auditability, and are not appended to candles or intended for manual labeling."
        ),
    }
    if extra_candles:
        metadata.update(
            {
                "extra_lookahead_start_time_utc": extra_candles[0]["t"],
                "extra_lookahead_end_time_utc": extra_candles[-1]["t"],
                "extra_lookahead_start_idx": extra_candles[0]["idx"],
                "extra_lookahead_end_idx": extra_candles[-1]["idx"],
                "extra_lookahead_source": {
                    "exchange": "Binance",
                    "endpoint": BINANCE_URL,
                    "timeframe": TIMEFRAME,
                },
            }
        )
    data["raw_price_outcome_population"] = metadata
    data["raw_price_outcome_lookahead_candles"] = extra_candles


def _pct(value: float, anchor: float) -> float:
    if anchor == 0:
        return 0.0
    return round((value / anchor) * 100, PCT_DECIMALS)


def _signed_pct(value: float, anchor: float) -> float | None:
    if anchor == 0:
        return 0.0
    return round(((value - anchor) / anchor) * 100, PCT_DECIMALS)


def _is_number(raw: Any) -> bool:
    return isinstance(raw, (int, float)) and not isinstance(raw, bool)


def _structure_price(event: dict[str, Any]) -> float | None:
    referenced_structure = event.get("referenced_structure") or {}
    if not isinstance(referenced_structure, dict):
        return None
    structure_price_raw = referenced_structure.get("structure_price")
    return float(structure_price_raw) if _is_number(structure_price_raw) else None


def _invalidation_rule(event_type: str | None, direction: str) -> tuple[str, str]:
    if isinstance(event_type, str):
        if "support" in event_type and "breach" in event_type:
            return "m15_close_back_above_structure_price", "above"
        if "resistance" in event_type and "breach" in event_type:
            return "m15_close_back_below_structure_price", "below"
        if "support" in event_type and ("retest" in event_type or "bounce" in event_type):
            return "m15_close_below_structure_price", "below"
        if "resistance" in event_type and ("retest" in event_type or "rejection" in event_type):
            return "m15_close_above_structure_price", "above"
        if "upper_bound" in event_type and ("breach" in event_type or "sweep" in event_type):
            return "m15_close_back_below_structure_price", "below"
        if "lower_bound" in event_type and ("breach" in event_type or "sweep" in event_type):
            return "m15_close_back_above_structure_price", "above"

    if direction == "short":
        return "m15_close_back_above_structure_price_direction_fallback", "above"
    if direction == "long":
        return "m15_close_back_below_structure_price_direction_fallback", "below"
    return "no_invalidation_rule", "none"


def _is_reclaim_or_invalidation(
    event_type: str | None,
    direction: str,
    structure_price: float | None,
    lookahead_candles: list[dict[str, Any]],
) -> tuple[bool | None, str]:
    rule_name, rule_side = _invalidation_rule(event_type, direction)
    if structure_price is None or rule_side == "none":
        return None, rule_name
    if rule_side == "above":
        return any(float(candle["c"]) > structure_price for candle in lookahead_candles), rule_name
    if rule_side == "below":
        return any(float(candle["c"]) < structure_price for candle in lookahead_candles), rule_name
    return None, rule_name


def _threshold_hits(
    direction: str,
    event_idx: int,
    anchor_price: float,
    lookahead_candles: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    hits: dict[str, dict[str, Any]] = {}
    for threshold in FAVORABLE_THRESHOLDS_PCT:
        first_hit_idx = None
        for candle in lookahead_candles:
            if direction == "short":
                favorable_pct = _pct(anchor_price - float(candle["l"]), anchor_price)
            else:
                favorable_pct = _pct(float(candle["h"]) - anchor_price, anchor_price)
            if favorable_pct >= threshold:
                first_hit_idx = candle["idx"]
                break
        key = f"{threshold:g}"
        hits[key] = {
            "hit": first_hit_idx is not None,
            "first_hit_idx": first_hit_idx,
            "bars_until_hit": first_hit_idx - event_idx if first_hit_idx is not None else None,
        }
    return hits


def _event_distance_features(
    event: dict[str, Any],
    event_candle: dict[str, Any] | None,
    direction: str | None,
    structure_price: float | None,
) -> dict[str, Any]:
    blank = {
        "event_close_to_structure_pct": None,
        "event_high_to_structure_pct": None,
        "event_low_to_structure_pct": None,
        "close_beyond_structure_pct": None,
        "wick_beyond_structure_pct": None,
    }
    if event_candle is None or structure_price is None:
        return blank

    close = float(event_candle["c"])
    high = float(event_candle["h"])
    low = float(event_candle["l"])
    blank.update(
        {
            "event_close_to_structure_pct": _signed_pct(close, structure_price),
            "event_high_to_structure_pct": _signed_pct(high, structure_price),
            "event_low_to_structure_pct": _signed_pct(low, structure_price),
        }
    )

    event_type = event.get("event_type")
    if isinstance(event_type, str) and ("support" in event_type or "lower_bound" in event_type):
        blank["close_beyond_structure_pct"] = _pct(structure_price - close, structure_price)
        blank["wick_beyond_structure_pct"] = _pct(structure_price - low, structure_price)
    elif isinstance(event_type, str) and ("resistance" in event_type or "upper_bound" in event_type):
        blank["close_beyond_structure_pct"] = _pct(close - structure_price, structure_price)
        blank["wick_beyond_structure_pct"] = _pct(high - structure_price, structure_price)
    elif direction == "short":
        blank["close_beyond_structure_pct"] = _pct(structure_price - close, structure_price)
        blank["wick_beyond_structure_pct"] = _pct(structure_price - low, structure_price)
    elif direction == "long":
        blank["close_beyond_structure_pct"] = _pct(close - structure_price, structure_price)
        blank["wick_beyond_structure_pct"] = _pct(high - structure_price, structure_price)
    return blank


def _event_population_warnings(
    event: dict[str, Any],
    candles_by_idx: dict[int, dict[str, Any]],
) -> list[str]:
    warnings = []
    event_idx = event.get("event_candle_idx")
    if not isinstance(event_idx, int):
        warnings.append("event_candle_idx_missing_or_not_integer")
    elif event_idx not in candles_by_idx:
        warnings.append("event_candle_idx_not_found")

    if event.get("expected_direction") not in {"long", "short"}:
        warnings.append("expected_direction_missing_or_not_measurable")
    if not isinstance(event.get("event_type"), str) or not event.get("event_type"):
        warnings.append("event_type_missing")
    if _structure_price(event) is None:
        warnings.append("referenced_structure.structure_price_missing")

    labeling_status = event.get("labeling_status")
    if not isinstance(labeling_status, str) or not labeling_status:
        warnings.append("labeling_status_missing")
    elif labeling_status.lower() in {"unlabeled", "pending", "todo", "incomplete"}:
        warnings.append(f"labeling_status_{labeling_status.lower()}")
    return warnings


def _blank_outcome() -> dict[str, Any]:
    return {
        "outcome_measured": False,
        "bars_measured": None,
        "lookahead_end_idx": None,
        "full_window_available": False,
        "anchor_price": None,
        "anchor_price_source": "event_candle_close",
        "max_favorable_excursion_pct": None,
        "max_adverse_excursion_pct": None,
        "max_favorable_close_excursion_pct": None,
        "max_adverse_close_excursion_pct": None,
        "final_close_return_pct": None,
        "closed_in_expected_direction": None,
        "continuation_threshold_pct": CONTINUATION_THRESHOLD_PCT,
        "continuation_occurred": None,
        "favorable_threshold_hit": None,
        "first_favorable_threshold_hit_idx": None,
        "bars_until_favorable_threshold": None,
        "invalidation_occurred": None,
        "structure_reclaimed": None,
        "invalidation_rule": None,
        "adverse_before_max_favorable": None,
        "threshold_hits": {
            f"{threshold:g}": {
                "hit": None,
                "first_hit_idx": None,
                "bars_until_hit": None,
            }
            for threshold in FAVORABLE_THRESHOLDS_PCT
        },
        "bars_until_max_favorable": None,
        "bars_until_max_adverse": None,
    }


def _measure_event_window(
    event: dict[str, Any],
    candles_by_idx: dict[int, dict[str, Any]],
    window_bars: int,
) -> dict[str, Any]:
    event_idx = event.get("event_candle_idx")
    direction = event.get("expected_direction")
    if not isinstance(event_idx, int) or direction not in {"long", "short"}:
        return _blank_outcome()

    event_candle = candles_by_idx.get(event_idx)
    if event_candle is None:
        return _blank_outcome()

    lookahead_idxs = range(event_idx + 1, event_idx + window_bars + 1)
    lookahead_candles = [candles_by_idx[idx] for idx in lookahead_idxs if idx in candles_by_idx]
    if not lookahead_candles:
        return _blank_outcome()

    anchor_price = float(event_candle["c"])
    if direction == "short":
        favorable_candle = min(lookahead_candles, key=lambda candle: float(candle["l"]))
        adverse_candle = max(lookahead_candles, key=lambda candle: float(candle["h"]))
        favorable_close_candle = min(lookahead_candles, key=lambda candle: float(candle["c"]))
        adverse_close_candle = max(lookahead_candles, key=lambda candle: float(candle["c"]))
        favorable_pct = _pct(anchor_price - float(favorable_candle["l"]), anchor_price)
        adverse_pct = _pct(float(adverse_candle["h"]) - anchor_price, anchor_price)
        favorable_close_pct = _pct(anchor_price - float(favorable_close_candle["c"]), anchor_price)
        adverse_close_pct = _pct(float(adverse_close_candle["c"]) - anchor_price, anchor_price)
        final_close_return_pct = _pct(anchor_price - float(lookahead_candles[-1]["c"]), anchor_price)
    else:
        favorable_candle = max(lookahead_candles, key=lambda candle: float(candle["h"]))
        adverse_candle = min(lookahead_candles, key=lambda candle: float(candle["l"]))
        favorable_close_candle = max(lookahead_candles, key=lambda candle: float(candle["c"]))
        adverse_close_candle = min(lookahead_candles, key=lambda candle: float(candle["c"]))
        favorable_pct = _pct(float(favorable_candle["h"]) - anchor_price, anchor_price)
        adverse_pct = _pct(anchor_price - float(adverse_candle["l"]), anchor_price)
        favorable_close_pct = _pct(float(favorable_close_candle["c"]) - anchor_price, anchor_price)
        adverse_close_pct = _pct(anchor_price - float(adverse_close_candle["c"]), anchor_price)
        final_close_return_pct = _pct(float(lookahead_candles[-1]["c"]) - anchor_price, anchor_price)

    structure_price = _structure_price(event)
    structure_reclaimed, invalidation_rule = _is_reclaim_or_invalidation(
        event.get("event_type"),
        direction,
        structure_price,
        lookahead_candles,
    )

    threshold_hits = _threshold_hits(direction, event_idx, anchor_price, lookahead_candles)
    continuation_threshold_hit = threshold_hits[f"{CONTINUATION_THRESHOLD_PCT:g}"]

    return {
        "outcome_measured": True,
        "bars_measured": len(lookahead_candles),
        "lookahead_end_idx": lookahead_candles[-1]["idx"],
        "full_window_available": len(lookahead_candles) == window_bars,
        "anchor_price": anchor_price,
        "anchor_price_source": "event_candle_close",
        "max_favorable_excursion_pct": favorable_pct,
        "max_adverse_excursion_pct": adverse_pct,
        "max_favorable_close_excursion_pct": favorable_close_pct,
        "max_adverse_close_excursion_pct": adverse_close_pct,
        "final_close_return_pct": final_close_return_pct,
        "closed_in_expected_direction": final_close_return_pct > 0,
        "continuation_threshold_pct": CONTINUATION_THRESHOLD_PCT,
        "continuation_occurred": favorable_pct >= CONTINUATION_THRESHOLD_PCT,
        "favorable_threshold_hit": continuation_threshold_hit["hit"],
        "first_favorable_threshold_hit_idx": continuation_threshold_hit["first_hit_idx"],
        "bars_until_favorable_threshold": continuation_threshold_hit["bars_until_hit"],
        "invalidation_occurred": structure_reclaimed,
        "structure_reclaimed": structure_reclaimed,
        "invalidation_rule": invalidation_rule,
        "adverse_before_max_favorable": adverse_candle["idx"] < favorable_candle["idx"],
        "threshold_hits": threshold_hits,
        "bars_until_max_favorable": favorable_candle["idx"] - event_idx,
        "bars_until_max_adverse": adverse_candle["idx"] - event_idx,
    }


def _measure_event_standard_windows(
    event: dict[str, Any],
    candles_by_idx: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    event_idx = event.get("event_candle_idx")
    event_candle = candles_by_idx.get(event_idx) if isinstance(event_idx, int) else None
    direction = event.get("expected_direction")
    direction_for_features = direction if direction in {"long", "short"} else None
    return {
        "outcome_population_warnings": _event_population_warnings(event, candles_by_idx),
        "event_distance_features": _event_distance_features(
            event,
            event_candle,
            direction_for_features,
            _structure_price(event),
        ),
        "standard_windows": {
            str(window): _measure_event_window(event, candles_by_idx, window)
            for window in RAW_PRICE_OUTCOME_WINDOWS
        }
    }


def populate_raw_price_outcomes(data: dict[str, Any]) -> int:
    candles = data.get("candles")
    events = data.get("event_outcome_labels")
    if not isinstance(candles, list):
        raise ValueError("Input JSON is missing a candles list.")
    if not isinstance(events, list):
        raise ValueError("Input JSON is missing an event_outcome_labels list.")

    extra_candles = _extra_lookahead_candles_if_needed(data, candles, events)
    candles_by_idx = _candle_by_idx(candles + extra_candles)
    populated = 0
    population_warnings = []
    for event in events:
        if not isinstance(event, dict):
            population_warnings.append({"event_id": None, "warnings": ["event_not_object"]})
            continue
        outcome = _measure_event_standard_windows(event, candles_by_idx)
        event["raw_price_outcome"] = outcome
        if outcome["outcome_population_warnings"]:
            population_warnings.append(
                {
                    "event_id": event.get("id"),
                    "event_candle_idx": event.get("event_candle_idx"),
                    "warnings": outcome["outcome_population_warnings"],
                }
            )
        if any(window["outcome_measured"] for window in outcome["standard_windows"].values()):
            populated += 1
    _record_lookahead_population_metadata(data, extra_candles)
    data["raw_price_outcome_population"]["warnings"] = population_warnings
    return populated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy a manually labeled session JSON and populate event raw_price_outcome fields."
    )
    parser.add_argument("input", help="Path to the manually labeled JSON file.")
    parser.add_argument("--output", "-o", default=None, help="Output JSON path. Default: <input>_completed.json")
    parser.add_argument("--overwrite", action="store_true", help="Allow writing over an existing output file.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else _default_output_path(input_path)
    if output_path.exists() and not args.overwrite:
        raise SystemExit(f"ERROR: output already exists: {output_path}. Use --overwrite or choose --output.")

    data = _load_json(input_path)
    populated = populate_raw_price_outcomes(data)
    _write_json(output_path, data)
    print(f"Populated {populated} event raw_price_outcome section(s).")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
