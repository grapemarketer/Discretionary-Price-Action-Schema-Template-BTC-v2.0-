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


def _is_reclaim_or_invalidation(
    direction: str,
    structure_price: float | None,
    lookahead_candles: list[dict[str, Any]],
) -> bool:
    if structure_price is None:
        return False
    if direction == "short":
        return any(float(candle["c"]) > structure_price for candle in lookahead_candles)
    if direction == "long":
        return any(float(candle["c"]) < structure_price for candle in lookahead_candles)
    return False


def _blank_outcome() -> dict[str, Any]:
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

    entry_close = float(event_candle["c"])
    if direction == "short":
        favorable_candle = min(lookahead_candles, key=lambda candle: float(candle["l"]))
        adverse_candle = max(lookahead_candles, key=lambda candle: float(candle["h"]))
        favorable_pct = _pct(entry_close - float(favorable_candle["l"]), entry_close)
        adverse_pct = _pct(float(adverse_candle["h"]) - entry_close, entry_close)
    else:
        favorable_candle = max(lookahead_candles, key=lambda candle: float(candle["h"]))
        adverse_candle = min(lookahead_candles, key=lambda candle: float(candle["l"]))
        favorable_pct = _pct(float(favorable_candle["h"]) - entry_close, entry_close)
        adverse_pct = _pct(entry_close - float(adverse_candle["l"]), entry_close)

    referenced_structure = event.get("referenced_structure") or {}
    structure_price_raw = referenced_structure.get("structure_price")
    structure_price = float(structure_price_raw) if isinstance(structure_price_raw, (int, float)) else None
    structure_reclaimed = _is_reclaim_or_invalidation(direction, structure_price, lookahead_candles)

    return {
        "outcome_measured": True,
        "bars_measured": len(lookahead_candles),
        "lookahead_end_idx": lookahead_candles[-1]["idx"],
        "max_favorable_excursion_pct": favorable_pct,
        "max_adverse_excursion_pct": adverse_pct,
        "continuation_occurred": favorable_pct > 0,
        "invalidation_occurred": structure_reclaimed,
        "structure_reclaimed": structure_reclaimed,
        "bars_until_max_favorable": favorable_candle["idx"] - event_idx,
        "bars_until_max_adverse": adverse_candle["idx"] - event_idx,
    }


def _measure_event_standard_windows(
    event: dict[str, Any],
    candles_by_idx: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    return {
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
    for event in events:
        if not isinstance(event, dict):
            continue
        outcome = _measure_event_standard_windows(event, candles_by_idx)
        event["raw_price_outcome"] = outcome
        if any(window["outcome_measured"] for window in outcome["standard_windows"].values()):
            populated += 1
    _record_lookahead_population_metadata(data, extra_candles)
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
