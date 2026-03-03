from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

from core.models import AppConfig, SymbolConfig
from evm import EvmRouterClient


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True)
class PreflightCheck:
    level: str
    label: str
    detail: str


class PreflightRunner:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.checks: list[PreflightCheck] = []

    def run(self) -> int:
        self._check_runtime_guards()
        self._check_kill_switch()

        client = self._build_client()
        if client is None:
            return self._finish()

        self._check_wallet(client)
        self._check_router(client)
        for symbol in self.config.symbols.values():
            self._check_symbol(client, symbol)
        self._check_txpool_support(client)
        return self._finish()

    def _build_client(self) -> EvmRouterClient | None:
        try:
            client = EvmRouterClient(self.config, read_only=True)
        except Exception as exc:
            self._fail("rpc/client", str(exc))
            return None

        self._pass(
            "rpc/client",
            f"connected chain_id={client.w3.eth.chain_id} rpc={self.config.chain.rpc_urls[0]}",
        )
        return client

    def _check_runtime_guards(self) -> None:
        runtime = self.config.runtime
        risk = self.config.risk

        if self.config.chain.chain_id == 56:
            self._pass("chain target", "BSC mainnet")
        else:
            self._warn("chain target", f"chain_id={self.config.chain.chain_id} is not BSC mainnet")

        if runtime.mode == "live":
            self._pass("runtime.mode", "live")
        else:
            self._warn("runtime.mode", f"{runtime.mode}; switch to live before real trading")

        if runtime.allow_live:
            self._pass("runtime.allow_live", "true")
        else:
            self._warn("runtime.allow_live", "false; live writes remain blocked")

        if self.config.chain.chain_id != 56 or runtime.allow_mainnet:
            self._pass("runtime.allow_mainnet", str(runtime.allow_mainnet).lower())
        else:
            self._warn("runtime.allow_mainnet", "false; mainnet writes remain blocked")

        buy_enabled = risk.mainnet_buy_enabled
        sell_enabled = risk.mainnet_sell_enabled
        if self.config.chain.chain_id == 56:
            if buy_enabled ^ sell_enabled:
                enabled_side = "buy" if buy_enabled else "sell"
                self._pass("mainnet side gate", f"single-sided: {enabled_side}")
            elif not buy_enabled and not sell_enabled:
                self._warn("mainnet side gate", "both buy/sell disabled; no regular trades can fire")
            else:
                self._warn("mainnet side gate", "both buy/sell enabled; not minimal single-sided")

            self._pass(
                "mainnet rollout cap",
                (
                    "max_notional="
                    f"{risk.mainnet_max_notional_per_order:.4f} "
                    "max_position="
                    f"{risk.mainnet_max_position_per_symbol_usd:.4f}"
                ),
            )

    def _check_kill_switch(self) -> None:
        path = self.config.risk.kill_switch_file
        if path is None:
            self._warn("kill switch", "not configured")
            return
        if path.exists():
            self._fail("kill switch", f"active: {path}")
            return
        parent = path.parent
        if parent.exists():
            self._pass("kill switch", f"armed path={path}")
            return
        self._warn("kill switch", f"parent directory missing: {parent}")

    def _check_wallet(self, client: EvmRouterClient) -> None:
        wallet_address = self.config.chain.wallet_address.strip()
        if not wallet_address or self._is_zero_address(wallet_address):
            self._fail("wallet address", "placeholder or missing")
            return

        self._pass("wallet address", wallet_address)

        env_name = self.config.chain.private_key_env
        private_key = os.getenv(env_name)
        if not private_key:
            self._warn("private key env", f"{env_name} is not set")
        else:
            try:
                derived = client.w3.eth.account.from_key(private_key).address
            except Exception as exc:
                self._fail("private key env", f"{env_name} invalid: {exc}")
            else:
                if derived != client.wallet_address:
                    self._fail(
                        "private key env",
                        f"{env_name} derives {derived}, config expects {client.wallet_address}",
                    )
                else:
                    self._pass("private key env", f"{env_name} matches wallet")

        try:
            native_balance_wei = client.get_native_balance_wei()
        except Exception as exc:
            self._fail("native balance", str(exc))
        else:
            native_balance = Decimal(native_balance_wei) / (Decimal(10) ** 18)
            if native_balance_wei > 0:
                self._pass("native balance", f"{native_balance:.8f} BNB")
            else:
                self._warn("native balance", "0 BNB; no gas available")

        try:
            latest_nonce, pending_nonce = client.get_wallet_nonce_state()
        except Exception as exc:
            self._fail("wallet nonce", str(exc))
        else:
            detail = f"latest={latest_nonce} pending={pending_nonce}"
            if pending_nonce >= latest_nonce:
                self._pass("wallet nonce", detail)
            else:
                self._warn("wallet nonce", detail)

    def _check_router(self, client: EvmRouterClient) -> None:
        router_address = self.config.router.address.strip()
        spender_address = self.config.router.spender_address.strip()
        if self._is_zero_address(router_address):
            self._fail("router address", "placeholder zero address")
        else:
            self._pass("router address", router_address)
        if self._is_zero_address(spender_address):
            self._fail("spender address", "placeholder zero address")
        else:
            self._pass("spender address", spender_address)

        try:
            wrapped_native = client.get_wrapped_native_token_address()
        except Exception as exc:
            self._fail("router WETH()", str(exc))
        else:
            self._pass("router WETH()", wrapped_native)

    def _check_symbol(self, client: EvmRouterClient, symbol: SymbolConfig) -> None:
        prefix = f"symbol {symbol.name}"

        try:
            client.validate_symbol(symbol)
        except Exception as exc:
            self._fail(prefix, str(exc))
            return

        route_addresses = (
            ("base token", symbol.route.base_token_address),
            ("quote token", symbol.route.quote_token_address),
        )
        for label, address in route_addresses:
            if self._is_zero_address(address):
                self._fail(prefix, f"{label} is placeholder zero address")
                return

        try:
            quote_decimals = client.get_token_decimals(
                symbol.route.quote_token_address,
                symbol.route.quote_token_decimals,
            )
            base_decimals = client.get_token_decimals(
                symbol.route.base_token_address,
                symbol.route.base_token_decimals,
            )
        except Exception as exc:
            self._fail(prefix, f"token metadata failed: {exc}")
            return

        self._pass(prefix, f"decimals base={base_decimals} quote={quote_decimals}")

        buy_probe_raw = client.to_raw_amount(self.config.market.probe_quote_usd, quote_decimals)
        try:
            buy_amounts = client.get_amounts_out(buy_probe_raw, symbol.route.buy_path)
        except Exception as exc:
            self._fail(prefix, f"buy quote failed: {exc}")
        else:
            base_out = client.from_raw_amount(int(buy_amounts[-1]), base_decimals)
            self._pass(prefix, f"buy quote ok probe={self.config.market.probe_quote_usd:.4f} base_out={base_out:.8f}")

        sell_probe_base = self.config.market.probe_quote_usd / max(
            symbol.simulation.initial_price or 1.0,
            1e-8,
        )
        sell_probe_raw = client.to_raw_amount(sell_probe_base, base_decimals)
        try:
            sell_amounts = client.get_amounts_out(sell_probe_raw, symbol.route.sell_path)
        except Exception as exc:
            self._fail(prefix, f"sell quote failed: {exc}")
        else:
            quote_out = client.from_raw_amount(int(sell_amounts[-1]), quote_decimals)
            self._pass(prefix, f"sell quote ok probe_base={sell_probe_base:.8f} quote_out={quote_out:.8f}")

        if client.wallet_address is None:
            return

        try:
            quote_balance_raw = client.get_token_balance_raw(symbol.route.quote_token_address)
            base_balance_raw = client.get_token_balance_raw(symbol.route.base_token_address)
        except Exception as exc:
            self._fail(prefix, f"wallet token balance failed: {exc}")
        else:
            quote_balance = client.from_raw_amount(quote_balance_raw, quote_decimals)
            base_balance = client.from_raw_amount(base_balance_raw, base_decimals)
            self._pass(prefix, f"balances base={base_balance:.8f} quote={quote_balance:.8f}")

        try:
            quote_allowance_raw = client.get_allowance_raw(symbol.route.quote_token_address)
            base_allowance_raw = client.get_allowance_raw(symbol.route.base_token_address)
        except Exception as exc:
            self._fail(prefix, f"allowance query failed: {exc}")
        else:
            quote_allowance = client.from_raw_amount(quote_allowance_raw, quote_decimals)
            base_allowance = client.from_raw_amount(base_allowance_raw, base_decimals)
            self._pass(
                prefix,
                f"allowance base={base_allowance:.8f} quote={quote_allowance:.8f}",
            )

    def _check_txpool_support(self, client: EvmRouterClient) -> None:
        if client.wallet_address is None:
            return
        try:
            pending_map = client.get_wallet_pending_transactions_by_nonce()
        except Exception as exc:
            self._warn("txpool support", str(exc))
            return
        if pending_map is None:
            self._warn("txpool support", "provider does not expose txpool/pending-block wallet view")
            return
        self._pass("txpool support", f"available nonces={len(pending_map)}")

    def _finish(self) -> int:
        print("")
        print("Live Preflight")
        print("=" * 80)
        for check in self.checks:
            print(f"[{check.level}] {check.label}: {check.detail}")
        print("-" * 80)
        fail_count = sum(1 for check in self.checks if check.level == "FAIL")
        warn_count = sum(1 for check in self.checks if check.level == "WARN")
        pass_count = sum(1 for check in self.checks if check.level == "PASS")
        print(f"summary: pass={pass_count} warn={warn_count} fail={fail_count}")
        return 0 if fail_count == 0 else 2

    def _pass(self, label: str, detail: str) -> None:
        self.checks.append(PreflightCheck("PASS", label, detail))

    def _warn(self, label: str, detail: str) -> None:
        self.checks.append(PreflightCheck("WARN", label, detail))

    def _fail(self, label: str, detail: str) -> None:
        self.checks.append(PreflightCheck("FAIL", label, detail))

    def _is_zero_address(self, value: str | None) -> bool:
        if value is None:
            return True
        return value.strip().lower() == ZERO_ADDRESS


def run_preflight(config: AppConfig) -> int:
    return PreflightRunner(config).run()
