from __future__ import annotations

from core.models import GridTarget, QuoteSnapshot, SymbolConfig, SymbolState, TradeDecision


class RiskManager:
    def __init__(self, min_net_edge_bps: float) -> None:
        self.min_net_edge_bps = min_net_edge_bps

    def allow_buy(
        self,
        symbol: SymbolConfig,
        state: SymbolState,
        quote: QuoteSnapshot,
        target: GridTarget,
        net_edge_bps: float,
    ) -> TradeDecision | None:
        order_quote_usd = target.order_quote_usd or 0.0

        if net_edge_bps < self.min_net_edge_bps:
            return None
        if state.quote_balance_usd - order_quote_usd < symbol.inventory.min_quote_reserve_usd:
            return None
        if order_quote_usd > symbol.inventory.max_quote_per_symbol_usd:
            return None
        if state.daily_trade_count >= symbol.grid.buy_levels[-1].level * 200:
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
    ) -> TradeDecision | None:
        sellable_base = max(0.0, state.base_balance - symbol.inventory.reserve_base_tokens)
        order_base_ratio = target.order_base_ratio or 0.0
        order_base_qty = sellable_base * order_base_ratio

        if net_edge_bps < self.min_net_edge_bps:
            return None
        if order_base_qty <= 0:
            return None
        if order_base_qty * quote.exec_sell_price < symbol.inventory.min_sell_base_usd:
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
