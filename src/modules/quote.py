from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Protocol

from core.models import AppConfig, QuoteSnapshot, SymbolConfig, SymbolState
from evm import EvmRouterClient


class QuoteEngine(Protocol):
    def next_quote(self, symbol: SymbolConfig, state: SymbolState) -> QuoteSnapshot: ...


def build_quote_engine(config: AppConfig) -> QuoteEngine:
    if config.runtime.mode in {"paper", "live"}:
        return LiveQuoteEngine(config)
    return SimulatedQuoteEngine(seed=config.simulation.seed)


class SimulatedQuoteEngine:
    def __init__(self, seed: int) -> None:
        self.random = random.Random(seed)

    def next_quote(self, symbol: SymbolConfig, state: SymbolState) -> QuoteSnapshot:
        previous_mid = state.last_mid_price or symbol.simulation.initial_price or 0.01
        drift_bps = symbol.simulation.drift_bps or symbol.simulation.default_drift_bps
        volatility_bps = (
            symbol.simulation.volatility_bps or symbol.simulation.default_volatility_bps
        )
        spread_bps = symbol.simulation.spread_bps or symbol.simulation.default_spread_bps

        move_bps = self.random.gauss(drift_bps, volatility_bps)
        next_mid = max(previous_mid * (1.0 + move_bps / 10000.0), 1e-8)
        half_spread = spread_bps / 2.0

        exec_buy = next_mid * (1.0 + half_spread / 10000.0)
        exec_sell = next_mid * (1.0 - half_spread / 10000.0)

        return QuoteSnapshot(
            symbol=symbol.name,
            ts=datetime.now(timezone.utc),
            mid_price=next_mid,
            exec_buy_price=exec_buy,
            exec_sell_price=exec_sell,
            spread_bps=spread_bps,
        )


class LiveQuoteEngine:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.client = EvmRouterClient(config, read_only=config.runtime.mode != "live")

    def next_quote(self, symbol: SymbolConfig, state: SymbolState) -> QuoteSnapshot:
        self.client.validate_symbol(symbol)

        quote_decimals = self.client.get_token_decimals(
            symbol.route.quote_token_address,
            symbol.route.quote_token_decimals,
        )
        base_decimals = self.client.get_token_decimals(
            symbol.route.base_token_address,
            symbol.route.base_token_decimals,
        )

        buy_amount_in_raw = self.client.to_raw_amount(self.config.market.probe_quote_usd, quote_decimals)
        buy_amounts = self.client.get_amounts_out(buy_amount_in_raw, symbol.route.buy_path)
        base_out = self.client.from_raw_amount(buy_amounts[-1], base_decimals)
        if base_out <= 0:
            raise RuntimeError(f"{symbol.name}: quote path returned zero base output")

        exec_buy_price = self.config.market.probe_quote_usd / base_out

        sell_probe_base_units = self.config.market.probe_quote_usd / max(
            state.last_mid_price or symbol.simulation.initial_price or 1.0,
            1e-8,
        )
        sell_amount_in_raw = self.client.to_raw_amount(sell_probe_base_units, base_decimals)
        sell_amounts = self.client.get_amounts_out(sell_amount_in_raw, symbol.route.sell_path)
        quote_out = self.client.from_raw_amount(sell_amounts[-1], quote_decimals)
        if sell_probe_base_units <= 0 or quote_out <= 0:
            raise RuntimeError(f"{symbol.name}: sell path returned zero quote output")

        exec_sell_price = quote_out / sell_probe_base_units
        mid_price = (exec_buy_price + exec_sell_price) / 2.0
        spread_bps = ((exec_buy_price - exec_sell_price) / mid_price) * 10000.0 if mid_price else 0.0

        return QuoteSnapshot(
            symbol=symbol.name,
            ts=datetime.now(timezone.utc),
            mid_price=mid_price,
            exec_buy_price=exec_buy_price,
            exec_sell_price=exec_sell_price,
            spread_bps=spread_bps,
        )
