from __future__ import annotations

from core.models import AppConfig, SymbolConfig


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def ensure_deployable_chain_config(config: AppConfig) -> None:
    if config.runtime.mode not in {"paper", "live"}:
        return

    errors: list[str] = []

    if _is_zero_address(config.chain.wallet_address):
        errors.append("chain.wallet_address is placeholder or missing")
    if _is_zero_address(config.router.address):
        errors.append("router.address is placeholder or missing")
    if _is_zero_address(config.router.spender_address):
        errors.append("router.spender_address is placeholder or missing")

    for symbol in config.symbols.values():
        errors.extend(_validate_symbol_route(symbol))

    if errors:
        rendered = "\n - ".join(errors)
        raise ValueError(f"paper/live config is not deployable:\n - {rendered}")


def _validate_symbol_route(symbol: SymbolConfig) -> list[str]:
    route = symbol.route
    errors: list[str] = []

    if _is_zero_address(route.base_token_address):
        errors.append(f"{symbol.name}: route.base_token_address is placeholder or missing")
    if _is_zero_address(route.quote_token_address):
        errors.append(f"{symbol.name}: route.quote_token_address is placeholder or missing")

    if not route.buy_path:
        errors.append(f"{symbol.name}: route.buy_path is required")
    else:
        if any(_is_zero_address(address) for address in route.buy_path):
            errors.append(f"{symbol.name}: route.buy_path contains placeholder address")
        if not _is_zero_address(route.quote_token_address):
            if route.buy_path[0].lower() != route.quote_token_address.lower():
                errors.append(f"{symbol.name}: route.buy_path must start with quote token")
        if not _is_zero_address(route.base_token_address):
            if route.buy_path[-1].lower() != route.base_token_address.lower():
                errors.append(f"{symbol.name}: route.buy_path must end with base token")

    if not route.sell_path:
        errors.append(f"{symbol.name}: route.sell_path is required")
    else:
        if any(_is_zero_address(address) for address in route.sell_path):
            errors.append(f"{symbol.name}: route.sell_path contains placeholder address")
        if not _is_zero_address(route.base_token_address):
            if route.sell_path[0].lower() != route.base_token_address.lower():
                errors.append(f"{symbol.name}: route.sell_path must start with base token")
        if not _is_zero_address(route.quote_token_address):
            if route.sell_path[-1].lower() != route.quote_token_address.lower():
                errors.append(f"{symbol.name}: route.sell_path must end with quote token")

    return errors


def _is_zero_address(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or normalized == ZERO_ADDRESS.lower()
