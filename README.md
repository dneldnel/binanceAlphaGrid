# Binance Alpha Grid Skeleton

Minimal runnable Python skeleton for a Binance Alpha dual-side grid / market-making bot.

The current implementation is intentionally `dry-run only`:

1. Loads strategy settings from TOML.
2. Simulates executable quotes for configured symbols.
3. Builds dynamic buy/sell grid levels.
4. Applies inventory skew and simple risk checks.
5. Simulates fills and prints a console dashboard.

## Requirements

1. Python 3.14+

## Run

```bash
python main.py --config config/strategy.example.toml --iterations 5 --no-sleep
```

To run indefinitely:

```bash
python main.py --config config/strategy.example.toml --iterations 0
```

## Project Layout

1. `config/strategy.example.toml`: example strategy configuration
2. `src/core`: config loader and data models
3. `src/modules`: strategy modules
4. `src/app.py`: application loop

## Notes

1. No external Python dependency is required.
2. Live chain execution is not implemented yet.
3. The current quote engine is simulated so the bot can run end-to-end locally.
