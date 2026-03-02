from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

from .models import (
    AppConfig,
    ChainConfig,
    ExecutionConfig,
    GridConfig,
    GridLevelConfig,
    InventoryConfig,
    MarketConfig,
    ReferencePriceConfig,
    ReportingConfig,
    RiskConfig,
    RouterConfig,
    RuntimeConfig,
    SimulationConfig,
    SymbolConfig,
    SymbolRouteConfig,
    UniverseConfig,
)


def load_config(path: Path) -> AppConfig:
    with path.open("rb") as file:
        raw = tomllib.load(file)

    runtime = _parse_runtime(raw["runtime"])
    chain = _parse_chain(raw["chain"])
    market = _parse_market(raw["market"])
    router = _parse_router(raw.get("router", {}))
    universe = _parse_universe(raw["universe"])
    reference_price = _parse_reference_price(raw["reference_price"])
    grid = _parse_grid(raw["grid"])
    inventory = _parse_inventory(raw["inventory"])
    execution = _parse_execution(raw["execution"])
    risk = _parse_risk(raw["risk"])
    reporting = _parse_reporting(raw["reporting"])
    simulation = _parse_simulation(raw["simulation"])
    symbols = _parse_symbols(
        raw,
        market=market,
        grid=grid,
        inventory=inventory,
        simulation=simulation,
    )

    if not symbols:
        raise ValueError("No enabled symbols found in config.")

    runtime.state_store_path.parent.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        runtime=runtime,
        chain=chain,
        market=market,
        router=router,
        universe=universe,
        reference_price=reference_price,
        grid=grid,
        inventory=inventory,
        execution=execution,
        risk=risk,
        reporting=reporting,
        simulation=simulation,
        symbols=symbols,
    )


def _parse_runtime(raw: dict[str, Any]) -> RuntimeConfig:
    mode = str(raw.get("mode", "dry-run" if bool(raw.get("dry_run", True)) else "live"))
    return RuntimeConfig(
        strategy_id=str(raw["strategy_id"]),
        mode=mode,
        refresh_interval_ms=int(raw["refresh_interval_ms"]),
        rotation_interval_sec=int(raw["rotation_interval_sec"]),
        state_store_path=Path(raw["state_store_path"]),
        log_level=str(raw["log_level"]),
        dry_run=mode != "live",
        allow_live=bool(raw.get("allow_live", False)),
        allow_mainnet=bool(raw.get("allow_mainnet", False)),
    )


def _parse_chain(raw: dict[str, Any]) -> ChainConfig:
    return ChainConfig(
        chain_id=int(raw["chain_id"]),
        chain_name=str(raw["chain_name"]),
        rpc_urls=[str(item) for item in raw["rpc_urls"]],
        rpc_timeout_sec=int(raw.get("rpc_timeout_sec", 15)),
        wallet_address=str(raw["wallet_address"]),
        private_key_env=str(raw["private_key_env"]),
        explorer_base_url=str(raw["explorer_base_url"]),
    )


def _parse_market(raw: dict[str, Any]) -> MarketConfig:
    return MarketConfig(
        quote_token_symbol=str(raw["quote_token_symbol"]),
        quote_token_address=str(raw["quote_token_address"]),
        quote_token_decimals=int(raw.get("quote_token_decimals", 18)),
        allowed_route_types=[str(item) for item in raw["allowed_route_types"]],
        probe_quote_usd=float(raw["probe_quote_usd"]),
        max_price_impact_bps=float(raw["max_price_impact_bps"]),
        stale_quote_sec=int(raw["stale_quote_sec"]),
    )


def _parse_router(raw: dict[str, Any]) -> RouterConfig:
    return RouterConfig(
        kind=str(raw.get("kind", "uniswap_v2")),
        address=str(raw.get("address", "")),
        spender_address=str(raw.get("spender_address", raw.get("address", ""))),
        quote_method=str(raw.get("quote_method", "getAmountsOut")),
        buy_swap_method=str(
            raw.get("buy_swap_method", "swapExactTokensForTokensSupportingFeeOnTransferTokens")
        ),
        sell_swap_method=str(
            raw.get("sell_swap_method", "swapExactTokensForTokensSupportingFeeOnTransferTokens")
        ),
        approve_max=bool(raw.get("approve_max", True)),
        tx_wait_timeout_sec=int(raw.get("tx_wait_timeout_sec", 120)),
        tx_poll_interval_sec=float(raw.get("tx_poll_interval_sec", 2.0)),
    )


def _parse_universe(raw: dict[str, Any]) -> UniverseConfig:
    return UniverseConfig(
        mode=str(raw["mode"]),
        include_symbols=[str(item) for item in raw.get("include_symbols", [])],
        exclude_symbols=[str(item) for item in raw.get("exclude_symbols", [])],
        max_symbols=int(raw["max_symbols"]),
        min_listing_age_hours=int(raw["min_listing_age_hours"]),
        min_pool_liquidity_usd=float(raw["min_pool_liquidity_usd"]),
        min_24h_volume_usd=float(raw["min_24h_volume_usd"]),
        max_token_tax_bps=float(raw["max_token_tax_bps"]),
        max_drop_pct_5m=float(raw["max_drop_pct_5m"]),
        keep_positions_on_rotation=bool(raw["keep_positions_on_rotation"]),
    )


def _parse_reference_price(raw: dict[str, Any]) -> ReferencePriceConfig:
    mid_weight = float(raw["mid_weight"])
    ema_weight = float(raw["ema_weight"])
    if round(mid_weight + ema_weight, 6) != 1.0:
        raise ValueError("reference_price.mid_weight + ema_weight must equal 1.0")
    return ReferencePriceConfig(
        source=str(raw["source"]),
        ema_alpha=float(raw["ema_alpha"]),
        mid_weight=mid_weight,
        ema_weight=ema_weight,
        volatility_lookback_sec=int(raw["volatility_lookback_sec"]),
        volatility_source=str(raw["volatility_source"]),
    )


def _parse_grid(raw: dict[str, Any]) -> GridConfig:
    return GridConfig(
        mode=str(raw["mode"]),
        base_step_bps=float(raw["base_step_bps"]),
        min_step_bps=float(raw["min_step_bps"]),
        max_step_bps=float(raw["max_step_bps"]),
        spread_multiplier=float(raw["spread_multiplier"]),
        volatility_multiplier=float(raw["volatility_multiplier"]),
        rebuild_on_fill=bool(raw["rebuild_on_fill"]),
        rebuild_on_reference_change_bps=float(raw["rebuild_on_reference_change_bps"]),
        paired_take_profit_bps=float(raw["paired_take_profit_bps"]),
        buy_levels=[_parse_grid_level(item, side="buy") for item in raw["buy_levels"]],
        sell_levels=[_parse_grid_level(item, side="sell") for item in raw["sell_levels"]],
    )


def _parse_grid_level(raw: dict[str, Any], *, side: str) -> GridLevelConfig:
    level = GridLevelConfig(
        level=int(raw["level"]),
        offset_bps=float(raw["offset_bps"]),
        cooldown_sec=int(raw.get("cooldown_sec", 0)),
        order_quote_usd=float(raw["order_quote_usd"]) if "order_quote_usd" in raw else None,
        order_base_ratio=float(raw["order_base_ratio"]) if "order_base_ratio" in raw else None,
    )
    if side == "buy" and level.order_quote_usd is None:
        raise ValueError("Buy level must define order_quote_usd")
    if side == "sell" and level.order_base_ratio is None:
        raise ValueError("Sell level must define order_base_ratio")
    return level


def _parse_inventory(raw: dict[str, Any]) -> InventoryConfig:
    return InventoryConfig(
        target_base_ratio=float(raw["target_base_ratio"]),
        inventory_skew_factor_bps=float(raw["inventory_skew_factor_bps"]),
        max_inventory_shift_bps=float(raw["max_inventory_shift_bps"]),
        reserve_base_tokens=float(raw["reserve_base_tokens"]),
        max_quote_per_symbol_usd=float(raw["max_quote_per_symbol_usd"]),
        max_base_exposure_usd=float(raw["max_base_exposure_usd"]),
        min_quote_reserve_usd=float(raw["min_quote_reserve_usd"]),
        min_sell_base_usd=float(raw["min_sell_base_usd"]),
        allow_emergency_sell_reserve=bool(raw["allow_emergency_sell_reserve"]),
    )


def _parse_execution(raw: dict[str, Any]) -> ExecutionConfig:
    return ExecutionConfig(
        router=str(raw["router"]),
        deadline_sec=int(raw["deadline_sec"]),
        slippage_bps=float(raw["slippage_bps"]),
        max_gas_gwei=float(raw["max_gas_gwei"]),
        max_gas_usd_per_tx=float(raw["max_gas_usd_per_tx"]),
        estimated_fee_bps=float(raw["estimated_fee_bps"]),
        min_net_edge_bps=float(raw["min_net_edge_bps"]),
        min_expected_profit_usd=float(raw["min_expected_profit_usd"]),
        single_symbol_single_inflight=bool(raw["single_symbol_single_inflight"]),
        max_inflight_txs=int(raw["max_inflight_txs"]),
        retry_times=int(raw["retry_times"]),
        retry_backoff_ms=int(raw["retry_backoff_ms"]),
    )


def _parse_risk(raw: dict[str, Any]) -> RiskConfig:
    kill_switch_raw = raw.get("kill_switch_file")
    return RiskConfig(
        kill_switch_file=Path(str(kill_switch_raw)) if kill_switch_raw else None,
        max_daily_realized_loss_usd=float(raw["max_daily_realized_loss_usd"]),
        max_daily_gas_usd=float(raw["max_daily_gas_usd"]),
        max_consecutive_failed_tx=int(raw["max_consecutive_failed_tx"]),
        max_failed_tx_per_symbol=int(raw["max_failed_tx_per_symbol"]),
        max_trades_per_symbol_per_hour=int(raw["max_trades_per_symbol_per_hour"]),
        hard_stop_from_cost_bps=float(raw["hard_stop_from_cost_bps"]),
        pause_on_liquidity_drop=bool(raw["pause_on_liquidity_drop"]),
        pause_on_honeypot_signal=bool(raw["pause_on_honeypot_signal"]),
        pause_on_route_failure=bool(raw["pause_on_route_failure"]),
    )


def _parse_reporting(raw: dict[str, Any]) -> ReportingConfig:
    return ReportingConfig(
        dashboard_refresh_sec=int(raw["dashboard_refresh_sec"]),
        persist_trades=bool(raw["persist_trades"]),
        persist_quotes=bool(raw["persist_quotes"]),
        recent_log_lines=int(raw["recent_log_lines"]),
    )


def _parse_simulation(raw: dict[str, Any]) -> SimulationConfig:
    return SimulationConfig(
        seed=int(raw["seed"]),
        starting_quote_usd=float(raw["starting_quote_usd"]),
        starting_base_units=float(raw["starting_base_units"]),
        default_drift_bps=float(raw["default_drift_bps"]),
        default_volatility_bps=float(raw["default_volatility_bps"]),
        default_spread_bps=float(raw["default_spread_bps"]),
        initial_price=float(raw["initial_price"]) if "initial_price" in raw else None,
        drift_bps=float(raw["drift_bps"]) if "drift_bps" in raw else None,
        volatility_bps=float(raw["volatility_bps"]) if "volatility_bps" in raw else None,
        spread_bps=float(raw["spread_bps"]) if "spread_bps" in raw else None,
    )


def _parse_symbols(
    raw: dict[str, Any],
    *,
    market: MarketConfig,
    grid: GridConfig,
    inventory: InventoryConfig,
    simulation: SimulationConfig,
) -> dict[str, SymbolConfig]:
    symbol_sections = raw.get("symbols", {})
    default_raw = symbol_sections.get("default", {})
    names = [name for name in symbol_sections if name != "default"]

    if not names:
        names = [name for name in raw["universe"].get("include_symbols", [])]

    parsed: dict[str, SymbolConfig] = {}

    for name in names:
        merged_symbol = _deep_merge(default_raw, symbol_sections.get(name, {}))
        enabled = bool(merged_symbol.get("enabled", True))
        if not enabled:
            continue

        symbol_grid_raw = _deep_merge(copy.deepcopy(raw["grid"]), merged_symbol.get("grid", {}))
        symbol_inventory_raw = _deep_merge(copy.deepcopy(raw["inventory"]), merged_symbol.get("inventory", {}))
        symbol_simulation_raw = _deep_merge(copy.deepcopy(raw["simulation"]), merged_symbol.get("simulation", {}))

        parsed[name] = SymbolConfig(
            name=name,
            enabled=enabled,
            grid=_parse_grid(symbol_grid_raw),
            inventory=_parse_inventory(symbol_inventory_raw),
            simulation=_parse_simulation(symbol_simulation_raw),
            route=_parse_symbol_route(merged_symbol.get("route", {}), market=market),
        )

    return parsed


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
            continue
        merged[key] = copy.deepcopy(value)
    return merged


def _parse_symbol_route(raw: dict[str, Any], *, market: MarketConfig) -> SymbolRouteConfig:
    return SymbolRouteConfig(
        base_token_address=str(raw.get("base_token_address", "")),
        base_token_decimals=(
            int(raw["base_token_decimals"]) if raw.get("base_token_decimals") is not None else None
        ),
        quote_token_address=str(raw.get("quote_token_address", market.quote_token_address)),
        quote_token_decimals=(
            int(raw["quote_token_decimals"])
            if raw.get("quote_token_decimals") is not None
            else market.quote_token_decimals
        ),
        buy_path=[str(item) for item in raw.get("buy_path", [])],
        sell_path=[str(item) for item in raw.get("sell_path", [])],
        fee_on_transfer=bool(raw.get("fee_on_transfer", False)),
    )
