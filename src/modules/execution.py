from __future__ import annotations

import json
from typing import Any, Protocol

from core.helpers import bps_change
from core.models import (
    AppConfig,
    ExecutionPreview,
    QuoteSnapshot,
    SymbolConfig,
    SymbolState,
    TradeDecision,
    TradeFill,
)
from evm import EvmRouterClient, TransactionSendError
from modules.quote import QuoteSignalError


class ExecutionFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        approve_tx_hash: str | None = None,
        swap_tx_hash: str | None = None,
        approve_nonce: int | None = None,
        swap_nonce: int | None = None,
        approve_gas_price_wei: int | None = None,
        swap_gas_price_wei: int | None = None,
    ) -> None:
        super().__init__(message)
        self.approve_tx_hash = approve_tx_hash
        self.swap_tx_hash = swap_tx_hash
        self.approve_nonce = approve_nonce
        self.swap_nonce = swap_nonce
        self.approve_gas_price_wei = approve_gas_price_wei
        self.swap_gas_price_wei = swap_gas_price_wei


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
    def _sellable_base_for_decision(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        decision: TradeDecision,
    ) -> float:
        reserve_base = 0.0 if decision.allow_reserve_sell else symbol.inventory.reserve_base_tokens
        return max(0.0, state.base_balance - reserve_base)

    def _build_preview(
        self,
        *,
        symbol: str,
        side: str,
        level: int,
        price: float,
        base_qty: float,
        quote_value: float,
        state: SymbolState,
        quote: QuoteSnapshot,
        estimated_gas_usd: float | None = None,
        amount_in_raw: int | None = None,
        expected_out_raw: int | None = None,
        amount_out_min_raw: int | None = None,
        approve_token_address: str | None = None,
        path: list[str] | None = None,
    ) -> ExecutionPreview:
        return ExecutionPreview(
            symbol=symbol,
            side=side,
            level=level,
            price=price,
            base_qty=base_qty,
            quote_value=quote_value,
            price_impact_bps=self._preview_price_impact_bps(quote=quote, side=side, price=price),
            expected_profit_usd=self._expected_profit_usd(
                state=state,
                side=side,
                price=price,
                base_qty=base_qty,
                estimated_gas_usd=estimated_gas_usd,
            ),
            estimated_gas_usd=estimated_gas_usd,
            amount_in_raw=amount_in_raw,
            expected_out_raw=expected_out_raw,
            amount_out_min_raw=amount_out_min_raw,
            approve_token_address=approve_token_address,
            path=list(path or []),
        )

    def _preview_price_impact_bps(
        self,
        *,
        quote: QuoteSnapshot,
        side: str,
        price: float,
    ) -> float:
        if quote.mid_price <= 0 or price <= 0:
            return 0.0
        deviation_bps = bps_change(price, quote.mid_price)
        if side == "buy":
            return max(0.0, deviation_bps)
        return max(0.0, -deviation_bps)

    def _expected_profit_usd(
        self,
        *,
        state: SymbolState,
        side: str,
        price: float,
        base_qty: float,
        estimated_gas_usd: float | None,
    ) -> float:
        if state.reference_price <= 0 or price <= 0 or base_qty <= 0:
            return 0.0
        if side == "buy":
            gross_edge_usd = (state.reference_price - price) * base_qty
        else:
            gross_edge_usd = (price - state.reference_price) * base_qty
        return gross_edge_usd - (estimated_gas_usd or 0.0)

    def _apply_buy_fill(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        decision: TradeDecision,
        preview: ExecutionPreview,
        *,
        message: str,
        tx_hash: str | None = None,
        approve_tx_hash: str | None = None,
        tx_nonce: int | None = None,
        approve_nonce: int | None = None,
        tx_gas_price_wei: int | None = None,
        approve_gas_price_wei: int | None = None,
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
            approve_tx_hash=approve_tx_hash,
            tx_nonce=tx_nonce,
            approve_nonce=approve_nonce,
            tx_gas_price_wei=tx_gas_price_wei,
            approve_gas_price_wei=approve_gas_price_wei,
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
        approve_tx_hash: str | None = None,
        tx_nonce: int | None = None,
        approve_nonce: int | None = None,
        tx_gas_price_wei: int | None = None,
        approve_gas_price_wei: int | None = None,
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
            approve_tx_hash=approve_tx_hash,
            tx_nonce=tx_nonce,
            approve_nonce=approve_nonce,
            tx_gas_price_wei=tx_gas_price_wei,
            approve_gas_price_wei=approve_gas_price_wei,
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

        return self._build_preview(
            symbol=symbol.name,
            side="buy",
            level=decision.level,
            price=fill_price,
            base_qty=quote_value / fill_price,
            quote_value=quote_value,
            state=state,
            quote=quote,
        )

    def preview_sell(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        decision: TradeDecision,
    ) -> ExecutionPreview | None:
        sellable_base = self._sellable_base_for_decision(symbol, state, decision)
        base_qty = sellable_base * (decision.order_base_ratio or 0.0)
        fill_price = quote.exec_sell_price
        if base_qty <= 0 or fill_price <= 0:
            return None

        return self._build_preview(
            symbol=symbol.name,
            side="sell",
            level=decision.level,
            price=fill_price,
            base_qty=base_qty,
            quote_value=base_qty * fill_price,
            state=state,
            quote=quote,
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
        try:
            expected_out_raw = self.client.get_amounts_out(amount_in_raw, symbol.route.buy_path)[-1]
        except Exception as exc:
            raise QuoteSignalError(
                "route_failure",
                f"{symbol.name}: buy preview route failed: {exc}",
            ) from exc
        base_qty = self.client.from_raw_amount(expected_out_raw, base_decimals)
        if base_qty <= 0:
            raise QuoteSignalError(
                "liquidity_drop",
                f"{symbol.name}: buy preview returned zero base output",
            )
        amount_out_min_raw = int(expected_out_raw * (1.0 - self.config.execution.slippage_bps / 10000.0))
        try:
            estimated_gas_usd = self.client.estimate_swap_bundle_gas_cost_usd(
                approve_token_address=symbol.route.quote_token_address,
                min_approve_amount_raw=amount_in_raw,
                amount_in_raw=amount_in_raw,
                amount_out_min_raw=amount_out_min_raw,
                path=list(symbol.route.buy_path),
                side="buy",
            )
        except Exception:
            estimated_gas_usd = None

        return self._build_preview(
            symbol=symbol.name,
            side="buy",
            level=decision.level,
            price=quote_value / base_qty,
            base_qty=base_qty,
            quote_value=quote_value,
            state=state,
            quote=quote,
            estimated_gas_usd=estimated_gas_usd,
            amount_in_raw=amount_in_raw,
            expected_out_raw=expected_out_raw,
            amount_out_min_raw=amount_out_min_raw,
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
        sellable_base = self._sellable_base_for_decision(symbol, state, decision)
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
        try:
            expected_out_raw = self.client.get_amounts_out(amount_in_raw, symbol.route.sell_path)[-1]
        except Exception as exc:
            raise QuoteSignalError(
                "route_failure",
                f"{symbol.name}: sell preview route failed: {exc}",
            ) from exc
        quote_value = self.client.from_raw_amount(expected_out_raw, quote_decimals)
        if quote_value <= 0:
            raise QuoteSignalError(
                "liquidity_drop",
                f"{symbol.name}: sell preview returned zero quote output",
            )
        amount_out_min_raw = int(expected_out_raw * (1.0 - self.config.execution.slippage_bps / 10000.0))
        try:
            estimated_gas_usd = self.client.estimate_swap_bundle_gas_cost_usd(
                approve_token_address=symbol.route.base_token_address,
                min_approve_amount_raw=amount_in_raw,
                amount_in_raw=amount_in_raw,
                amount_out_min_raw=amount_out_min_raw,
                path=list(symbol.route.sell_path),
                side="sell",
            )
        except Exception:
            estimated_gas_usd = None

        return self._build_preview(
            symbol=symbol.name,
            side="sell",
            level=decision.level,
            price=quote_value / base_qty,
            base_qty=base_qty,
            quote_value=quote_value,
            state=state,
            quote=quote,
            estimated_gas_usd=estimated_gas_usd,
            amount_in_raw=amount_in_raw,
            expected_out_raw=expected_out_raw,
            amount_out_min_raw=amount_out_min_raw,
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

        approve_tx = None
        swap_tx = None
        try:
            approve_tx = self.client.ensure_allowance(approve_token_address, amount_in_raw)
        except TransactionSendError as exc:
            raise ExecutionFailure(
                str(exc),
                approve_tx_hash=exc.tx_hash,
                approve_nonce=exc.nonce,
                approve_gas_price_wei=exc.gas_price_wei,
            ) from exc
        except Exception as exc:
            raise ExecutionFailure(str(exc)) from exc

        try:
            swap_tx = self.client.swap_exact_tokens_for_tokens(
                amount_in_raw=amount_in_raw,
                amount_out_min_raw=amount_out_min_raw,
                path=path,
                side="buy",
            )
        except TransactionSendError as exc:
            raise ExecutionFailure(
                str(exc),
                approve_tx_hash=approve_tx.tx_hash if approve_tx is not None else None,
                swap_tx_hash=exc.tx_hash,
                approve_nonce=approve_tx.nonce if approve_tx is not None else None,
                swap_nonce=exc.nonce,
                approve_gas_price_wei=approve_tx.gas_price_wei if approve_tx is not None else None,
                swap_gas_price_wei=exc.gas_price_wei,
            ) from exc
        except Exception as exc:
            raise ExecutionFailure(
                str(exc),
                approve_tx_hash=approve_tx.tx_hash if approve_tx is not None else None,
                approve_nonce=approve_tx.nonce if approve_tx is not None else None,
                approve_gas_price_wei=approve_tx.gas_price_wei if approve_tx is not None else None,
            ) from exc
        approve_tx_hash = approve_tx.tx_hash if approve_tx is not None else None
        swap_tx_hash = swap_tx.tx_hash
        tx_note = self._build_tx_note(approve_tx_hash, swap_tx_hash)
        actual_preview = self._buy_preview_from_receipt_transfers(
            symbol,
            preview,
            tx_hash=swap_tx_hash,
        )
        if actual_preview is None:
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
            approve_tx_hash=approve_tx_hash,
            tx_nonce=swap_tx.nonce,
            approve_nonce=approve_tx.nonce if approve_tx is not None else None,
            tx_gas_price_wei=swap_tx.gas_price_wei,
            approve_gas_price_wei=approve_tx.gas_price_wei if approve_tx is not None else None,
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

        approve_tx = None
        swap_tx = None
        try:
            approve_tx = self.client.ensure_allowance(approve_token_address, amount_in_raw)
        except TransactionSendError as exc:
            raise ExecutionFailure(
                str(exc),
                approve_tx_hash=exc.tx_hash,
                approve_nonce=exc.nonce,
                approve_gas_price_wei=exc.gas_price_wei,
            ) from exc
        except Exception as exc:
            raise ExecutionFailure(str(exc)) from exc

        try:
            swap_tx = self.client.swap_exact_tokens_for_tokens(
                amount_in_raw=amount_in_raw,
                amount_out_min_raw=amount_out_min_raw,
                path=path,
                side="sell",
            )
        except TransactionSendError as exc:
            raise ExecutionFailure(
                str(exc),
                approve_tx_hash=approve_tx.tx_hash if approve_tx is not None else None,
                swap_tx_hash=exc.tx_hash,
                approve_nonce=approve_tx.nonce if approve_tx is not None else None,
                swap_nonce=exc.nonce,
                approve_gas_price_wei=approve_tx.gas_price_wei if approve_tx is not None else None,
                swap_gas_price_wei=exc.gas_price_wei,
            ) from exc
        except Exception as exc:
            raise ExecutionFailure(
                str(exc),
                approve_tx_hash=approve_tx.tx_hash if approve_tx is not None else None,
                approve_nonce=approve_tx.nonce if approve_tx is not None else None,
                approve_gas_price_wei=approve_tx.gas_price_wei if approve_tx is not None else None,
            ) from exc
        approve_tx_hash = approve_tx.tx_hash if approve_tx is not None else None
        swap_tx_hash = swap_tx.tx_hash
        tx_note = self._build_tx_note(approve_tx_hash, swap_tx_hash)
        actual_preview = self._sell_preview_from_receipt_transfers(
            symbol,
            preview,
            tx_hash=swap_tx_hash,
        )
        if actual_preview is None:
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
            approve_tx_hash=approve_tx_hash,
            tx_nonce=swap_tx.nonce,
            approve_nonce=approve_tx.nonce if approve_tx is not None else None,
            tx_gas_price_wei=swap_tx.gas_price_wei,
            approve_gas_price_wei=approve_tx.gas_price_wei if approve_tx is not None else None,
        )

    def retry_pending_tx(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        pending_tx: Any,
    ) -> TradeFill | None:
        side = str(pending_tx["side"])
        amount_in_raw, amount_out_min_raw, approve_token_address, path = self._pending_tx_router_args(
            pending_tx
        )
        preview = self._pending_tx_preview(symbol, pending_tx)
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

        approve_nonce = self._pending_tx_nonce(pending_tx, "approve_nonce")
        swap_nonce = self._pending_tx_nonce(pending_tx, "swap_nonce")
        prior_approve_tx_hash = str(pending_tx["approve_tx_hash"] or "") or None
        approve_min_gas_price_wei = self._replacement_min_gas_price_wei(
            self._pending_tx_gas_price_wei(pending_tx, "approve_gas_price_wei")
        )
        swap_min_gas_price_wei = self._replacement_min_gas_price_wei(
            self._pending_tx_gas_price_wei(pending_tx, "swap_gas_price_wei")
        )

        approve_tx = None
        swap_tx = None
        try:
            approve_tx = self.client.ensure_allowance(
                approve_token_address,
                amount_in_raw,
                nonce=approve_nonce,
                min_gas_price_wei=approve_min_gas_price_wei,
            )
        except TransactionSendError as exc:
            raise ExecutionFailure(
                str(exc),
                approve_tx_hash=exc.tx_hash,
                approve_nonce=exc.nonce,
                approve_gas_price_wei=exc.gas_price_wei,
            ) from exc
        except Exception as exc:
            raise ExecutionFailure(
                str(exc),
                approve_tx_hash=prior_approve_tx_hash,
                approve_nonce=approve_nonce,
                approve_gas_price_wei=self._pending_tx_gas_price_wei(
                    pending_tx,
                    "approve_gas_price_wei",
                ),
            ) from exc

        try:
            swap_tx = self.client.swap_exact_tokens_for_tokens(
                amount_in_raw=amount_in_raw,
                amount_out_min_raw=amount_out_min_raw,
                path=path,
                side=side,
                nonce=swap_nonce,
                min_gas_price_wei=swap_min_gas_price_wei,
            )
        except TransactionSendError as exc:
            raise ExecutionFailure(
                str(exc),
                approve_tx_hash=(
                    approve_tx.tx_hash if approve_tx is not None else prior_approve_tx_hash
                ),
                swap_tx_hash=exc.tx_hash,
                approve_nonce=approve_tx.nonce if approve_tx is not None else approve_nonce,
                swap_nonce=exc.nonce,
                approve_gas_price_wei=(
                    approve_tx.gas_price_wei if approve_tx is not None else self._pending_tx_gas_price_wei(
                        pending_tx,
                        "approve_gas_price_wei",
                    )
                ),
                swap_gas_price_wei=exc.gas_price_wei,
            ) from exc
        except Exception as exc:
            raise ExecutionFailure(
                str(exc),
                approve_tx_hash=approve_tx.tx_hash if approve_tx is not None else prior_approve_tx_hash,
                approve_nonce=approve_tx.nonce if approve_tx is not None else approve_nonce,
                swap_nonce=swap_nonce,
                approve_gas_price_wei=(
                    approve_tx.gas_price_wei if approve_tx is not None else self._pending_tx_gas_price_wei(
                        pending_tx,
                        "approve_gas_price_wei",
                    )
                ),
                swap_gas_price_wei=self._pending_tx_gas_price_wei(pending_tx, "swap_gas_price_wei"),
            ) from exc
        approve_tx_hash = approve_tx.tx_hash if approve_tx is not None else prior_approve_tx_hash
        swap_tx_hash = swap_tx.tx_hash

        decision = TradeDecision(
            symbol=symbol.name,
            side=side,
            level=int(pending_tx["level"]),
            target_price=preview.price,
            reason="pending tx retried on startup",
        )
        if side == "buy":
            actual_preview = self._buy_preview_from_receipt_transfers(
                symbol,
                preview,
                tx_hash=swap_tx_hash,
            )
            if actual_preview is None:
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
                message=(
                    f"recovered live buy level {decision.level} filled at "
                    f"{actual_preview.price:.6f} tx={swap_tx_hash}"
                ),
                tx_hash=swap_tx_hash,
                approve_tx_hash=approve_tx_hash,
                tx_nonce=swap_tx.nonce,
                approve_nonce=approve_tx.nonce if approve_tx is not None else approve_nonce,
                tx_gas_price_wei=swap_tx.gas_price_wei,
                approve_gas_price_wei=approve_tx.gas_price_wei if approve_tx is not None else None,
            )

        actual_preview = self._sell_preview_from_receipt_transfers(
            symbol,
            preview,
            tx_hash=swap_tx_hash,
        )
        if actual_preview is None:
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
            message=(
                f"recovered live sell level {decision.level} filled at "
                f"{actual_preview.price:.6f} tx={swap_tx_hash}"
            ),
            tx_hash=swap_tx_hash,
            approve_tx_hash=approve_tx_hash,
            tx_nonce=swap_tx.nonce,
            approve_nonce=approve_tx.nonce if approve_tx is not None else approve_nonce,
            tx_gas_price_wei=swap_tx.gas_price_wei,
            approve_gas_price_wei=approve_tx.gas_price_wei if approve_tx is not None else None,
        )

    def cancel_pending_tx(self, pending_tx: Any) -> Any:
        cancel_nonce = self._pending_tx_nonce(pending_tx, "swap_nonce")
        if cancel_nonce is None:
            cancel_nonce = self._pending_tx_nonce(pending_tx, "approve_nonce")
        if cancel_nonce is None:
            raise ValueError("Pending tx is missing nonce metadata for cancellation.")

        prior_gas_candidates = [
            self._pending_tx_gas_price_wei(pending_tx, "cancel_gas_price_wei"),
            self._pending_tx_gas_price_wei(pending_tx, "swap_gas_price_wei"),
            self._pending_tx_gas_price_wei(pending_tx, "approve_gas_price_wei"),
        ]
        prior_gas_price_wei = max(
            (value for value in prior_gas_candidates if value is not None),
            default=None,
        )
        min_gas_price_wei = self._replacement_min_gas_price_wei(prior_gas_price_wei)
        return self.client.cancel_transaction(
            nonce=cancel_nonce,
            min_gas_price_wei=min_gas_price_wei,
        )

    def confirm_pending_tx(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        pending_tx: Any,
    ) -> TradeFill | None:
        preview = self._pending_tx_preview(symbol, pending_tx)
        side = str(pending_tx["side"])
        tx_hash = str(pending_tx["swap_tx_hash"] or pending_tx["tx_hash"] or "")
        approve_tx_hash = str(pending_tx["approve_tx_hash"] or "") or None
        actual_preview = self._preview_from_receipt_transfers(symbol, preview, tx_hash=tx_hash)
        if actual_preview is not None:
            preview = actual_preview
        decision = TradeDecision(
            symbol=symbol.name,
            side=side,
            level=int(pending_tx["level"]),
            target_price=preview.price,
            reason="pending tx confirmed during startup recovery",
        )
        if side == "buy":
            return self._apply_buy_fill(
                symbol,
                state,
                decision,
                preview,
                message=f"recovered confirmed buy level {decision.level} tx={tx_hash}",
                tx_hash=tx_hash or None,
                approve_tx_hash=approve_tx_hash,
                tx_nonce=self._pending_tx_nonce(pending_tx, "swap_nonce"),
                approve_nonce=self._pending_tx_nonce(pending_tx, "approve_nonce"),
                tx_gas_price_wei=self._pending_tx_gas_price_wei(pending_tx, "swap_gas_price_wei"),
                approve_gas_price_wei=self._pending_tx_gas_price_wei(
                    pending_tx,
                    "approve_gas_price_wei",
                ),
            )
        return self._apply_sell_fill(
            symbol,
            state,
            decision,
            preview,
            message=f"recovered confirmed sell level {decision.level} tx={tx_hash}",
            tx_hash=tx_hash or None,
            approve_tx_hash=approve_tx_hash,
            tx_nonce=self._pending_tx_nonce(pending_tx, "swap_nonce"),
            approve_nonce=self._pending_tx_nonce(pending_tx, "approve_nonce"),
            tx_gas_price_wei=self._pending_tx_gas_price_wei(pending_tx, "swap_gas_price_wei"),
            approve_gas_price_wei=self._pending_tx_gas_price_wei(
                pending_tx,
                "approve_gas_price_wei",
            ),
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

    def _buy_preview_from_receipt_transfers(
        self,
        symbol: SymbolConfig,
        preview: ExecutionPreview,
        *,
        tx_hash: str,
    ) -> ExecutionPreview | None:
        return self._preview_from_receipt_transfers(symbol, preview, tx_hash=tx_hash)

    def _sell_preview_from_receipt_transfers(
        self,
        symbol: SymbolConfig,
        preview: ExecutionPreview,
        *,
        tx_hash: str,
    ) -> ExecutionPreview | None:
        return self._preview_from_receipt_transfers(symbol, preview, tx_hash=tx_hash)

    def _preview_from_receipt_transfers(
        self,
        symbol: SymbolConfig,
        preview: ExecutionPreview,
        *,
        tx_hash: str,
    ) -> ExecutionPreview | None:
        if not tx_hash:
            return None

        try:
            base_token_address = self.client.to_checksum(symbol.route.base_token_address)
            quote_token_address = self.client.to_checksum(symbol.route.quote_token_address)
            transfer_deltas_raw = self.client.get_erc20_transfer_deltas_raw(
                tx_hash,
                [base_token_address, quote_token_address],
            )
            base_delta_raw = int(transfer_deltas_raw.get(base_token_address, 0))
            quote_delta_raw = int(transfer_deltas_raw.get(quote_token_address, 0))
            base_decimals = self.client.get_token_decimals(
                symbol.route.base_token_address,
                symbol.route.base_token_decimals,
            )
            quote_decimals = self.client.get_token_decimals(
                symbol.route.quote_token_address,
                symbol.route.quote_token_decimals,
            )
        except Exception:
            return None

        if preview.side == "buy":
            actual_base_qty = self.client.from_raw_amount(max(0, base_delta_raw), base_decimals)
            actual_quote_value = self.client.from_raw_amount(max(0, -quote_delta_raw), quote_decimals)
        else:
            actual_base_qty = self.client.from_raw_amount(max(0, -base_delta_raw), base_decimals)
            actual_quote_value = self.client.from_raw_amount(max(0, quote_delta_raw), quote_decimals)

        if actual_base_qty <= 0 or actual_quote_value <= 0:
            return None

        return ExecutionPreview(
            symbol=preview.symbol,
            side=preview.side,
            level=preview.level,
            price=actual_quote_value / actual_base_qty,
            base_qty=actual_base_qty,
            quote_value=actual_quote_value,
            price_impact_bps=preview.price_impact_bps,
            expected_profit_usd=preview.expected_profit_usd,
            estimated_gas_usd=preview.estimated_gas_usd,
            amount_in_raw=preview.amount_in_raw,
            expected_out_raw=preview.expected_out_raw,
            amount_out_min_raw=preview.amount_out_min_raw,
            approve_token_address=preview.approve_token_address,
            path=list(preview.path),
        )

    def _pending_tx_preview(
        self,
        symbol: SymbolConfig,
        pending_tx: Any,
    ) -> ExecutionPreview:
        side = str(pending_tx["side"])
        if side == "buy":
            quote_decimals = self.client.get_token_decimals(
                symbol.route.quote_token_address,
                symbol.route.quote_token_decimals,
            )
            base_decimals = self.client.get_token_decimals(
                symbol.route.base_token_address,
                symbol.route.base_token_decimals,
            )
            quote_value = self.client.from_raw_amount(int(pending_tx["amount_in_raw"]), quote_decimals)
            base_qty = self.client.from_raw_amount(int(pending_tx["expected_out_raw"]), base_decimals)
        else:
            base_decimals = self.client.get_token_decimals(
                symbol.route.base_token_address,
                symbol.route.base_token_decimals,
            )
            quote_decimals = self.client.get_token_decimals(
                symbol.route.quote_token_address,
                symbol.route.quote_token_decimals,
            )
            base_qty = self.client.from_raw_amount(int(pending_tx["amount_in_raw"]), base_decimals)
            quote_value = self.client.from_raw_amount(int(pending_tx["expected_out_raw"]), quote_decimals)

        if base_qty <= 0 or quote_value <= 0:
            raise ValueError(
                f"{symbol.name}: pending tx preview is invalid for recovery (side={side})."
            )

        return ExecutionPreview(
            symbol=symbol.name,
            side=side,
            level=int(pending_tx["level"]),
            price=quote_value / base_qty,
            base_qty=base_qty,
            quote_value=quote_value,
            price_impact_bps=0.0,
            expected_profit_usd=0.0,
            estimated_gas_usd=(
                float(pending_tx["estimated_gas_usd"])
                if pending_tx["estimated_gas_usd"] is not None
                else None
            ),
            amount_in_raw=int(pending_tx["amount_in_raw"]),
            expected_out_raw=int(pending_tx["expected_out_raw"]),
            amount_out_min_raw=int(pending_tx["amount_out_min_raw"]),
            approve_token_address=str(pending_tx["approve_token_address"]),
            path=json.loads(str(pending_tx["path_json"])),
        )

    def _pending_tx_router_args(
        self,
        pending_tx: Any,
    ) -> tuple[int, int, str, list[str]]:
        amount_in_raw = pending_tx["amount_in_raw"]
        amount_out_min_raw = pending_tx["amount_out_min_raw"]
        approve_token_address = pending_tx["approve_token_address"]
        path_json = pending_tx["path_json"]
        if (
            amount_in_raw is None
            or amount_out_min_raw is None
            or approve_token_address is None
            or path_json is None
        ):
            raise ValueError("Pending tx is missing router arguments for recovery.")
        path = json.loads(str(path_json))
        if not path:
            raise ValueError("Pending tx path is empty.")
        return (
            int(amount_in_raw),
            int(amount_out_min_raw),
            str(approve_token_address),
            [str(item) for item in path],
        )

    def _pending_tx_nonce(
        self,
        pending_tx: Any,
        field: str,
    ) -> int | None:
        value = pending_tx[field]
        if value is None:
            return None
        return int(value)

    def _pending_tx_gas_price_wei(
        self,
        pending_tx: Any,
        field: str,
    ) -> int | None:
        value = pending_tx[field]
        if value is None:
            return None
        return int(value)

    def _replacement_min_gas_price_wei(self, previous_gas_price_wei: int | None) -> int | None:
        if previous_gas_price_wei is None or previous_gas_price_wei <= 0:
            return None
        bump_bps = max(0, int(round(self.config.execution.replacement_gas_bump_bps)))
        bumped = (previous_gas_price_wei * (10000 + bump_bps) + 9999) // 10000
        return max(previous_gas_price_wei + 1, bumped)

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
            price_impact_bps=preview.price_impact_bps,
            expected_profit_usd=preview.expected_profit_usd,
            estimated_gas_usd=preview.estimated_gas_usd,
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
            price_impact_bps=preview.price_impact_bps,
            expected_profit_usd=preview.expected_profit_usd,
            estimated_gas_usd=preview.estimated_gas_usd,
            amount_in_raw=preview.amount_in_raw,
            expected_out_raw=preview.expected_out_raw,
            amount_out_min_raw=preview.amount_out_min_raw,
            approve_token_address=preview.approve_token_address,
            path=list(preview.path),
        )
