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
6. First-version cancel strategy: after replacement budget is exhausted and `cancel_pending_after_sec` elapses, live mode can send a same-nonce self-transfer to replace a stuck tx.
7. Best-effort txpool / pending-block detection for same-wallet external replacement txs, including external cancel detection.
8. Mainnet rollout guard rails: live mainnet can independently gate buy/sell and enforce tighter rollout-only notional/position caps.
9. Kill switch, preview risk checks, and symbol/global in-flight gating.

Known gaps before mainnet rollout:

1. External replacement detection is still best-effort and depends on RPC support for txpool or pending-block data.
2. Honeypot / liquidity pause signals are still heuristic first versions, not a full data-plane risk feed.
3. The rollout runbook is documented; the current operator plan starts directly from minimal mainnet live, so first-pass validation risk is higher.

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

Run a rollout preflight without placing trades:

```bash
python main.py --config config/live.toml --preflight
```

## Project Layout

1. `config/strategy.example.toml`: example strategy configuration
2. `config/live.toml`: BSC paper/live rollout template
3. `config/testnet.live.toml`: BSC testnet stage-0 live validation template
4. `doc/bsc_mainnet_rollout_runbook.md`: staged rollout checklist and acceptance gates
5. `src/core`: config loader and data models
6. `src/modules`: strategy modules
7. `src/app.py`: application loop

## Notes

1. `dry-run` can run without `web3`.
2. `paper/live` require valid RPC, router, wallet, and route configuration; placeholder addresses and broken route endpoints now fail fast at startup.
3. `live` defaults remain guarded by `allow_live = false` and `allow_mainnet = false`.
4. On BSC mainnet live mode, `risk.mainnet_*` can keep trading one-sided and small while the base risk caps stay higher for paper validation.
5. Current rollout docs assume the next execution step is minimal mainnet live; testnet and mainnet read-only remain fallback paths.
6. `config/live.toml` already pins the PancakeSwap V2 BSC mainnet router; the remaining placeholders are wallet and symbol route values.
7. `--preflight` does read-only readiness checks for RPC, wallet, router, balances, allowance, nonce, kill switch, and route quotes.
