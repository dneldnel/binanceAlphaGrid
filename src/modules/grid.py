from __future__ import annotations

from core.helpers import clamp
from core.models import GridPlan, GridTarget, QuoteSnapshot, SymbolConfig, SymbolState


class GridEngine:
    def build(self, symbol: SymbolConfig, state: SymbolState, quote: QuoteSnapshot) -> GridPlan:
        base_value = state.base_balance * quote.mid_price
        total_value = base_value + state.quote_balance_usd
        inventory_ratio = base_value / total_value if total_value > 0 else 0.0
        inventory_gap = inventory_ratio - symbol.inventory.target_base_ratio
        inventory_shift_bps = clamp(
            inventory_gap * symbol.inventory.inventory_skew_factor_bps,
            -symbol.inventory.max_inventory_shift_bps,
            symbol.inventory.max_inventory_shift_bps,
        )

        base_step_bps = max(
            symbol.grid.min_step_bps,
            state.spread_bps * symbol.grid.spread_multiplier,
            state.volatility_bps * symbol.grid.volatility_multiplier,
            symbol.grid.base_step_bps,
        )
        step_bps = clamp(base_step_bps, symbol.grid.min_step_bps, symbol.grid.max_step_bps)

        buy_targets: list[GridTarget] = []
        sell_targets: list[GridTarget] = []

        for level in symbol.grid.buy_levels:
            adjusted_offset = max(1.0, level.offset_bps + inventory_shift_bps)
            trigger_price = state.reference_price * (1.0 - adjusted_offset / 10000.0)
            buy_targets.append(
                GridTarget(
                    side="buy",
                    level=level.level,
                    trigger_price=trigger_price,
                    order_quote_usd=level.order_quote_usd,
                    cooldown_sec=level.cooldown_sec,
                )
            )

        for level in symbol.grid.sell_levels:
            adjusted_offset = max(1.0, level.offset_bps - inventory_shift_bps)
            trigger_price = state.reference_price * (1.0 + adjusted_offset / 10000.0)
            sell_targets.append(
                GridTarget(
                    side="sell",
                    level=level.level,
                    trigger_price=trigger_price,
                    order_base_ratio=level.order_base_ratio,
                    cooldown_sec=level.cooldown_sec,
                )
            )

        state.buy_open_count = len(buy_targets)
        state.sell_open_count = len(sell_targets)
        state.buy_basis_price = buy_targets[0].trigger_price if buy_targets else 0.0
        state.sell_basis_price = sell_targets[0].trigger_price if sell_targets else 0.0

        return GridPlan(
            buy_targets=buy_targets,
            sell_targets=sell_targets,
            inventory_shift_bps=inventory_shift_bps,
            step_bps=step_bps,
        )

    def find_buy_target(self, plan: GridPlan, quote: QuoteSnapshot) -> GridTarget | None:
        candidates = [
            target for target in plan.buy_targets if quote.exec_buy_price <= target.trigger_price
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item.level)[0]

    def find_sell_target(self, plan: GridPlan, quote: QuoteSnapshot) -> GridTarget | None:
        candidates = [
            target for target in plan.sell_targets if quote.exec_sell_price >= target.trigger_price
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item.level)[0]
