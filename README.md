# Discretionary-Price-Action-Schema-Template-BTC-v2.0-
JSON schema template for discretionary price-action traders who want to convert market-structure reads and judgements into machine-readable datasets for signal research, ML modeling, and systematic trading development.

# Price Action Session Labeling Schema

This repository contains a two-script workflow for building and completing OHLC-only price-action labeling files.

The main script creates a 24-hour manual labeling template from Binance 15-minute candles. The helper script is run after manual labeling to autopopulate raw price outcome measurements.

## Files

| File | Purpose |
|---|---|
| `v2.0_jsonschema.py` | Generates the main 24-hour price-action labeling JSON template. |
| `populate_raw_price_outcomes.py` | Reads a manually labeled JSON file and fills `event_outcome_labels.raw_price_outcome`. |

## Requirements

Install the required Python packages:

```bash
pip install requests pandas plotly
```

The main script uses:

- `requests` for Binance API requests
- `pandas` for candle data handling
- `plotly` for the optional candlestick chart

The helper script uses only the Python standard library.

## Workflow Overview

The workflow has two stages:

1. Generate the 24-hour manual labeling file with `v2.0_jsonschema.py`.
2. After manually labeling events, run `populate_raw_price_outcomes.py` to produce a completed JSON file with measured outcomes.

The main labeling session always remains 24 hours, from 5PM to 5PM in either EST or EDT.

The helper script may fetch additional candles after the session ends, but only for outcome measurement. These extra candles are not part of the manual labeling session.

## Stage 1: Generate The Manual Labeling Template

Run the main script with a session date and timezone:

```bash
python v2.0_jsonschema.py "5/6/26 EDT" --symbol BTCUSDT --output ctx_BTCUSDT_20260506.json
```

To skip the interactive chart:

```bash
python v2.0_jsonschema.py "5/6/26 EDT" --symbol BTCUSDT --output ctx_BTCUSDT_20260506.json --no-chart
```

If `--output` is omitted, the script creates a timestamped file:

```text
ctx_BTCUSDT_YYYYMMDD_HHMMSS.json
```

### Accepted Date Formats

Examples:

```bash
python v2.0_jsonschema.py "1/20/26 (EST)"
python v2.0_jsonschema.py "1/20/26 EDT"
python v2.0_jsonschema.py "1/20/2026 EST"
```

Only `EST` and `EDT` are supported.

## What The Main Script Produces

The main script fetches Binance futures 15-minute OHLC candles for a fixed 24-hour session:

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
- `support_resistance_negative_examples`
- `micro_support_resistance_negative_examples`
- `support_resistance_borderline_examples`
- `auction_ranges`
- `micro_trends`
- `micro_candlestick_patterns`
- `confluence`
- `event_outcome_labels`

The `candles` section is the manual labeling window. It should remain the 24-hour session only.

## Manual Labeling Sections

### Price Action Levels

The schema supports:

- support
- resistance
- micro support
- micro resistance

Each level includes:

```json
{
  "id": "support_1",
  "price": null,
  "candle_idx_range": {
    "start_idx": null,
    "end_idx": null
  },
  "holds_at_session_end": false,
  "level_role": "support",
  "label_confidence": null,
  "confidence_reason_codes": [],
  "weakness_reason_codes": []
}
```

Support and resistance also include conversion fields for later auction-bound conversion.

### Negative And Borderline Examples

The schema includes explicit sections for examples that should not be treated as clean levels:

- `support_resistance_negative_examples`
- `micro_support_resistance_negative_examples`
- `support_resistance_borderline_examples`

These sections are useful for capturing rejected or ambiguous level candidates, not only clean support and resistance.

### Auction Ranges

The schema supports:

- macro auction ranges
- micro auction ranges

Macro ranges can contain multiple candle windows. Micro ranges use one candle range.

### Micro Trends

Micro trends include fields for:

- trend direction
- candle range
- acceleration
- trend break
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

The schema includes manual candlestick pattern slots and also auto-detects micro range engulfings from OHLC:

- `bullish_micro_range_engulfing`
- `bearish_micro_range_engulfing`

Auto-detected patterns are marked with:

```json
"auto_detected": true
```

### Event Outcome Labels

`event_outcome_labels` is the main bridge between manual labeling and automated raw outcome measurement.

Each event can define:

- event type
- event candle index
- expected direction
- referenced structure
- optional context references
- human interpretation
- raw price outcome placeholders

Example:

```json
{
  "id": "event_1",
  "event_type": "support_level_bounce",
  "event_candle_idx": 42,
  "expected_direction": "long",
  "referenced_structure": {
    "structure_type": "support",
    "structure_id": "support_1",
    "structure_role": "support",
    "structure_price": 62500.0
  },
  "context_refs": {
    "micro_trend_id": null,
    "auction_id": null,
    "confluence_ids": []
  },
  "human_interpretation": {
    "read": "support_hold_with_bounce_potential",
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

- 8 candles
- 16 candles
- 24 candles
- 48 candles

These are stored under:

```json
"raw_price_outcome": {
  "standard_windows": {
    "8": {},
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

## Extra Lookahead Candles

The main script only includes the 24-hour manual labeling session in `candles`.

If a labeled event is close to the end of the session, the 48-candle outcome window may extend beyond the available session candles. In that case, the helper script fetches up to 12 additional hours of Binance 15-minute candles.

The first extra candle starts exactly at:

```json
session_metadata.end_time_utc_exclusive
```

This keeps the transition aligned with the main session boundary and avoids missing or overlapping candles.

Extra candles are not appended to `candles`. They are written separately at the bottom of the completed JSON file:

```json
"raw_price_outcome_lookahead_candles": []
```

This section is included for auditability and accuracy. It shows the continuation candles used by the helper script for raw outcome measurement.

## Completed Output Metadata

The helper script also writes:

```json
"raw_price_outcome_population": {
  "standard_windows_bars": [8, 16, 24, 48],
  "manual_labeling_candles_unchanged": true,
  "extra_lookahead_candles_fetched": 0,
  "extra_lookahead_data_usage": "..."
}
```

This records how the outcome fields were populated and confirms that the original manual labeling candles were not modified.

## Important Boundaries

The main script is for manual labeling.

The helper script is for objective outcome measurement after manual labeling.

The 24-hour `candles` array should be treated as the manual labeling session. Extra lookahead candles are only for measuring future outcomes and should not be used as part of the manual labeling window.

## Example Full Workflow

Generate the manual labeling template:

```bash
python v2.0_jsonschema.py "5/6/26 EDT" --symbol BTCUSDT --output ctx_BTCUSDT_20260506.json --no-chart
```

Manually fill relevant fields in:

- `price_action_levels`
- `auction_ranges`
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
- Events with missing or invalid `event_candle_idx` or `expected_direction` will remain unmeasured.

