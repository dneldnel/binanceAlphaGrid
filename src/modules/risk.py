from __future__ import annotations

from core.models import ExecutionPreview, GridTarget, QuoteSnapshot, SymbolConfig, SymbolState, TradeDecision


class RiskManager:
    def __init__(
        self,
        *,
        min_net_edge_bps: float,
        max_price_impact_bps: float,
        max_gas_usd_per_tx: float,
        min_expected_profit_usd: float,
        max_notional_per_order: float,
        max_position_per_symbol_usd: float,
        max_daily_realized_loss_usd: float,
        max_daily_gas_usd: float,
        max_consecutive_failed_tx: int,
        max_failed_tx_per_symbol: int,
        max_trades_per_symbol_per_hour: int,
        enforce_mainnet_rollout_controls: bool,
        mainnet_buy_enabled: bool,
        mainnet_sell_enabled: bool,
        mainnet_max_notional_per_order: float,
        mainnet_max_position_per_symbol_usd: float,
    ) -> None:
        self.min_net_edge_bps = min_net_edge_bps
        self.max_price_impact_bps = max_price_impact_bps
        self.max_gas_usd_per_tx = max_gas_usd_per_tx
        self.min_expected_profit_usd = min_expected_profit_usd
        self.max_notional_per_order = max_notional_per_order
        self.max_position_per_symbol_usd = max_position_per_symbol_usd
        self.max_daily_realized_loss_usd = max_daily_realized_loss_usd
        self.max_daily_gas_usd = max_daily_gas_usd
        self.max_consecutive_failed_tx = max_consecutive_failed_tx
        self.max_failed_tx_per_symbol = max_failed_tx_per_symbol
        self.max_trades_per_symbol_per_hour = max_trades_per_symbol_per_hour
        self.enforce_mainnet_rollout_controls = enforce_mainnet_rollout_controls
        self.mainnet_buy_enabled = mainnet_buy_enabled
        self.mainnet_sell_enabled = mainnet_sell_enabled
        self.mainnet_max_notional_per_order = mainnet_max_notional_per_order
        self.mainnet_max_position_per_symbol_usd = mainnet_max_position_per_symbol_usd

    def _effective_limit(self, base_limit: float, rollout_limit: float) -> float:
        if not self.enforce_mainnet_rollout_controls or rollout_limit <= 0:
            return base_limit
        if base_limit <= 0:
            return rollout_limit
        return min(base_limit, rollout_limit)

    def _effective_max_notional_per_order(self) -> float:
        return self._effective_limit(
            self.max_notional_per_order,
            self.mainnet_max_notional_per_order,
        )

    def _effective_max_position_per_symbol_usd(self) -> float:
        return self._effective_limit(
            self.max_position_per_symbol_usd,
            self.mainnet_max_position_per_symbol_usd,
        )

    def global_trade_pause_reason(
        self,
        *,
        global_realized_pnl: float,
        global_daily_gas_usd: float,
        consecutive_failed_tx_count: int,
    ) -> str | None:
        if (
            self.max_daily_realized_loss_usd > 0
            and global_realized_pnl <= -self.max_daily_realized_loss_usd
        ):
            return (
                "daily realized loss "
                f"{global_realized_pnl:.2f} <= -{self.max_daily_realized_loss_usd:.2f}"
            )
        if self.max_daily_gas_usd > 0 and global_daily_gas_usd >= self.max_daily_gas_usd:
            return (
                "daily gas "
                f"{global_daily_gas_usd:.4f} >= {self.max_daily_gas_usd:.4f}"
            )
        if (
            self.max_consecutive_failed_tx > 0
            and consecutive_failed_tx_count >= self.max_consecutive_failed_tx
        ):
            return (
                "consecutive failed tx "
                f"{consecutive_failed_tx_count} >= {self.max_consecutive_failed_tx}"
            )
        return None

    def symbol_trade_pause_reason(
        self,
        *,
        failed_tx_count_for_symbol: int,
    ) -> str | None:
        if (
            self.max_failed_tx_per_symbol > 0
            and failed_tx_count_for_symbol >= self.max_failed_tx_per_symbol
        ):
            return (
                "failed tx count "
                f"{failed_tx_count_for_symbol} >= {self.max_failed_tx_per_symbol}"
            )
        return None

    def preview_violation_reason(
        self,
        preview: ExecutionPreview,
        *,
        state: SymbolState,
    ) -> str | None:
        max_notional_per_order = self._effective_max_notional_per_order()
        max_position_per_symbol_usd = self._effective_max_position_per_symbol_usd()
        if max_notional_per_order > 0 and preview.quote_value > max_notional_per_order:
            return (
                "order notional "
                f"{preview.quote_value:.4f} > {max_notional_per_order:.4f}"
            )
        if preview.side == "buy" and max_position_per_symbol_usd > 0:
            projected_position_usd = (state.base_balance + preview.base_qty) * preview.price
            if projected_position_usd > max_position_per_symbol_usd:
                return (
                    "projected position "
                    f"{projected_position_usd:.4f} > {max_position_per_symbol_usd:.4f}"
                )
        if (
            self.max_price_impact_bps > 0
            and preview.price_impact_bps > self.max_price_impact_bps
        ):
            return (
                "price impact "
                f"{preview.price_impact_bps:.1f}bps > {self.max_price_impact_bps:.1f}bps"
            )
        if self.max_gas_usd_per_tx > 0 and preview.amount_in_raw is not None:
            if preview.estimated_gas_usd is None:
                return "estimated gas unavailable"
            if preview.estimated_gas_usd > self.max_gas_usd_per_tx:
                return (
                    "estimated gas "
                    f"{preview.estimated_gas_usd:.4f} > {self.max_gas_usd_per_tx:.4f}"
                )
        if preview.expected_profit_usd < self.min_expected_profit_usd:
            return (
                "expected profit "
                f"{preview.expected_profit_usd:.4f} < {self.min_expected_profit_usd:.4f}"
            )
        return None

    def allow_buy(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        target: GridTarget,
        net_edge_bps: float,
        trades_per_symbol_last_hour: int,
    ) -> TradeDecision | None:
        order_quote_usd = target.order_quote_usd or 0.0
        projected_position_usd = (state.base_balance * quote.exec_buy_price) + order_quote_usd
        max_notional_per_order = self._effective_max_notional_per_order()
        max_position_per_symbol_usd = self._effective_max_position_per_symbol_usd()

        if self.enforce_mainnet_rollout_controls and not self.mainnet_buy_enabled:
            return None
        if net_edge_bps < self.min_net_edge_bps:
            return None
        if state.quote_balance_usd - order_quote_usd < symbol.inventory.min_quote_reserve_usd:
            return None
        if order_quote_usd > symbol.inventory.max_quote_per_symbol_usd:
            return None
        if max_notional_per_order > 0 and order_quote_usd > max_notional_per_order:
            return None
        if max_position_per_symbol_usd > 0 and projected_position_usd > max_position_per_symbol_usd:
            return None
        if (
            self.max_trades_per_symbol_per_hour > 0
            and trades_per_symbol_last_hour >= self.max_trades_per_symbol_per_hour
        ):
            return None

        return TradeDecision(
            symbol=symbol.name,
            side="buy",
            level=target.level,
            target_price=target.trigger_price,
            order_quote_usd=order_quote_usd,
            reason="buy trigger hit",
            net_edge_bps=net_edge_bps,
        )

    def allow_sell(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        target: GridTarget,
        net_edge_bps: float,
        trades_per_symbol_last_hour: int,
    ) -> TradeDecision | None:
        sellable_base = max(0.0, state.base_balance - symbol.inventory.reserve_base_tokens)
        order_base_ratio = target.order_base_ratio or 0.0
        order_base_qty = sellable_base * order_base_ratio
        order_notional_usd = order_base_qty * quote.exec_sell_price
        max_notional_per_order = self._effective_max_notional_per_order()

        if self.enforce_mainnet_rollout_controls and not self.mainnet_sell_enabled:
            return None
        if net_edge_bps < self.min_net_edge_bps:
            return None
        if order_base_qty <= 0:
            return None
        if order_base_qty * quote.exec_sell_price < symbol.inventory.min_sell_base_usd:
            return None
        if max_notional_per_order > 0 and order_notional_usd > max_notional_per_order:
            return None
        if (
            self.max_trades_per_symbol_per_hour > 0
            and trades_per_symbol_last_hour >= self.max_trades_per_symbol_per_hour
        ):
            return None

        return TradeDecision(
            symbol=symbol.name,
            side="sell",
            level=target.level,
            target_price=target.trigger_price,
            order_base_ratio=order_base_ratio,
            reason="sell trigger hit",
            net_edge_bps=net_edge_bps,
        )
