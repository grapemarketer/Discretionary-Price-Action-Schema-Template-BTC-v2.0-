# Price Action Session Labeling Schema

This repository contains a two-stage workflow for producing OHLC-only price-action labeling files and then completing them with objective post-event outcome measurements.

The generator creates a fixed 24-hour manual labeling template from Binance 15-minute futures candles. The helper script is run after manual labeling to fill raw price outcome measurements for each labeled event.

## Files

| File | Purpose |
|---|---|
| `v2.1_jsonschema.py` | Current generator for the 24-hour price-action labeling JSON template. |
| `v2.0_jsonschema.py` | Older generator retained in the workspace. Prefer `v2.1_jsonschema.py` for new files. |
| `populate_raw_price_outcomes.py` | Reads a manually labeled JSON file and fills `event_outcome_labels.raw_price_outcome`. |
| `ctx_BTCUSDT_*.json` | Generated labeling templates or completed labeling files. |

## Requirements

Install the generator dependencies:

```bash
pip install requests pandas plotly
```

The generator uses:

- `requests` for Binance API requests
- `pandas` for candle data handling
- `plotly` for the optional candlestick chart

The helper script uses only the Python standard library.

## Workflow Overview

The workflow has two stages:

1. Generate the 24-hour manual labeling file with `v2.1_jsonschema.py`.
2. After manually labeling events, run `populate_raw_price_outcomes.py` to produce a completed JSON file with measured outcomes.

The main labeling session always remains 24 hours, from 5PM to 5PM in either EST or EDT.

The helper may fetch additional candles after the session ends, but only for outcome measurement. These extra candles are not part of the manual labeling session and are not appended to `candles`.

## Stage 1: Generate The Manual Labeling Template

Run the generator with a session date and timezone:

```bash
python v2.1_jsonschema.py "5/6/26 EDT" --symbol BTCUSDT --output ctx_BTCUSDT_20260506.json
```

To skip the interactive chart:

```bash
python v2.1_jsonschema.py "5/6/26 EDT" --symbol BTCUSDT --output ctx_BTCUSDT_20260506.json --no-chart
```

If `--output` is omitted, the script creates a timestamped file:

```text
ctx_BTCUSDT_YYYYMMDD_HHMMSS.json
```

Accepted date examples:

```bash
python v2.1_jsonschema.py "1/20/26 (EST)"
python v2.1_jsonschema.py "1/20/26 EDT"
python v2.1_jsonschema.py "1/20/2026 EST"
```

Only `EST` and `EDT` are supported.

## What The Generator Produces

The generator fetches Binance futures 15-minute OHLC candles for a fixed 24-hour session:

```text
5PM local session start -> 5PM next-day local session end
```

The output JSON includes:

- `schema_metadata`
- `session_metadata`
- `instrument_metadata`
- `candles`
- `auto_price_action_sequences`
- `last_24h_percent_range`
- `price_action_levels`
- `macro_support_resistance_negative_examples`
- `micro_support_resistance_negative_examples`
- `macro_support_resistance_borderline_examples`
- `micro_support_resistance_borderline_examples`
- `auction_ranges`
- `auction_ranges_negative_examples`
- `micro_level_regime_context`
- `micro_trends`
- `micro_candlestick_patterns`
- `confluence`
- `event_outcome_labels`

The `candles` section is the manual labeling window. It should remain the 24-hour session only.

## Manual Labeling Sections

### Price Action Levels

The current schema uses explicit macro and micro level roles:

- `macro_support`
- `macro_resistance`
- `micro_support`
- `micro_resistance`

These are stored under:

```json
"price_action_levels": {
  "macro_support": [],
  "macro_resistance": [],
  "micro_support": [],
  "micro_resistance": []
}
```

Macro levels represent durable session structures. Micro levels represent immediate local structures and include a short `context_window` with an `expires_after_bars` placeholder.

Each level includes formation, candle range, reaction indexes, confidence fields, weakness fields, and a `holds_at_session_end` flag. Macro support/resistance levels also include conversion fields for later auction-bound conversion.

### Negative And Borderline Examples

The schema includes explicit sections for examples that should not be treated as clean levels:

- `macro_support_resistance_negative_examples`
- `micro_support_resistance_negative_examples`
- `macro_support_resistance_borderline_examples`
- `micro_support_resistance_borderline_examples`

Use these sections to capture rejected, duplicate, weak, or ambiguous candidates instead of leaving them undocumented. This helps ML ingestion learn what not to classify as a valid level.

### Auction Ranges

Auction ranges are split into:

```json
"auction_ranges": {
  "macro": [],
  "micro": []
}
```

Macro and micro auctions are validated by macro support/resistance bounds:

- lower bound should reference a `macro_support`
- upper bound should reference a `macro_resistance`
- `macro_support_resistance_distance_pct` records the distance between bounds
- distances greater than `0.5%` classify as macro auctions
- distances equal to or below `0.5%` classify as micro auctions

Rejected auction candidates belong in:

```json
"auction_ranges_negative_examples": {
  "macro": [],
  "micro": []
}
```

### Micro Level Regime Context

`micro_level_regime_context` summarizes local pressure from repeated micro support and micro resistance behavior.

It tracks:

- micro supports formed, held, and breached
- micro resistances formed, held, and breached
- dominant pressure
- regime read
- referenced micro support and resistance IDs

This section is useful for modeling whether local structure was showing buyside momentum, sellside momentum, or two-way chop before an event.

### Micro Trends

Micro trends include:

- trend direction
- candle range
- confirmation candle
- acceleration range
- trend break candle
- reclaim after break
- break confirmation

Example:

```json
{
  "id": "micro_trend_1",
  "trend": null,
  "candle_idx_range": {
    "start_idx": null,
    "end_idx": null
  },
  "confirmation_candle_idx": null,
  "accelerated": false,
  "acceleration_candle_idx_range": {
    "start_idx": null,
    "end_idx": null
  },
  "trend_break_candle_idx": null,
  "trend_reclaimed_after_break": false,
  "trend_reclaim_candle_idx": null,
  "trend_break_confirmation_candle_idx": null
}
```

### Candlestick Patterns

The schema includes manual candlestick pattern slots and auto-detects micro range engulfings from OHLC:

- `bullish_micro_range_engulfing`
- `bearish_micro_range_engulfing`

Auto-detected patterns are marked with:

```json
"auto_detected": true
```

Manual pattern entries can also include `candles_involved` when the relevant candles are non-contiguous.

### Confluence

`confluence` links a pattern, primary macro structure, optional confirming micro break, and directional support.

Use this section when a candlestick pattern, level reaction, trend change, or micro-level break strengthens the read of a structural event.

### Event Outcome Labels

`event_outcome_labels` is the bridge between manual structure labeling and automated raw outcome measurement.

Each event can define:

- event type
- event candle index
- expected direction
- referenced structure
- optional context references
- last relevant micro level
- human interpretation
- raw price outcome placeholders

Example:

```json
{
  "id": "event_1",
  "event_type": "macro_support_level_retest",
  "event_candle_idx": 42,
  "expected_direction": "long",
  "referenced_structure": {
    "structure_type": "macro_support",
    "structure_id": "macro_support_1",
    "structure_role": "macro_support",
    "structure_price": 62500.0
  },
  "context_refs": {
    "micro_trend_id": null,
    "auction_id": null,
    "confluence_ids": []
  },
  "last_micro_level": {
    "level_id": null,
    "level_role": null,
    "level_price": null,
    "level_validation_idx": null,
    "distance_to_referenced_structure_pct": null,
    "has_significance": false,
    "significance_reason_codes": []
  },
  "human_interpretation": {
    "read": "macro_support_hold_with_bounce_potential",
    "confidence": "medium",
    "reason_codes": [],
    "counterevidence_codes": []
  }
}
```

The helper script fills `raw_price_outcome`; it is not meant to be filled manually.

## Stage 2: Populate Raw Price Outcomes

After manually labeling events, run:

```bash
python populate_raw_price_outcomes.py ctx_BTCUSDT_20260506.json
```

By default, this writes:

```text
ctx_BTCUSDT_20260506_completed.json
```

To choose the output path:

```bash
python populate_raw_price_outcomes.py ctx_BTCUSDT_20260506.json --output ctx_BTCUSDT_20260506_completed.json
```

To allow overwriting an existing completed file:

```bash
python populate_raw_price_outcomes.py ctx_BTCUSDT_20260506.json --output ctx_BTCUSDT_20260506_completed.json --overwrite
```

## What The Helper Script Measures

The helper script populates standard raw outcome windows:

- 1 candle
- 2 candles
- 4 candles
- 6 candles
- 8 candles
- 10 candles
- 16 candles
- 24 candles
- 48 candles

These are stored under:

```json
"raw_price_outcome": {
  "standard_windows": {
    "1": {},
    "2": {},
    "4": {},
    "6": {},
    "8": {},
    "10": {},
    "16": {},
    "24": {},
    "48": {}
  }
}
```

Each window contains:

```json
{
  "outcome_measured": true,
  "bars_measured": 24,
  "lookahead_end_idx": 66,
  "max_favorable_excursion_pct": 1.2345,
  "max_adverse_excursion_pct": 0.4567,
  "continuation_occurred": true,
  "invalidation_occurred": false,
  "structure_reclaimed": false,
  "bars_until_max_favorable": 10,
  "bars_until_max_adverse": 3
}
```

For long events:

- favorable movement is measured from the event close to the highest future high
- adverse movement is measured from the event close to the lowest future low

For short events:

- favorable movement is measured from the event close to the lowest future low
- adverse movement is measured from the event close to the highest future high

If `referenced_structure.structure_price` is provided, the helper also checks whether the structure was reclaimed or invalidated during the lookahead window.

Only events with an integer `event_candle_idx` and `expected_direction` of `long` or `short` are measured. Neutral or incomplete events remain unmeasured.

## Extra Lookahead Candles

The generator only includes the 24-hour manual labeling session in `candles`.

If a labeled event is close to the end of the session, the 48-candle outcome window may extend beyond the available session candles. In that case, the helper fetches up to 12 additional hours of Binance 15-minute candles.

The first extra candle starts exactly at:

```json
session_metadata.end_time_utc_exclusive
```

This keeps the transition aligned with the main session boundary and avoids missing or overlapping candles.

Extra candles are not appended to `candles`. They are written separately at the bottom of the completed JSON file:

```json
"raw_price_outcome_lookahead_candles": []
```

This section is included for auditability and accuracy. It shows the continuation candles used by the helper for raw outcome measurement.

## Completed Output Metadata

The helper also writes:

```json
"raw_price_outcome_population": {
  "standard_windows_bars": [1, 2, 4, 6, 8, 10, 16, 24, 48],
  "manual_labeling_candles_unchanged": true,
  "extra_lookahead_candles_fetched": 0,
  "extra_lookahead_data_usage": "..."
}
```

This records how the outcome fields were populated and confirms that the original manual labeling candles were not modified.

## Important Boundaries

The generator is for manual labeling.

The helper script is for objective outcome measurement after manual labeling.

The 24-hour `candles` array should be treated as the manual labeling session. Extra lookahead candles are only for measuring future outcomes and should not be used as part of the manual labeling window.

Raw outcome fields should be regenerated by `populate_raw_price_outcomes.py` after event labels change.

## Example Full Workflow

Generate the manual labeling template:

```bash
python v2.1_jsonschema.py "5/6/26 EDT" --symbol BTCUSDT --output ctx_BTCUSDT_20260506.json --no-chart
```

Manually fill relevant fields in:

- `price_action_levels`
- `macro_support_resistance_negative_examples`
- `micro_support_resistance_negative_examples`
- `macro_support_resistance_borderline_examples`
- `micro_support_resistance_borderline_examples`
- `auction_ranges`
- `auction_ranges_negative_examples`
- `micro_level_regime_context`
- `micro_trends`
- `micro_candlestick_patterns`
- `confluence`
- `event_outcome_labels`

Then populate raw outcome measurements:

```bash
python populate_raw_price_outcomes.py ctx_BTCUSDT_20260506.json --output ctx_BTCUSDT_20260506_completed.json
```

The completed file will contain:

- the original manual labels
- autopopulated `raw_price_outcome` windows
- `raw_price_outcome_population` metadata
- `raw_price_outcome_lookahead_candles` at the bottom

## Notes

- The schema is OHLC-only.
- The main session is always 24 hours.
- Extra lookahead candles are helper-only and outcome-only.
- Raw outcomes are autopopulated, not manually labeled.
- The expected direction for event measurement must be `long` or `short`.
- Events with missing or invalid `event_candle_idx` or `expected_direction` remain unmeasured.
