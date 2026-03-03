# Binance Alpha Grid Bot

Runnable Python implementation of a Binance Alpha dual-side grid / market-making bot.

Current execution modes:

1. `dry-run`: simulated quote + simulated execution.
2. `paper`: real on-chain quote + dry-run execution.
3. `live`: real on-chain quote + real BSC transaction execution.

Current live scope:

1. Single-router `UniswapV2`-style quote / approve / swap flow.
2. Router ABI can be loaded from `config/live.toml` via `router.router_abi_path`.
3. Per-order notional cap and per-symbol position cap are wired into live preview risk checks.
4. SQLite persistence for positions, fills, pending tx, realized pnl, execution failures, and execution attempts.
5. Nonce-aware pending tx recovery first version using wallet `latest/pending nonce`, plus same-nonce replacement gas bump on retry.
6. Kill switch, preview risk checks, and symbol/global in-flight gating.

Known gaps before mainnet rollout:

1. There is no cancel tx strategy or txpool-based external replacement detection yet.
2. Honeypot / liquidity pause signals are still heuristic first versions, not a full data-plane risk feed.
3. Testnet -> mainnet read-only -> minimal live rollout checklist is still not documented in a dedicated runbook.

## Requirements

1. Python 3.11+
2. For `paper` / `live`, install optional live dependencies:

```bash
pip install -e '.[live]'
```

## Run

```bash
python main.py --config config/strategy.example.toml --iterations 5 --no-sleep
```

To run indefinitely:

```bash
python main.py --config config/strategy.example.toml --iterations 0
```

Run with explicit mode override:

```bash
python main.py --config config/live.toml --mode paper --iterations 5 --no-sleep
```

## Project Layout

1. `config/strategy.example.toml`: example strategy configuration
2. `config/live.toml`: BSC paper/live rollout template
3. `src/core`: config loader and data models
4. `src/modules`: strategy modules
5. `src/app.py`: application loop

## Notes

1. `dry-run` can run without `web3`.
2. `paper/live` require valid RPC, router, wallet, and route configuration.
3. `live` defaults remain guarded by `allow_live = false` and `allow_mainnet = false`.
