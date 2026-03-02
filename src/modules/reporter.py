from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from core.helpers import fmt_price
from core.models import AppConfig, SymbolState, TradeFill


class Reporter:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.events: deque[str] = deque(maxlen=config.reporting.recent_log_lines)

    def record_fill(self, fill: TradeFill) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.events.appendleft(
            f"[{timestamp}] {fill.symbol} {fill.side.upper()} L{fill.level} qty={fill.base_qty:.4f} "
            f"price={fill.price:.6f} realized={fill.realized_pnl:+.2f}"
        )

    def record_message(self, symbol: str, message: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.events.appendleft(f"[{timestamp}] {symbol} {message}")

    def render(self, iteration: int, states: list[SymbolState]) -> None:
        print("")
        print("=" * 118)
        print(
            f"Binance Alpha Grid Skeleton | iteration={iteration} | mode={self.config.runtime.mode}"
        )
        print("=" * 118)
        print(
            f"{'Symbol':<12} {'Status':<8} {'Mid':>12} {'Ref':>12} {'EMA':>12} "
            f"{'Base':>10} {'QuoteUSD':>10} {'Trades':>8} {'RealPnL':>10} {'Unreal':>10}"
        )
        print("-" * 118)

        for state in states:
            print(
                f"{state.symbol:<12} {state.status:<8} {fmt_price(state.last_mid_price):>12} "
                f"{fmt_price(state.reference_price):>12} {fmt_price(state.ema_price):>12} "
                f"{state.base_balance:>10.4f} {state.quote_balance_usd:>10.2f} "
                f"{state.daily_trade_count:>8d} {state.realized_pnl:>10.2f} {state.unrealized_pnl:>10.2f}"
            )

        print("-" * 118)
        print("Recent events:")
        if not self.events:
            print("  (none)")
            return

        for event in list(self.events):
            print(f"  {event}")
