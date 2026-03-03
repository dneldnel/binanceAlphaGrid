from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    strategy_id: str
    mode: str
    refresh_interval_ms: int
    rotation_interval_sec: int
    state_store_path: Path
    log_level: str
    dry_run: bool
    allow_live: bool
    allow_mainnet: bool


@dataclass(frozen=True)
class ChainConfig:
    chain_id: int
    chain_name: str
    rpc_urls: list[str]
    rpc_timeout_sec: int
    wallet_address: str
    private_key_env: str
    explorer_base_url: str


@dataclass(frozen=True)
class MarketConfig:
    quote_token_symbol: str
    quote_token_address: str
    quote_token_decimals: int
    allowed_route_types: list[str]
    probe_quote_usd: float
    max_price_impact_bps: float
    stale_quote_sec: int


@dataclass(frozen=True)
class RouterConfig:
    kind: str
    address: str
    spender_address: str
    router_abi_path: Path | None
    quote_method: str
    buy_swap_method: str
    sell_swap_method: str
    approve_max: bool
    tx_wait_timeout_sec: int
    tx_poll_interval_sec: float


@dataclass(frozen=True)
class UniverseConfig:
    mode: str
    include_symbols: list[str]
    exclude_symbols: list[str]
    max_symbols: int
    min_listing_age_hours: int
    min_pool_liquidity_usd: float
    min_24h_volume_usd: float
    max_token_tax_bps: float
    max_drop_pct_5m: float
    keep_positions_on_rotation: bool


@dataclass(frozen=True)
class ReferencePriceConfig:
    source: str
    ema_alpha: float
    mid_weight: float
    ema_weight: float
    volatility_lookback_sec: int
    volatility_source: str


@dataclass(frozen=True)
class GridLevelConfig:
    level: int
    offset_bps: float
    cooldown_sec: int
    order_quote_usd: float | None = None
    order_base_ratio: float | None = None


@dataclass(frozen=True)
class GridConfig:
    mode: str
    base_step_bps: float
    min_step_bps: float
    max_step_bps: float
    spread_multiplier: float
    volatility_multiplier: float
    rebuild_on_fill: bool
    rebuild_on_reference_change_bps: float
    paired_take_profit_bps: float
    buy_levels: list[GridLevelConfig]
    sell_levels: list[GridLevelConfig]


@dataclass(frozen=True)
class InventoryConfig:
    target_base_ratio: float
    inventory_skew_factor_bps: float
    max_inventory_shift_bps: float
    reserve_base_tokens: float
    max_quote_per_symbol_usd: float
    max_base_exposure_usd: float
    min_quote_reserve_usd: float
    min_sell_base_usd: float
    allow_emergency_sell_reserve: bool


@dataclass(frozen=True)
class ExecutionConfig:
    router: str
    deadline_sec: int
    slippage_bps: float
    max_gas_gwei: float
    replacement_gas_bump_bps: float
    max_gas_usd_per_tx: float
    estimated_fee_bps: float
    min_net_edge_bps: float
    min_expected_profit_usd: float
    single_symbol_single_inflight: bool
    max_inflight_txs: int
    retry_times: int
    retry_backoff_ms: int


@dataclass(frozen=True)
class RiskConfig:
    kill_switch_file: Path | None
    max_notional_per_order: float
    max_position_per_symbol_usd: float
    max_daily_realized_loss_usd: float
    max_daily_gas_usd: float
    max_consecutive_failed_tx: int
    max_failed_tx_per_symbol: int
    max_trades_per_symbol_per_hour: int
    hard_stop_from_cost_bps: float
    pause_on_liquidity_drop: bool
    pause_on_honeypot_signal: bool
    pause_on_route_failure: bool


@dataclass(frozen=True)
class ReportingConfig:
    dashboard_refresh_sec: int
    persist_trades: bool
    persist_quotes: bool
    recent_log_lines: int


@dataclass(frozen=True)
class SimulationConfig:
    seed: int
    starting_quote_usd: float
    starting_base_units: float
    default_drift_bps: float
    default_volatility_bps: float
    default_spread_bps: float
    initial_price: float | None = None
    drift_bps: float | None = None
    volatility_bps: float | None = None
    spread_bps: float | None = None


@dataclass(frozen=True)
class SymbolRouteConfig:
    base_token_address: str
    base_token_decimals: int | None
    quote_token_address: str
    quote_token_decimals: int | None
    buy_path: list[str]
    sell_path: list[str]
    fee_on_transfer: bool


@dataclass(frozen=True)
class SymbolConfig:
    name: str
    enabled: bool
    grid: GridConfig
    inventory: InventoryConfig
    simulation: SimulationConfig
    route: SymbolRouteConfig


@dataclass(frozen=True)
class AppConfig:
    runtime: RuntimeConfig
    chain: ChainConfig
    market: MarketConfig
    router: RouterConfig
    universe: UniverseConfig
    reference_price: ReferencePriceConfig
    grid: GridConfig
    inventory: InventoryConfig
    execution: ExecutionConfig
    risk: RiskConfig
    reporting: ReportingConfig
    simulation: SimulationConfig
    symbols: dict[str, SymbolConfig]


@dataclass
class QuoteSnapshot:
    symbol: str
    ts: datetime
    mid_price: float
    exec_buy_price: float
    exec_sell_price: float
    spread_bps: float


@dataclass
class GridTarget:
    side: str
    level: int
    trigger_price: float
    order_quote_usd: float | None = None
    order_base_ratio: float | None = None
    cooldown_sec: int = 0


@dataclass
class GridPlan:
    buy_targets: list[GridTarget]
    sell_targets: list[GridTarget]
    inventory_shift_bps: float
    step_bps: float


@dataclass
class TradeDecision:
    symbol: str
    side: str
    level: int
    target_price: float
    reason: str
    order_quote_usd: float | None = None
    order_base_ratio: float | None = None
    net_edge_bps: float = 0.0
    allow_reserve_sell: bool = False
    force_exit: bool = False


@dataclass
class ExecutionPreview:
    symbol: str
    side: str
    level: int
    price: float
    base_qty: float
    quote_value: float
    price_impact_bps: float = 0.0
    expected_profit_usd: float = 0.0
    estimated_gas_usd: float | None = None
    amount_in_raw: int | None = None
    expected_out_raw: int | None = None
    amount_out_min_raw: int | None = None
    approve_token_address: str | None = None
    path: list[str] = field(default_factory=list)


@dataclass
class TradeFill:
    symbol: str
    side: str
    level: int
    price: float
    base_qty: float
    quote_value: float
    realized_pnl: float
    message: str
    tx_hash: str | None = None
    approve_tx_hash: str | None = None
    tx_nonce: int | None = None
    approve_nonce: int | None = None
    tx_gas_price_wei: int | None = None
    approve_gas_price_wei: int | None = None


@dataclass
class SymbolState:
    symbol: str
    status: str
    base_balance: float
    quote_balance_usd: float
    reserve_base_tokens: float
    reference_price: float
    ema_price: float
    last_mid_price: float
    spread_bps: float
    volatility_bps: float
    buy_basis_price: float
    sell_basis_price: float
    avg_cost_price: float
    realized_pnl: float
    unrealized_pnl: float
    daily_trade_count: int
    buy_open_count: int
    buy_done_count: int
    sell_open_count: int
    sell_done_count: int
    recent_mid_prices: deque[float] = field(default_factory=lambda: deque(maxlen=512))
    recent_logs: deque[str] = field(default_factory=lambda: deque(maxlen=20))
