from __future__ import annotations

from typing import Protocol

from core.models import (
    AppConfig,
    ExecutionPreview,
    QuoteSnapshot,
    SymbolConfig,
    SymbolState,
    TradeDecision,
    TradeFill,
)
from evm import EvmRouterClient


class ExecutionEngine(Protocol):
    def preview_buy(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
    ) -> ExecutionPreview | None: ...

    def preview_sell(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
    ) -> ExecutionPreview | None: ...

    def execute_buy(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
        preview: ExecutionPreview,
    ) -> TradeFill | None: ...

    def execute_sell(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
        preview: ExecutionPreview,
    ) -> TradeFill | None: ...


def build_execution_engine(config: AppConfig) -> ExecutionEngine:
    if config.runtime.mode == "live":
        return LiveExecutionEngine(config)
    return DryRunExecutionEngine()


class BaseExecutionEngine:
    def _apply_buy_fill(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        decision: TradeDecision,
        preview: ExecutionPreview,
        *,
        message: str,
        tx_hash: str | None = None,
    ) -> TradeFill:
        previous_base = state.base_balance
        new_base = previous_base + preview.base_qty

        if previous_base > 0 and state.avg_cost_price > 0:
            state.avg_cost_price = (
                (previous_base * state.avg_cost_price) + (preview.base_qty * preview.price)
            ) / new_base
        else:
            state.avg_cost_price = preview.price

        state.base_balance = new_base
        state.quote_balance_usd -= preview.quote_value
        state.buy_done_count += 1
        state.daily_trade_count += 1
        state.status = "BOUGHT"

        return TradeFill(
            symbol=symbol.name,
            side="buy",
            level=decision.level,
            price=preview.price,
            base_qty=preview.base_qty,
            quote_value=preview.quote_value,
            realized_pnl=0.0,
            message=message,
            tx_hash=tx_hash,
        )

    def _apply_sell_fill(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        decision: TradeDecision,
        preview: ExecutionPreview,
        *,
        message: str,
        tx_hash: str | None = None,
    ) -> TradeFill:
        realized_pnl = 0.0
        if state.avg_cost_price > 0:
            realized_pnl = preview.quote_value - (preview.base_qty * state.avg_cost_price)

        state.base_balance -= preview.base_qty
        state.quote_balance_usd += preview.quote_value
        state.realized_pnl += realized_pnl
        state.sell_done_count += 1
        state.daily_trade_count += 1
        state.status = "SOLD"

        return TradeFill(
            symbol=symbol.name,
            side="sell",
            level=decision.level,
            price=preview.price,
            base_qty=preview.base_qty,
            quote_value=preview.quote_value,
            realized_pnl=realized_pnl,
            message=message,
            tx_hash=tx_hash,
        )


class DryRunExecutionEngine(BaseExecutionEngine):
    def preview_buy(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
    ) -> ExecutionPreview | None:
        quote_value = min(decision.order_quote_usd or 0.0, state.quote_balance_usd)
        fill_price = quote.exec_buy_price
        if quote_value <= 0 or fill_price <= 0:
            return None

        return ExecutionPreview(
            symbol=symbol.name,
            side="buy",
            level=decision.level,
            price=fill_price,
            base_qty=quote_value / fill_price,
            quote_value=quote_value,
        )

    def preview_sell(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
    ) -> ExecutionPreview | None:
        sellable_base = max(0.0, state.base_balance - symbol.inventory.reserve_base_tokens)
        base_qty = sellable_base * (decision.order_base_ratio or 0.0)
        fill_price = quote.exec_sell_price
        if base_qty <= 0 or fill_price <= 0:
            return None

        return ExecutionPreview(
            symbol=symbol.name,
            side="sell",
            level=decision.level,
            price=fill_price,
            base_qty=base_qty,
            quote_value=base_qty * fill_price,
        )

    def execute_buy(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
        preview: ExecutionPreview,
    ) -> TradeFill | None:
        if preview.side != "buy":
            raise ValueError("Buy execution requires a buy preview.")
        return self._apply_buy_fill(
            symbol,
            state,
            decision,
            preview,
            message=f"buy level {decision.level} filled at {preview.price:.6f}",
        )

    def execute_sell(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
        preview: ExecutionPreview,
    ) -> TradeFill | None:
        if preview.side != "sell":
            raise ValueError("Sell execution requires a sell preview.")
        return self._apply_sell_fill(
            symbol,
            state,
            decision,
            preview,
            message=f"sell level {decision.level} filled at {preview.price:.6f}",
        )


class LiveExecutionEngine(BaseExecutionEngine):
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.client = EvmRouterClient(config)

    def preview_buy(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
    ) -> ExecutionPreview | None:
        self.client.validate_symbol(symbol)
        quote_value = min(decision.order_quote_usd or 0.0, state.quote_balance_usd)
        if quote_value <= 0:
            return None

        quote_decimals = self.client.get_token_decimals(
            symbol.route.quote_token_address,
            symbol.route.quote_token_decimals,
        )
        base_decimals = self.client.get_token_decimals(
            symbol.route.base_token_address,
            symbol.route.base_token_decimals,
        )

        amount_in_raw = self.client.to_raw_amount(quote_value, quote_decimals)
        expected_out_raw = self.client.get_amounts_out(amount_in_raw, symbol.route.buy_path)[-1]
        base_qty = self.client.from_raw_amount(expected_out_raw, base_decimals)
        if base_qty <= 0:
            return None

        return ExecutionPreview(
            symbol=symbol.name,
            side="buy",
            level=decision.level,
            price=quote_value / base_qty,
            base_qty=base_qty,
            quote_value=quote_value,
            amount_in_raw=amount_in_raw,
            expected_out_raw=expected_out_raw,
            amount_out_min_raw=int(
                expected_out_raw * (1.0 - self.config.execution.slippage_bps / 10000.0)
            ),
            approve_token_address=symbol.route.quote_token_address,
            path=list(symbol.route.buy_path),
        )

    def preview_sell(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
    ) -> ExecutionPreview | None:
        self.client.validate_symbol(symbol)
        sellable_base = max(0.0, state.base_balance - symbol.inventory.reserve_base_tokens)
        base_qty = sellable_base * (decision.order_base_ratio or 0.0)
        if base_qty <= 0:
            return None

        quote_decimals = self.client.get_token_decimals(
            symbol.route.quote_token_address,
            symbol.route.quote_token_decimals,
        )
        base_decimals = self.client.get_token_decimals(
            symbol.route.base_token_address,
            symbol.route.base_token_decimals,
        )

        amount_in_raw = self.client.to_raw_amount(base_qty, base_decimals)
        expected_out_raw = self.client.get_amounts_out(amount_in_raw, symbol.route.sell_path)[-1]
        quote_value = self.client.from_raw_amount(expected_out_raw, quote_decimals)
        if quote_value <= 0:
            return None

        return ExecutionPreview(
            symbol=symbol.name,
            side="sell",
            level=decision.level,
            price=quote_value / base_qty,
            base_qty=base_qty,
            quote_value=quote_value,
            amount_in_raw=amount_in_raw,
            expected_out_raw=expected_out_raw,
            amount_out_min_raw=int(
                expected_out_raw * (1.0 - self.config.execution.slippage_bps / 10000.0)
            ),
            approve_token_address=symbol.route.base_token_address,
            path=list(symbol.route.sell_path),
        )

    def execute_buy(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
        preview: ExecutionPreview,
    ) -> TradeFill | None:
        if preview.side != "buy":
            raise ValueError("Buy execution requires a buy preview.")
        amount_in_raw, amount_out_min_raw, approve_token_address, path = self._require_router_preview(
            preview
        )
        base_decimals = self.client.get_token_decimals(
            symbol.route.base_token_address,
            symbol.route.base_token_decimals,
        )
        quote_decimals = self.client.get_token_decimals(
            symbol.route.quote_token_address,
            symbol.route.quote_token_decimals,
        )
        before_base_raw = self.client.get_token_balance_raw(symbol.route.base_token_address)
        before_quote_raw = self.client.get_token_balance_raw(symbol.route.quote_token_address)

        approve_tx_hash = self.client.ensure_allowance(approve_token_address, amount_in_raw)
        swap_tx_hash = self.client.swap_exact_tokens_for_tokens(
            amount_in_raw=amount_in_raw,
            amount_out_min_raw=amount_out_min_raw,
            path=path,
            side="buy",
        )
        tx_note = self._build_tx_note(approve_tx_hash, swap_tx_hash)
        actual_preview = self._buy_preview_from_balance_delta(
            symbol,
            preview,
            base_decimals=base_decimals,
            quote_decimals=quote_decimals,
            before_base_raw=before_base_raw,
            before_quote_raw=before_quote_raw,
        )

        return self._apply_buy_fill(
            symbol,
            state,
            decision,
            actual_preview,
            message=f"live buy level {decision.level} filled at {actual_preview.price:.6f} {tx_note}",
            tx_hash=swap_tx_hash,
        )

    def execute_sell(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
        preview: ExecutionPreview,
    ) -> TradeFill | None:
        if preview.side != "sell":
            raise ValueError("Sell execution requires a sell preview.")
        amount_in_raw, amount_out_min_raw, approve_token_address, path = self._require_router_preview(
            preview
        )
        base_decimals = self.client.get_token_decimals(
            symbol.route.base_token_address,
            symbol.route.base_token_decimals,
        )
        quote_decimals = self.client.get_token_decimals(
            symbol.route.quote_token_address,
            symbol.route.quote_token_decimals,
        )
        before_base_raw = self.client.get_token_balance_raw(symbol.route.base_token_address)
        before_quote_raw = self.client.get_token_balance_raw(symbol.route.quote_token_address)

        approve_tx_hash = self.client.ensure_allowance(approve_token_address, amount_in_raw)
        swap_tx_hash = self.client.swap_exact_tokens_for_tokens(
            amount_in_raw=amount_in_raw,
            amount_out_min_raw=amount_out_min_raw,
            path=path,
            side="sell",
        )
        tx_note = self._build_tx_note(approve_tx_hash, swap_tx_hash)
        actual_preview = self._sell_preview_from_balance_delta(
            symbol,
            preview,
            base_decimals=base_decimals,
            quote_decimals=quote_decimals,
            before_base_raw=before_base_raw,
            before_quote_raw=before_quote_raw,
        )

        return self._apply_sell_fill(
            symbol,
            state,
            decision,
            actual_preview,
            message=f"live sell level {decision.level} filled at {actual_preview.price:.6f} {tx_note}",
            tx_hash=swap_tx_hash,
        )

    def _require_router_preview(
        self,
        preview: ExecutionPreview,
    ) -> tuple[int, int, str, list[str]]:
        if (
            preview.amount_in_raw is None
            or preview.amount_out_min_raw is None
            or preview.approve_token_address is None
            or not preview.path
        ):
            raise ValueError("Live execution requires a router-backed preview.")
        return (
            preview.amount_in_raw,
            preview.amount_out_min_raw,
            preview.approve_token_address,
            list(preview.path),
        )

    def _build_tx_note(self, approve_tx_hash: str | None, swap_tx_hash: str) -> str:
        tx_note = f"tx={swap_tx_hash}"
        if approve_tx_hash:
            tx_note = f"approve={approve_tx_hash} {tx_note}"
        return tx_note

    def _buy_preview_from_balance_delta(
        self,
        symbol: SymbolConfig,
        preview: ExecutionPreview,
        *,
        base_decimals: int,
        quote_decimals: int,
        before_base_raw: int,
        before_quote_raw: int,
    ) -> ExecutionPreview:
        after_base_raw = self.client.get_token_balance_raw(symbol.route.base_token_address)
        after_quote_raw = self.client.get_token_balance_raw(symbol.route.quote_token_address)
        actual_base_qty = self.client.from_raw_amount(
            max(0, after_base_raw - before_base_raw),
            base_decimals,
        )
        actual_quote_value = self.client.from_raw_amount(
            max(0, before_quote_raw - after_quote_raw),
            quote_decimals,
        )
        if actual_base_qty <= 0 or actual_quote_value <= 0:
            return preview

        return ExecutionPreview(
            symbol=preview.symbol,
            side=preview.side,
            level=preview.level,
            price=actual_quote_value / actual_base_qty,
            base_qty=actual_base_qty,
            quote_value=actual_quote_value,
            amount_in_raw=preview.amount_in_raw,
            expected_out_raw=preview.expected_out_raw,
            amount_out_min_raw=preview.amount_out_min_raw,
            approve_token_address=preview.approve_token_address,
            path=list(preview.path),
        )

    def _sell_preview_from_balance_delta(
        self,
        symbol: SymbolConfig,
        preview: ExecutionPreview,
        *,
        base_decimals: int,
        quote_decimals: int,
        before_base_raw: int,
        before_quote_raw: int,
    ) -> ExecutionPreview:
        after_base_raw = self.client.get_token_balance_raw(symbol.route.base_token_address)
        after_quote_raw = self.client.get_token_balance_raw(symbol.route.quote_token_address)
        actual_base_qty = self.client.from_raw_amount(
            max(0, before_base_raw - after_base_raw),
            base_decimals,
        )
        actual_quote_value = self.client.from_raw_amount(
            max(0, after_quote_raw - before_quote_raw),
            quote_decimals,
        )
        if actual_base_qty <= 0 or actual_quote_value <= 0:
            return preview

        return ExecutionPreview(
            symbol=preview.symbol,
            side=preview.side,
            level=preview.level,
            price=actual_quote_value / actual_base_qty,
            base_qty=actual_base_qty,
            quote_value=actual_quote_value,
            amount_in_raw=preview.amount_in_raw,
            expected_out_raw=preview.expected_out_raw,
            amount_out_min_raw=preview.amount_out_min_raw,
            approve_token_address=preview.approve_token_address,
            path=list(preview.path),
        )
