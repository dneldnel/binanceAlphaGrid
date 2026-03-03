from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone

from core.helpers import bps_change
from core.models import AppConfig, SymbolConfig, SymbolState, TradeDecision
from evm import EvmRouterClient
from modules.execution import ExecutionFailure, LiveExecutionEngine, build_execution_engine
from modules.grid import GridEngine
from modules.quote import QuoteSignalError, build_quote_engine
from modules.reference_price import ReferencePriceEngine
from modules.reporter import Reporter
from modules.risk import RiskManager
from modules.state_store import StateStore
from modules.universe import UniverseSelector


class Application:
    def __init__(self, config: AppConfig, *, no_sleep: bool = False) -> None:
        self.config = config
        self.no_sleep = no_sleep
        self._kill_switch_active = False
        self._consecutive_failed_tx_count = 0
        self._failed_tx_count_by_symbol = {
            symbol.name: 0
            for symbol in config.symbols.values()
        }
        self._trades_last_hour_by_symbol = {
            symbol.name: 0
            for symbol in config.symbols.values()
        }
        self._sticky_pause_reason_by_symbol = {
            symbol.name: ""
            for symbol in config.symbols.values()
        }
        self._last_risk_message_by_symbol = {
            symbol.name: ""
            for symbol in config.symbols.values()
        }

        self.universe = UniverseSelector(config)
        self.quote_engine = build_quote_engine(config)
        self.reference_engine = ReferencePriceEngine(config)
        self.grid_engine = GridEngine()
        self.risk = RiskManager(
            min_net_edge_bps=config.execution.min_net_edge_bps,
            max_price_impact_bps=config.market.max_price_impact_bps,
            max_gas_usd_per_tx=config.execution.max_gas_usd_per_tx,
            min_expected_profit_usd=config.execution.min_expected_profit_usd,
            max_notional_per_order=config.risk.max_notional_per_order,
            max_position_per_symbol_usd=config.risk.max_position_per_symbol_usd,
            max_daily_realized_loss_usd=config.risk.max_daily_realized_loss_usd,
            max_daily_gas_usd=config.risk.max_daily_gas_usd,
            max_consecutive_failed_tx=config.risk.max_consecutive_failed_tx,
            max_failed_tx_per_symbol=config.risk.max_failed_tx_per_symbol,
            max_trades_per_symbol_per_hour=config.risk.max_trades_per_symbol_per_hour,
        )
        self.execution = build_execution_engine(config)
        self.state_store = StateStore(config.runtime.state_store_path)
        self.reporter = Reporter(config)
        self.states = self._bootstrap_states()
        self._recover_pending_txs()

    def run(self, iterations: int) -> int:
        iteration = 0
        while iterations == 0 or iteration < iterations:
            iteration += 1
            self._recover_pending_txs()
            active_symbols = self.universe.select()
            kill_switch_active = self._refresh_kill_switch_state()

            for symbol in active_symbols:
                state = self.states[symbol.name]
                try:
                    self._process_symbol(symbol, state, kill_switch_active=kill_switch_active)
                except Exception as exc:
                    self._handle_symbol_loop_error(symbol.name, state, exc)

            ordered_states = [self.states[symbol.name] for symbol in active_symbols]
            self.reporter.render(iteration=iteration, states=ordered_states)

            if not self.no_sleep and self.config.runtime.refresh_interval_ms > 0:
                time.sleep(self.config.runtime.refresh_interval_ms / 1000.0)

        return 0

    def _bootstrap_states(self) -> dict[str, SymbolState]:
        states: dict[str, SymbolState] = {}
        chain_bootstrap_balances = self._load_chain_bootstrap_balances()

        for symbol in self.config.symbols.values():
            persisted = self.state_store.load_position(symbol.name)
            if persisted is None:
                state = self._build_initial_state(symbol)
                state.recent_logs.append("bootstrapped symbol state")
            else:
                state = self._restore_state(symbol, persisted)
                state.recent_logs.append("restored symbol state from sqlite")
            if chain_bootstrap_balances is not None:
                self._apply_chain_bootstrap_balances(
                    symbol,
                    state,
                    chain_bootstrap_balances[symbol.name],
                )
            self._refresh_windowed_symbol_metrics(symbol.name, state)
            state.recent_mid_prices.append(state.last_mid_price)
            states[symbol.name] = state
            self.state_store.sync_symbol_state(state)

        return states

    def _build_initial_state(self, symbol: SymbolConfig) -> SymbolState:
        initial_price = symbol.simulation.initial_price or 0.01
        starting_base = symbol.inventory.reserve_base_tokens + symbol.simulation.starting_base_units
        return SymbolState(
            symbol=symbol.name,
            status="BOOT",
            base_balance=starting_base,
            quote_balance_usd=min(
                symbol.simulation.starting_quote_usd,
                symbol.inventory.max_quote_per_symbol_usd,
            ),
            reserve_base_tokens=symbol.inventory.reserve_base_tokens,
            reference_price=initial_price,
            ema_price=initial_price,
            last_mid_price=initial_price,
            spread_bps=symbol.simulation.spread_bps or symbol.simulation.default_spread_bps,
            volatility_bps=0.0,
            buy_basis_price=initial_price,
            sell_basis_price=initial_price,
            avg_cost_price=initial_price,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_trade_count=0,
            buy_open_count=0,
            buy_done_count=0,
            sell_open_count=0,
            sell_done_count=0,
        )

    def _restore_state(self, symbol: SymbolConfig, persisted: sqlite3.Row) -> SymbolState:
        initial_price = symbol.simulation.initial_price or 0.01
        reference_price = float(persisted["reference_price"] or initial_price)
        last_mid_price = float(persisted["last_mid_price"] or reference_price)
        avg_cost_price = float(persisted["avg_cost_price"] or reference_price)
        return SymbolState(
            symbol=symbol.name,
            status=str(persisted["status"] or "RESTORED"),
            base_balance=float(persisted["base_balance"]),
            quote_balance_usd=float(persisted["quote_balance_usd"]),
            reserve_base_tokens=float(persisted["reserve_base_tokens"]),
            reference_price=reference_price,
            ema_price=reference_price,
            last_mid_price=last_mid_price,
            spread_bps=symbol.simulation.spread_bps or symbol.simulation.default_spread_bps,
            volatility_bps=0.0,
            buy_basis_price=reference_price,
            sell_basis_price=reference_price,
            avg_cost_price=avg_cost_price,
            realized_pnl=float(persisted["realized_pnl"]),
            unrealized_pnl=float(persisted["unrealized_pnl"]),
            daily_trade_count=int(persisted["daily_trade_count"]),
            buy_open_count=0,
            buy_done_count=int(persisted["buy_done_count"]),
            sell_open_count=0,
            sell_done_count=int(persisted["sell_done_count"]),
        )

    def _refresh_kill_switch_state(self) -> bool:
        kill_switch_path = self.config.risk.kill_switch_file
        active = bool(kill_switch_path and kill_switch_path.exists())
        if active != self._kill_switch_active:
            label = str(kill_switch_path) if kill_switch_path is not None else "(disabled)"
            if active:
                self.reporter.record_message("SYSTEM", f"kill switch active: {label}")
            else:
                self.reporter.record_message("SYSTEM", f"kill switch cleared: {label}")
            self._kill_switch_active = active
        return active

    def _load_chain_bootstrap_balances(self) -> dict[str, tuple[float, float]] | None:
        if self.config.runtime.mode not in {"paper", "live"}:
            return None

        client = EvmRouterClient(self.config, read_only=True)
        if client.wallet_address is None:
            raise RuntimeError("chain.wallet_address is required for paper/live balance sync.")

        token_balance_raw_cache: dict[str, int] = {}
        quote_token_totals: dict[str, float] = {}
        quote_groups: dict[str, list[SymbolConfig]] = {}
        base_balances: dict[str, float] = {}

        for symbol in self.config.symbols.values():
            client.validate_symbol(symbol)

            base_token_address = client.to_checksum(symbol.route.base_token_address)
            base_decimals = client.get_token_decimals(
                base_token_address,
                symbol.route.base_token_decimals,
            )
            if base_token_address not in token_balance_raw_cache:
                token_balance_raw_cache[base_token_address] = client.get_token_balance_raw(
                    base_token_address
                )
            base_balances[symbol.name] = client.from_raw_amount(
                token_balance_raw_cache[base_token_address],
                base_decimals,
            )

            quote_token_address = client.to_checksum(symbol.route.quote_token_address)
            quote_groups.setdefault(quote_token_address, []).append(symbol)
            if quote_token_address in quote_token_totals:
                continue

            quote_decimals = client.get_token_decimals(
                quote_token_address,
                symbol.route.quote_token_decimals,
            )
            if quote_token_address not in token_balance_raw_cache:
                token_balance_raw_cache[quote_token_address] = client.get_token_balance_raw(
                    quote_token_address
                )
            quote_token_totals[quote_token_address] = client.from_raw_amount(
                token_balance_raw_cache[quote_token_address],
                quote_decimals,
            )

        quote_allocations: dict[str, float] = {}
        for quote_token_address, grouped_symbols in quote_groups.items():
            total_quote_value = quote_token_totals.get(quote_token_address, 0.0)
            total_cap = sum(
                max(0.0, symbol.inventory.max_quote_per_symbol_usd)
                for symbol in grouped_symbols
            )
            if total_quote_value <= 0 or total_cap <= 0:
                for symbol in grouped_symbols:
                    quote_allocations[symbol.name] = 0.0
                continue

            remaining_quote_value = total_quote_value
            remaining_cap = total_cap
            ordered_symbols = sorted(grouped_symbols, key=lambda item: item.name)

            # Allocate a shared quote balance proportionally to each symbol cap so
            # the same wallet quote tokens are not counted multiple times.
            for index, symbol in enumerate(ordered_symbols):
                cap = max(0.0, symbol.inventory.max_quote_per_symbol_usd)
                if cap <= 0 or remaining_quote_value <= 0:
                    quote_allocations[symbol.name] = 0.0
                elif index == len(ordered_symbols) - 1 or remaining_cap <= 0:
                    quote_allocations[symbol.name] = min(cap, remaining_quote_value)
                else:
                    proportional_share = remaining_quote_value * (cap / remaining_cap)
                    quote_allocations[symbol.name] = min(cap, proportional_share)
                remaining_quote_value -= quote_allocations[symbol.name]
                remaining_cap -= cap

        return {
            symbol.name: (
                base_balances[symbol.name],
                quote_allocations.get(symbol.name, 0.0),
            )
            for symbol in self.config.symbols.values()
        }

    def _apply_chain_bootstrap_balances(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        balances: tuple[float, float],
    ) -> None:
        base_balance, quote_balance_usd = balances
        state.base_balance = base_balance
        state.quote_balance_usd = quote_balance_usd
        state.recent_logs.append(
            f"synced chain balances base={base_balance:.6f} quote={quote_balance_usd:.2f}"
        )

    def _recover_pending_txs(self) -> None:
        if self.config.runtime.mode != "live":
            return
        if not isinstance(self.execution, LiveExecutionEngine):
            return

        recoverable_rows = self.state_store.load_pending_txs(("prepared", "submitted", "retryable"))
        for row in recoverable_rows:
            try:
                self._recover_pending_tx_row(row)
            except Exception as exc:
                pending_tx_id = int(row["id"])
                symbol_name = str(row["symbol"])
                self.state_store.mark_pending_tx_orphaned(
                    pending_tx_id,
                    f"pending tx recovery failed: {exc}",
                )
                self.reporter.record_message(
                    symbol_name,
                    f"pending tx {pending_tx_id} orphaned during recovery: {exc}",
                )

    def _recover_pending_tx_row(self, pending_tx: sqlite3.Row) -> None:
        pending_tx_id = int(pending_tx["id"])
        symbol_name = str(pending_tx["symbol"])
        symbol = self.config.symbols.get(symbol_name)
        if symbol is None:
            self.state_store.mark_pending_tx_orphaned(
                pending_tx_id,
                f"unknown symbol during recovery: {symbol_name}",
            )
            return

        state = self.states[symbol.name]
        attempt_count = int(pending_tx["attempt_count"] or 0)
        if self._recover_swap_stage(symbol, state, pending_tx):
            return
        if self._recover_approve_stage(symbol, pending_tx):
            return
        if not self._pending_tx_retry_due(pending_tx):
            return

        if attempt_count >= 1 + max(0, self.config.execution.retry_times):
            self.state_store.mark_pending_tx_orphaned(
                pending_tx_id,
                f"retry budget exhausted at attempt_count={attempt_count}",
            )
            self.reporter.record_message(
                symbol.name,
                f"pending tx {pending_tx_id} orphaned: retry budget exhausted",
            )
            return

        execution_attempt_id = None
        try:
            self.state_store.mark_pending_tx_submitted(pending_tx_id)
            execution_attempt_id = self._record_pending_execution_attempt(
                symbol.name,
                str(pending_tx["side"]),
                pending_tx_id=pending_tx_id,
                estimated_gas_usd=pending_tx["estimated_gas_usd"],
            )
            fill = self.execution.retry_pending_tx(symbol, state, pending_tx)
        except Exception as exc:
            self._handle_execution_error(
                symbol.name,
                state,
                side=str(pending_tx["side"]),
                error=exc,
                pending_tx_id=pending_tx_id,
                execution_attempt_id=execution_attempt_id,
            )
            return

        if fill is None:
            return
        self.reporter.record_fill(fill)
        self.state_store.record_fill(fill)
        self.state_store.mark_pending_tx_confirmed(pending_tx_id, fill)
        self._sync_execution_attempt_result(
            execution_attempt_id=execution_attempt_id,
            pending_tx_id=pending_tx_id,
            approve_tx_hash=fill.approve_tx_hash,
            approve_nonce=fill.approve_nonce,
            approve_gas_price_wei=fill.approve_gas_price_wei,
            swap_tx_hash=fill.tx_hash,
            swap_nonce=fill.tx_nonce,
            swap_gas_price_wei=fill.tx_gas_price_wei,
        )
        self.state_store.sync_symbol_state(state)

    def _recover_swap_stage(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        pending_tx: sqlite3.Row,
    ) -> bool:
        pending_tx_id = int(pending_tx["id"])
        swap_tx_hash = str(pending_tx["swap_tx_hash"] or pending_tx["tx_hash"] or "").strip()
        swap_nonce = self._pending_tx_nonce(pending_tx, "swap_nonce")
        swap_gas_price_wei = self._pending_tx_gas_price_wei(pending_tx, "swap_gas_price_wei")
        approve_tx_hash = str(pending_tx["approve_tx_hash"] or "").strip() or None
        approve_nonce = self._pending_tx_nonce(pending_tx, "approve_nonce")
        approve_gas_price_wei = self._pending_tx_gas_price_wei(pending_tx, "approve_gas_price_wei")
        if not swap_tx_hash and swap_nonce is None:
            return False

        receipt_status = None
        if swap_tx_hash:
            receipt_status = self.execution.client.get_transaction_receipt_status(swap_tx_hash)
        if receipt_status is None:
            if swap_nonce is None:
                message = "swap tx missing nonce metadata for safe recovery"
                self.state_store.mark_pending_tx_orphaned(pending_tx_id, message)
                self._maybe_record_pending_message(symbol.name, pending_tx, message)
                return True

            latest_nonce, pending_nonce = self.execution.client.get_wallet_nonce_state()
            if latest_nonce > swap_nonce:
                message = (
                    f"swap nonce {swap_nonce} already consumed without receipt"
                )
                self.state_store.mark_pending_tx_orphaned(pending_tx_id, message)
                self._maybe_record_pending_message(symbol.name, pending_tx, message)
                return True
            if pending_nonce > swap_nonce:
                attempt_count = int(pending_tx["attempt_count"] or 0)
                retry_budget = 1 + max(0, self.config.execution.retry_times)
                if self._pending_tx_retry_due(pending_tx) and attempt_count < retry_budget:
                    return False
                message = f"swap nonce {swap_nonce} still pending on-chain"
                if attempt_count >= retry_budget:
                    message = f"{message}; replacement budget exhausted"
                self.state_store.mark_pending_tx_inflight(
                    pending_tx_id,
                    message,
                    approve_tx_hash=approve_tx_hash,
                    approve_nonce=approve_nonce,
                    approve_gas_price_wei=approve_gas_price_wei,
                    swap_tx_hash=swap_tx_hash or None,
                    swap_nonce=swap_nonce,
                    swap_gas_price_wei=swap_gas_price_wei,
                )
                self._maybe_record_pending_message(symbol.name, pending_tx, message)
                return True
            return False

        if receipt_status != 1:
            self._sync_execution_attempt_result(
                execution_attempt_id=None,
                pending_tx_id=pending_tx_id,
                approve_tx_hash=approve_tx_hash,
                approve_nonce=approve_nonce,
                approve_gas_price_wei=approve_gas_price_wei,
                swap_tx_hash=swap_tx_hash or None,
                swap_nonce=swap_nonce,
                swap_gas_price_wei=swap_gas_price_wei,
            )
            self.state_store.mark_pending_tx_failed(
                pending_tx_id,
                f"recovered tx receipt status={receipt_status}",
                approve_tx_hash=approve_tx_hash,
                approve_nonce=approve_nonce,
                approve_gas_price_wei=approve_gas_price_wei,
                swap_tx_hash=swap_tx_hash or None,
                swap_nonce=swap_nonce,
                swap_gas_price_wei=swap_gas_price_wei,
            )
            self.reporter.record_message(
                symbol.name,
                f"pending tx {pending_tx_id} failed on recovery: receipt status={receipt_status}",
            )
            return True

        fill = self.execution.confirm_pending_tx(symbol, state, pending_tx)
        if fill is None:
            self.state_store.mark_pending_tx_orphaned(
                pending_tx_id,
                "confirmed tx could not be replayed into state on startup",
            )
            self.reporter.record_message(
                symbol.name,
                f"pending tx {pending_tx_id} orphaned: could not replay confirmed tx",
            )
            return True

        self.reporter.record_fill(fill)
        self.state_store.record_fill(fill)
        self.state_store.mark_pending_tx_confirmed(pending_tx_id, fill)
        self._sync_execution_attempt_result(
            execution_attempt_id=None,
            pending_tx_id=pending_tx_id,
            approve_tx_hash=fill.approve_tx_hash,
            approve_nonce=fill.approve_nonce,
            approve_gas_price_wei=fill.approve_gas_price_wei,
            swap_tx_hash=fill.tx_hash,
            swap_nonce=fill.tx_nonce,
            swap_gas_price_wei=fill.tx_gas_price_wei,
        )
        self.state_store.sync_symbol_state(state)
        return True

    def _recover_approve_stage(
        self,
        symbol: SymbolConfig,
        pending_tx: sqlite3.Row,
    ) -> bool:
        pending_tx_id = int(pending_tx["id"])
        approve_tx_hash = str(pending_tx["approve_tx_hash"] or "").strip() or None
        approve_nonce = self._pending_tx_nonce(pending_tx, "approve_nonce")
        approve_gas_price_wei = self._pending_tx_gas_price_wei(pending_tx, "approve_gas_price_wei")
        if approve_tx_hash is None and approve_nonce is None:
            return False

        if self._pending_tx_allowance_satisfied(pending_tx):
            return False

        receipt_status = None
        if approve_tx_hash is not None:
            receipt_status = self.execution.client.get_transaction_receipt_status(approve_tx_hash)
        if receipt_status == 1:
            message = "approve confirmed but allowance is still insufficient"
            self._sync_execution_attempt_result(
                execution_attempt_id=None,
                pending_tx_id=pending_tx_id,
                approve_tx_hash=approve_tx_hash,
                approve_nonce=approve_nonce,
                approve_gas_price_wei=approve_gas_price_wei,
                swap_tx_hash=None,
                swap_nonce=None,
                swap_gas_price_wei=None,
            )
            self.state_store.mark_pending_tx_orphaned(pending_tx_id, message)
            self._maybe_record_pending_message(symbol.name, pending_tx, message)
            return True
        if receipt_status is not None and receipt_status != 1:
            self._sync_execution_attempt_result(
                execution_attempt_id=None,
                pending_tx_id=pending_tx_id,
                approve_tx_hash=approve_tx_hash,
                approve_nonce=approve_nonce,
                approve_gas_price_wei=approve_gas_price_wei,
                swap_tx_hash=None,
                swap_nonce=None,
                swap_gas_price_wei=None,
            )
            self.state_store.mark_pending_tx_failed(
                pending_tx_id,
                f"approve receipt status={receipt_status}",
                approve_tx_hash=approve_tx_hash,
                approve_nonce=approve_nonce,
                approve_gas_price_wei=approve_gas_price_wei,
            )
            self.reporter.record_message(
                symbol.name,
                f"pending tx {pending_tx_id} failed on recovery: approve receipt status={receipt_status}",
            )
            return True
        if approve_nonce is None:
            message = "approve tx missing nonce metadata for safe recovery"
            self.state_store.mark_pending_tx_orphaned(pending_tx_id, message)
            self._maybe_record_pending_message(symbol.name, pending_tx, message)
            return True

        latest_nonce, pending_nonce = self.execution.client.get_wallet_nonce_state()
        if latest_nonce > approve_nonce:
            message = f"approve nonce {approve_nonce} consumed without allowance increase"
            self.state_store.mark_pending_tx_orphaned(pending_tx_id, message)
            self._maybe_record_pending_message(symbol.name, pending_tx, message)
            return True
        if pending_nonce > approve_nonce:
            attempt_count = int(pending_tx["attempt_count"] or 0)
            retry_budget = 1 + max(0, self.config.execution.retry_times)
            if self._pending_tx_retry_due(pending_tx) and attempt_count < retry_budget:
                return False
            message = f"approve nonce {approve_nonce} still pending on-chain"
            if attempt_count >= retry_budget:
                message = f"{message}; replacement budget exhausted"
            self.state_store.mark_pending_tx_inflight(
                pending_tx_id,
                message,
                approve_tx_hash=approve_tx_hash,
                approve_nonce=approve_nonce,
                approve_gas_price_wei=approve_gas_price_wei,
            )
            self._maybe_record_pending_message(symbol.name, pending_tx, message)
            return True
        return False

    def _pending_tx_allowance_satisfied(self, pending_tx: sqlite3.Row) -> bool:
        approve_token_address = str(pending_tx["approve_token_address"] or "").strip()
        amount_in_raw = pending_tx["amount_in_raw"]
        if not approve_token_address or amount_in_raw is None:
            return False
        allowance_raw = self.execution.client.get_allowance_raw(approve_token_address)
        return allowance_raw >= int(amount_in_raw)

    def _pending_tx_retry_due(self, pending_tx: sqlite3.Row) -> bool:
        next_retry_at = str(pending_tx["next_retry_at"] or "").strip()
        if not next_retry_at:
            return True
        return datetime.now(timezone.utc) >= datetime.fromisoformat(next_retry_at)

    def _pending_tx_nonce(
        self,
        pending_tx: sqlite3.Row,
        field: str,
    ) -> int | None:
        value = pending_tx[field]
        if value is None:
            return None
        return int(value)

    def _pending_tx_gas_price_wei(
        self,
        pending_tx: sqlite3.Row,
        field: str,
    ) -> int | None:
        value = pending_tx[field]
        if value is None:
            return None
        return int(value)

    def _maybe_record_pending_message(
        self,
        symbol_name: str,
        pending_tx: sqlite3.Row,
        message: str,
    ) -> None:
        current = str(pending_tx["last_error"] or "").strip()
        if current == message:
            return
        pending_tx_id = int(pending_tx["id"])
        self.reporter.record_message(symbol_name, f"pending tx {pending_tx_id}: {message}")

    def _process_symbol(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        *,
        kill_switch_active: bool,
    ) -> None:
        self._refresh_windowed_symbol_metrics(symbol.name, state)
        sticky_pause_reason = self._sticky_pause_reason_by_symbol.get(symbol.name, "")
        if sticky_pause_reason:
            self._set_trade_pause_state(
                symbol.name,
                state,
                status="PAUSED",
                reason=sticky_pause_reason,
            )
            return
        quote = self.quote_engine.next_quote(symbol, state)
        self.reference_engine.update(state, quote)
        stale_quote_reason = self._stale_quote_reason(quote)
        if stale_quote_reason is not None:
            self._set_trade_pause_state(
                symbol.name,
                state,
                status="PAUSED",
                reason=stale_quote_reason,
            )
            return
        if kill_switch_active:
            state.status = "HALTED"
            self.state_store.sync_symbol_state(state)
            return
        hard_stop_decision = self._hard_stop_sell_decision(symbol, state, quote)
        if hard_stop_decision is not None:
            if self._attempt_sell_decision(
                symbol,
                state,
                quote,
                hard_stop_decision,
                bypass_preview_risk=True,
            ):
                return
        trade_pause = self._trade_pause_gate(symbol.name)
        if trade_pause is not None:
            status, reason = trade_pause
            self._set_trade_pause_state(symbol.name, state, status=status, reason=reason)
            return
        plan = self.grid_engine.build(symbol, state, quote)

        buy_target = self.grid_engine.find_buy_target(plan, quote)
        if buy_target is not None:
            net_edge_bps = (
                bps_change(state.reference_price, quote.exec_buy_price)
                - self.config.execution.estimated_fee_bps
            )
            decision = self.risk.allow_buy(
                symbol,
                state,
                quote,
                buy_target,
                net_edge_bps,
                trades_per_symbol_last_hour=self._trades_last_hour_by_symbol[symbol.name],
            )
            if decision is not None:
                if self._attempt_buy_decision(symbol, state, quote, decision):
                    return

        sell_target = self.grid_engine.find_sell_target(plan, quote)
        if sell_target is not None:
            net_edge_bps = (
                bps_change(quote.exec_sell_price, state.reference_price)
                - self.config.execution.estimated_fee_bps
            )
            decision = self.risk.allow_sell(
                symbol,
                state,
                quote,
                sell_target,
                net_edge_bps,
                trades_per_symbol_last_hour=self._trades_last_hour_by_symbol[symbol.name],
            )
            if decision is not None:
                if self._attempt_sell_decision(symbol, state, quote, decision):
                    return

        state.status = "IDLE"
        self.state_store.sync_symbol_state(state)

    def _handle_execution_error(
        self,
        symbol_name: str,
        state: SymbolState,
        *,
        side: str,
        error: Exception,
        pending_tx_id: int | None,
        execution_attempt_id: int | None,
    ) -> None:
        message = f"{side} execution failed: {error}"
        if pending_tx_id is not None:
            self._increment_execution_failure_counters(symbol_name)
            self.state_store.record_execution_failure(
                symbol=symbol_name,
                side=side,
                message=str(error),
                pending_tx_id=pending_tx_id,
                )
        state.status = "ERROR"
        state.recent_logs.append(message)
        self._last_risk_message_by_symbol[symbol_name] = ""
        self.reporter.record_message(symbol_name, message)
        if pending_tx_id is not None:
            (
                approve_tx_hash,
                swap_tx_hash,
                approve_nonce,
                swap_nonce,
                approve_gas_price_wei,
                swap_gas_price_wei,
            ) = (
                self._extract_execution_tx_metadata(error)
            )
            self._sync_execution_attempt_result(
                execution_attempt_id=execution_attempt_id,
                pending_tx_id=pending_tx_id,
                approve_tx_hash=approve_tx_hash,
                approve_nonce=approve_nonce,
                approve_gas_price_wei=approve_gas_price_wei,
                swap_tx_hash=swap_tx_hash,
                swap_nonce=swap_nonce,
                swap_gas_price_wei=swap_gas_price_wei,
            )
            if self._is_retryable_execution_error(error):
                self.state_store.mark_pending_tx_retryable(
                    pending_tx_id,
                    str(error),
                    next_retry_at=self._next_pending_tx_retry_at(),
                    approve_tx_hash=approve_tx_hash,
                    approve_nonce=approve_nonce,
                    approve_gas_price_wei=approve_gas_price_wei,
                    swap_tx_hash=swap_tx_hash,
                    swap_nonce=swap_nonce,
                    swap_gas_price_wei=swap_gas_price_wei,
                )
            else:
                self.state_store.mark_pending_tx_failed(
                    pending_tx_id,
                    str(error),
                    approve_tx_hash=approve_tx_hash,
                    approve_nonce=approve_nonce,
                    approve_gas_price_wei=approve_gas_price_wei,
                    swap_tx_hash=swap_tx_hash,
                    swap_nonce=swap_nonce,
                    swap_gas_price_wei=swap_gas_price_wei,
                )
        pause_signal = self._pause_signal_from_error(error)
        if pause_signal is not None:
            self._activate_pause_signal(
                symbol_name,
                state,
                reason=pause_signal[1],
                sticky=pause_signal[2],
            )
            return
        self.state_store.sync_symbol_state(state)

    def _handle_symbol_loop_error(
        self,
        symbol_name: str,
        state: SymbolState,
        error: Exception,
    ) -> None:
        pause_signal = self._pause_signal_from_error(error)
        if pause_signal is not None:
            self._activate_pause_signal(
                symbol_name,
                state,
                reason=pause_signal[1],
                sticky=pause_signal[2],
            )
            return
        message = f"symbol loop failed: {error}"
        state.status = "ERROR"
        state.recent_logs.append(message)
        self.reporter.record_message(symbol_name, message)
        self.state_store.sync_symbol_state(state)

    def _extract_execution_tx_metadata(
        self,
        error: Exception,
    ) -> tuple[str | None, str | None, int | None, int | None, int | None, int | None]:
        if isinstance(error, ExecutionFailure):
            return (
                error.approve_tx_hash,
                error.swap_tx_hash,
                error.approve_nonce,
                error.swap_nonce,
                error.approve_gas_price_wei,
                error.swap_gas_price_wei,
            )
        return None, None, None, None, None, None

    def _is_retryable_execution_error(self, error: Exception) -> bool:
        if self.config.execution.retry_times <= 0:
            return False

        text = str(error).lower()
        retryable_markers = (
            "timeout",
            "timed out",
            "nonce too low",
            "replacement transaction underpriced",
            "already known",
            "temporarily unavailable",
            "connection reset",
            "failed waiting for receipt",
        )
        return any(marker in text for marker in retryable_markers)

    def _next_pending_tx_retry_at(self) -> str | None:
        if self.config.execution.retry_backoff_ms <= 0:
            return None
        return (
            datetime.now(timezone.utc) + timedelta(milliseconds=self.config.execution.retry_backoff_ms)
        ).isoformat(timespec="seconds")

    def _trade_pause_gate(self, symbol_name: str) -> tuple[str, str] | None:
        symbol_open_pending = self.state_store.count_open_pending_txs(symbol=symbol_name)
        if self.config.execution.single_symbol_single_inflight and symbol_open_pending > 0:
            return (
                "PAUSED",
                f"new trades paused: symbol in-flight tx count {symbol_open_pending} >= 1",
            )

        global_open_pending = self.state_store.count_open_pending_txs()
        if (
            self.config.execution.max_inflight_txs > 0
            and global_open_pending >= self.config.execution.max_inflight_txs
        ):
            return (
                "PAUSED",
                "new trades paused: "
                f"global in-flight tx count {global_open_pending} >= "
                f"{self.config.execution.max_inflight_txs}",
            )

        global_reason = self.risk.global_trade_pause_reason(
            global_realized_pnl=self._global_daily_realized_pnl(),
            global_daily_gas_usd=self._global_daily_gas_usd(),
            consecutive_failed_tx_count=self._consecutive_failed_tx_count,
        )
        if global_reason is not None:
            return "HALTED", f"new trades halted: {global_reason}"

        symbol_reason = self.risk.symbol_trade_pause_reason(
            failed_tx_count_for_symbol=self._failed_tx_count_by_symbol.get(symbol_name, 0),
        )
        if symbol_reason is not None:
            return "PAUSED", f"new trades paused: {symbol_reason}"
        return None

    def _global_realized_pnl(self) -> float:
        return sum(state.realized_pnl for state in self.states.values())

    def _global_daily_realized_pnl(self) -> float:
        return self.state_store.sum_realized_pnl_since(self._utc_day_start_iso())

    def _global_daily_gas_usd(self) -> float:
        return self.state_store.sum_execution_attempt_gas_since(self._utc_day_start_iso())

    def _increment_execution_failure_counters(self, symbol_name: str) -> None:
        self._consecutive_failed_tx_count += 1
        self._failed_tx_count_by_symbol[symbol_name] = (
            self._failed_tx_count_by_symbol.get(symbol_name, 0) + 1
        )

    def _reset_consecutive_failure_counter(self, symbol_name: str) -> None:
        self._consecutive_failed_tx_count = 0
        self._last_risk_message_by_symbol[symbol_name] = ""

    def _set_trade_pause_state(
        self,
        symbol_name: str,
        state: SymbolState,
        *,
        status: str,
        reason: str,
    ) -> None:
        state.status = status
        if self._last_risk_message_by_symbol.get(symbol_name) != reason:
            state.recent_logs.append(reason)
            self.reporter.record_message(symbol_name, reason)
            self._last_risk_message_by_symbol[symbol_name] = reason
        self.state_store.sync_symbol_state(state)

    def _record_preview_block(
        self,
        symbol_name: str,
        state: SymbolState,
        *,
        side: str,
        reason: str,
    ) -> None:
        self._set_trade_pause_state(
            symbol_name,
            state,
            status="PAUSED",
            reason=f"{side} preview blocked: {reason}",
        )

    def _attempt_buy_decision(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
    ) -> bool:
        pending_tx_id = None
        execution_attempt_id = None
        try:
            preview = self.execution.preview_buy(symbol, state, quote, decision)
            if preview is None:
                return False
            preview_violation = self.risk.preview_violation_reason(preview, state=state)
            if preview_violation is not None:
                self._record_preview_block(
                    symbol.name,
                    state,
                    side="buy",
                    reason=preview_violation,
                )
                return True
            pending_tx_id = self.state_store.create_pending_tx(
                symbol,
                decision,
                preview,
            )
            if pending_tx_id is not None:
                self.state_store.mark_pending_tx_submitted(pending_tx_id)
            execution_attempt_id = self._record_execution_attempt(
                symbol.name,
                "buy",
                preview,
                pending_tx_id=pending_tx_id,
            )
            fill = self.execution.execute_buy(
                symbol,
                state,
                quote,
                decision,
                preview,
            )
        except Exception as exc:
            self._handle_execution_error(
                symbol.name,
                state,
                side="buy",
                error=exc,
                pending_tx_id=pending_tx_id,
                execution_attempt_id=execution_attempt_id,
            )
            return True

        if fill is None:
            return False
        self._reset_consecutive_failure_counter(symbol.name)
        self.reporter.record_fill(fill)
        self.state_store.record_fill(fill)
        if pending_tx_id is not None:
            self.state_store.mark_pending_tx_confirmed(pending_tx_id, fill)
        self._sync_execution_attempt_result(
            execution_attempt_id=execution_attempt_id,
            pending_tx_id=pending_tx_id,
            approve_tx_hash=fill.approve_tx_hash,
            approve_nonce=fill.approve_nonce,
            approve_gas_price_wei=fill.approve_gas_price_wei,
            swap_tx_hash=fill.tx_hash,
            swap_nonce=fill.tx_nonce,
            swap_gas_price_wei=fill.tx_gas_price_wei,
        )
        self.state_store.sync_symbol_state(state)
        return True

    def _attempt_sell_decision(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
        *,
        bypass_preview_risk: bool = False,
    ) -> bool:
        pending_tx_id = None
        execution_attempt_id = None
        try:
            preview = self.execution.preview_sell(symbol, state, quote, decision)
            if preview is None:
                return False
            if not bypass_preview_risk:
                preview_violation = self.risk.preview_violation_reason(preview, state=state)
                if preview_violation is not None:
                    self._record_preview_block(
                        symbol.name,
                        state,
                        side="sell",
                        reason=preview_violation,
                    )
                    return True
            pending_tx_id = self.state_store.create_pending_tx(
                symbol,
                decision,
                preview,
            )
            if pending_tx_id is not None:
                self.state_store.mark_pending_tx_submitted(pending_tx_id)
            execution_attempt_id = self._record_execution_attempt(
                symbol.name,
                "sell",
                preview,
                pending_tx_id=pending_tx_id,
            )
            fill = self.execution.execute_sell(
                symbol,
                state,
                quote,
                decision,
                preview,
            )
        except Exception as exc:
            self._handle_execution_error(
                symbol.name,
                state,
                side="sell",
                error=exc,
                pending_tx_id=pending_tx_id,
                execution_attempt_id=execution_attempt_id,
            )
            return True

        if fill is None:
            return False
        self._reset_consecutive_failure_counter(symbol.name)
        self.reporter.record_fill(fill)
        self.state_store.record_fill(fill)
        if pending_tx_id is not None:
            self.state_store.mark_pending_tx_confirmed(pending_tx_id, fill)
        self._sync_execution_attempt_result(
            execution_attempt_id=execution_attempt_id,
            pending_tx_id=pending_tx_id,
            approve_tx_hash=fill.approve_tx_hash,
            approve_nonce=fill.approve_nonce,
            approve_gas_price_wei=fill.approve_gas_price_wei,
            swap_tx_hash=fill.tx_hash,
            swap_nonce=fill.tx_nonce,
            swap_gas_price_wei=fill.tx_gas_price_wei,
        )
        self.state_store.sync_symbol_state(state)
        return True

    def _record_execution_attempt(
        self,
        symbol_name: str,
        side: str,
        preview: object,
        *,
        pending_tx_id: int | None,
    ) -> int | None:
        estimated_gas_usd = getattr(preview, "estimated_gas_usd", None)
        amount_in_raw = getattr(preview, "amount_in_raw", None)
        if estimated_gas_usd is None or amount_in_raw is None or estimated_gas_usd <= 0:
            return None
        return self.state_store.record_execution_attempt(
            symbol=symbol_name,
            side=side,
            estimated_gas_usd=estimated_gas_usd,
            pending_tx_id=pending_tx_id,
        )

    def _record_pending_execution_attempt(
        self,
        symbol_name: str,
        side: str,
        *,
        pending_tx_id: int | None,
        estimated_gas_usd: object,
    ) -> int | None:
        if estimated_gas_usd is None:
            return None
        value = float(estimated_gas_usd)
        if value <= 0:
            return None
        return self.state_store.record_execution_attempt(
            symbol=symbol_name,
            side=side,
            estimated_gas_usd=value,
            pending_tx_id=pending_tx_id,
        )

    def _sync_execution_attempt_result(
        self,
        *,
        execution_attempt_id: int | None,
        pending_tx_id: int | None,
        approve_tx_hash: str | None,
        approve_nonce: int | None,
        approve_gas_price_wei: int | None,
        swap_tx_hash: str | None,
        swap_nonce: int | None,
        swap_gas_price_wei: int | None,
    ) -> None:
        actual_gas_usd = self._actual_execution_gas_usd(
            approve_tx_hash=approve_tx_hash,
            swap_tx_hash=swap_tx_hash,
        )
        if execution_attempt_id is not None:
            self.state_store.update_execution_attempt_result(
                execution_attempt_id,
                approve_tx_hash=approve_tx_hash,
                approve_nonce=approve_nonce,
                approve_gas_price_wei=approve_gas_price_wei,
                swap_tx_hash=swap_tx_hash,
                swap_nonce=swap_nonce,
                swap_gas_price_wei=swap_gas_price_wei,
                actual_gas_usd=actual_gas_usd,
            )
            return
        if pending_tx_id is not None:
            self.state_store.update_latest_execution_attempt_for_pending_tx(
                pending_tx_id,
                approve_tx_hash=approve_tx_hash,
                approve_nonce=approve_nonce,
                approve_gas_price_wei=approve_gas_price_wei,
                swap_tx_hash=swap_tx_hash,
                swap_nonce=swap_nonce,
                swap_gas_price_wei=swap_gas_price_wei,
                actual_gas_usd=actual_gas_usd,
            )

    def _actual_execution_gas_usd(
        self,
        *,
        approve_tx_hash: str | None,
        swap_tx_hash: str | None,
    ) -> float | None:
        if self.config.runtime.mode != "live":
            return None
        if not isinstance(self.execution, LiveExecutionEngine):
            return None

        tx_hashes: list[str] = []
        if approve_tx_hash:
            tx_hashes.append(approve_tx_hash)
        if swap_tx_hash and swap_tx_hash not in tx_hashes:
            tx_hashes.append(swap_tx_hash)
        if not tx_hashes:
            return None

        total = 0.0
        resolved = False
        for tx_hash in tx_hashes:
            try:
                gas_cost_usd = self.execution.client.get_transaction_gas_cost_usd(tx_hash)
            except Exception:
                gas_cost_usd = None
            if gas_cost_usd is None:
                continue
            total += gas_cost_usd
            resolved = True
        if not resolved:
            return None
        return total

    def _stale_quote_reason(self, quote: QuoteSnapshot) -> str | None:
        age_sec = (datetime.now(timezone.utc) - quote.ts).total_seconds()
        if age_sec <= self.config.market.stale_quote_sec:
            return None
        return (
            "new trades paused: stale quote "
            f"{age_sec:.1f}s > {self.config.market.stale_quote_sec}s"
        )

    def _hard_stop_sell_decision(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
    ) -> TradeDecision | None:
        if self.config.risk.hard_stop_from_cost_bps <= 0:
            return None
        if state.base_balance <= 0 or state.avg_cost_price <= 0 or quote.exec_sell_price <= 0:
            return None

        sellable_base = state.base_balance
        if not symbol.inventory.allow_emergency_sell_reserve:
            sellable_base = max(0.0, state.base_balance - symbol.inventory.reserve_base_tokens)
        if sellable_base <= 0:
            return None

        drawdown_bps = -bps_change(quote.exec_sell_price, state.avg_cost_price)
        if drawdown_bps < self.config.risk.hard_stop_from_cost_bps:
            return None

        return TradeDecision(
            symbol=symbol.name,
            side="sell",
            level=0,
            target_price=quote.exec_sell_price,
            order_base_ratio=1.0,
            reason=f"hard stop from cost {drawdown_bps:.1f}bps",
            allow_reserve_sell=symbol.inventory.allow_emergency_sell_reserve,
            force_exit=True,
        )

    def _pause_signal_from_error(
        self,
        error: Exception,
    ) -> tuple[str, str, bool] | None:
        if isinstance(error, QuoteSignalError):
            if error.signal_kind == "liquidity_drop" and self.config.risk.pause_on_liquidity_drop:
                return (
                    "liquidity_drop",
                    f"new trades paused: {error}",
                    False,
                )
            if error.signal_kind == "route_failure" and self.config.risk.pause_on_route_failure:
                return (
                    "route_failure",
                    f"new trades paused: {error}",
                    False,
                )

        if self.config.risk.pause_on_honeypot_signal:
            text = str(error).lower()
            honeypot_markers = (
                "honeypot",
                "transferhelper",
                "transfer failed",
                "transfer_from_failed",
                "insufficient output amount",
                "fee on transfer",
                "token transfer failed",
                "tax",
            )
            if any(marker in text for marker in honeypot_markers):
                return (
                    "honeypot_signal",
                    f"new trades paused: honeypot signal: {error}",
                    True,
                )

        return None

    def _activate_pause_signal(
        self,
        symbol_name: str,
        state: SymbolState,
        *,
        reason: str,
        sticky: bool,
    ) -> None:
        if sticky:
            self._sticky_pause_reason_by_symbol[symbol_name] = reason
        self._set_trade_pause_state(
            symbol_name,
            state,
            status="PAUSED",
            reason=reason,
        )

    def _refresh_windowed_symbol_metrics(self, symbol_name: str, state: SymbolState) -> None:
        day_start_iso = self._utc_day_start_iso()
        hour_start_iso = self._utc_hour_ago_iso()
        state.daily_trade_count = self.state_store.count_fills_for_symbol_since(
            symbol_name,
            day_start_iso,
        )
        self._trades_last_hour_by_symbol[symbol_name] = self.state_store.count_fills_for_symbol_since(
            symbol_name,
            hour_start_iso,
        )
        self._failed_tx_count_by_symbol[symbol_name] = (
            self.state_store.count_execution_failures_for_symbol_since(
                symbol_name,
                day_start_iso,
            )
        )

    def _utc_day_start_iso(self) -> str:
        now = datetime.now(timezone.utc)
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")

    def _utc_hour_ago_iso(self) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
