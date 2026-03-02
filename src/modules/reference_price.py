from __future__ import annotations

from core.helpers import rolling_volatility_bps
from core.models import AppConfig, QuoteSnapshot, SymbolState


class ReferencePriceEngine:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def update(self, state: SymbolState, quote: QuoteSnapshot) -> None:
        alpha = self.config.reference_price.ema_alpha

        if state.ema_price == 0:
            state.ema_price = quote.mid_price
        else:
            state.ema_price = state.ema_price + alpha * (quote.mid_price - state.ema_price)

        state.recent_mid_prices.append(quote.mid_price)
        state.last_mid_price = quote.mid_price
        state.spread_bps = quote.spread_bps
        state.volatility_bps = rolling_volatility_bps(list(state.recent_mid_prices))
        state.reference_price = (
            self.config.reference_price.mid_weight * quote.mid_price
            + self.config.reference_price.ema_weight * state.ema_price
        )

        if state.avg_cost_price > 0:
            state.unrealized_pnl = state.base_balance * (quote.mid_price - state.avg_cost_price)
        else:
            state.unrealized_pnl = 0.0
