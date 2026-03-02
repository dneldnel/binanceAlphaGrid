from __future__ import annotations

import sqlite3
import time

from core.helpers import bps_change
from core.models import AppConfig, SymbolState
from modules.execution import build_execution_engine
from modules.grid import GridEngine
from modules.quote import build_quote_engine
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

        self.universe = UniverseSelector(config)
        self.quote_engine = build_quote_engine(config)
        self.reference_engine = ReferencePriceEngine(config)
        self.grid_engine = GridEngine()
        self.risk = RiskManager(min_net_edge_bps=config.execution.min_net_edge_bps)
        self.execution = build_execution_engine(config)
        self.state_store = StateStore(config.runtime.state_store_path)
        self.reporter = Reporter(config)
        self.states = self._bootstrap_states()

    def run(self, iterations: int) -> int:
        iteration = 0
        while iterations == 0 or iteration < iterations:
            iteration += 1
            active_symbols = self.universe.select()
            kill_switch_active = self._refresh_kill_switch_state()

            for symbol in active_symbols:
                state = self.states[symbol.name]
                quote = self.quote_engine.next_quote(symbol, state)
                self.reference_engine.update(state, quote)
                if kill_switch_active:
                    state.status = "HALTED"
                    self.state_store.sync_symbol_state(state)
                    continue
                plan = self.grid_engine.build(symbol, state, quote)

                buy_target = self.grid_engine.find_buy_target(plan, quote)
                if buy_target is not None:
                    net_edge_bps = (
                        bps_change(state.reference_price, quote.exec_buy_price)
                        - self.config.execution.estimated_fee_bps
                    )
                    decision = self.risk.allow_buy(symbol, state, quote, buy_target, net_edge_bps)
                    if decision is not None:
                        pending_tx_id = None
                        try:
                            preview = self.execution.preview_buy(symbol, state, quote, decision)
                            if preview is not None:
                                pending_tx_id = self.state_store.create_pending_tx(
                                    symbol,
                                    decision,
                                    preview,
                                )
                                fill = self.execution.execute_buy(
                                    symbol,
                                    state,
                                    quote,
                                    decision,
                                    preview,
                                )
                                if fill is not None:
                                    self.reporter.record_fill(fill)
                                    self.state_store.record_fill(fill)
                                    if pending_tx_id is not None:
                                        self.state_store.mark_pending_tx_confirmed(
                                            pending_tx_id,
                                            fill.tx_hash,
                                        )
                                    self.state_store.sync_symbol_state(state)
                                    continue
                        except Exception as exc:
                            self._handle_execution_error(
                                symbol.name,
                                state,
                                side="buy",
                                error=exc,
                                pending_tx_id=pending_tx_id,
                            )
                            continue

                sell_target = self.grid_engine.find_sell_target(plan, quote)
                if sell_target is not None:
                    net_edge_bps = (
                        bps_change(quote.exec_sell_price, state.reference_price)
                        - self.config.execution.estimated_fee_bps
                    )
                    decision = self.risk.allow_sell(symbol, state, quote, sell_target, net_edge_bps)
                    if decision is not None:
                        pending_tx_id = None
                        try:
                            preview = self.execution.preview_sell(symbol, state, quote, decision)
                            if preview is not None:
                                pending_tx_id = self.state_store.create_pending_tx(
                                    symbol,
                                    decision,
                                    preview,
                                )
                                fill = self.execution.execute_sell(
                                    symbol,
                                    state,
                                    quote,
                                    decision,
                                    preview,
                                )
                                if fill is not None:
                                    self.reporter.record_fill(fill)
                                    self.state_store.record_fill(fill)
                                    if pending_tx_id is not None:
                                        self.state_store.mark_pending_tx_confirmed(
                                            pending_tx_id,
                                            fill.tx_hash,
                                        )
                                    self.state_store.sync_symbol_state(state)
                                    continue
                        except Exception as exc:
                            self._handle_execution_error(
                                symbol.name,
                                state,
                                side="sell",
                                error=exc,
                                pending_tx_id=pending_tx_id,
                            )
                            continue

                state.status = "IDLE"
                self.state_store.sync_symbol_state(state)

            ordered_states = [self.states[symbol.name] for symbol in active_symbols]
            self.reporter.render(iteration=iteration, states=ordered_states)

            if not self.no_sleep and self.config.runtime.refresh_interval_ms > 0:
                time.sleep(self.config.runtime.refresh_interval_ms / 1000.0)

        return 0

    def _bootstrap_states(self) -> dict[str, SymbolState]:
        states: dict[str, SymbolState] = {}

        for symbol in self.config.symbols.values():
            persisted = self.state_store.load_position(symbol.name)
            if persisted is None:
                state = self._build_initial_state(symbol)
                state.recent_logs.append("bootstrapped symbol state")
            else:
                state = self._restore_state(symbol, persisted)
                state.recent_logs.append("restored symbol state from sqlite")
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

    def _handle_execution_error(
        self,
        symbol_name: str,
        state: SymbolState,
        *,
        side: str,
        error: Exception,
        pending_tx_id: int | None,
    ) -> None:
        message = f"{side} execution failed: {error}"
        state.status = "ERROR"
        state.recent_logs.append(message)
        self.reporter.record_message(symbol_name, message)
        if pending_tx_id is not None:
            self.state_store.mark_pending_tx_failed(pending_tx_id, str(error))
        self.state_store.sync_symbol_state(state)
