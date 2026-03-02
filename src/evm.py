from __future__ import annotations

import os
from decimal import Decimal, ROUND_DOWN
from typing import Any

from core.models import AppConfig, SymbolConfig

try:
    from web3 import HTTPProvider, Web3
except ImportError:  # pragma: no cover - optional dependency
    HTTPProvider = None
    Web3 = None


ERC20_ABI: list[dict[str, Any]] = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


UNISWAP_V2_ROUTER_ABI: list[dict[str, Any]] = [
    {
        "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
        "name": "getAmountsOut",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


class LiveModeError(RuntimeError):
    """Raised when live trading prerequisites are not satisfied."""


class EvmRouterClient:
    def __init__(self, config: AppConfig, *, read_only: bool = False) -> None:
        if Web3 is None or HTTPProvider is None:
            raise LiveModeError(
                "web3.py is not installed. Install with: pip install 'web3>=7,<8'"
            )
        if not read_only and not config.runtime.allow_live:
            raise LiveModeError("runtime.allow_live is false. Refusing to initialize live mode.")
        if not read_only and config.chain.chain_id == 56 and not config.runtime.allow_mainnet:
            raise LiveModeError("runtime.allow_mainnet is false for BSC mainnet.")
        if not config.router.address:
            raise LiveModeError("router.address is required for live mode.")
        if not read_only and not config.chain.wallet_address:
            raise LiveModeError("chain.wallet_address is required for live mode.")

        provider = HTTPProvider(
            config.chain.rpc_urls[0],
            request_kwargs={"timeout": config.chain.rpc_timeout_sec},
        )
        self.w3 = Web3(provider)
        if not self.w3.is_connected():
            raise LiveModeError(f"Could not connect to RPC: {config.chain.rpc_urls[0]}")

        self.config = config
        self.read_only = read_only
        self.private_key: str | None = None
        self.wallet_address: str | None = None
        self.router_address = self.to_checksum(config.router.address)
        spender_address = config.router.spender_address or config.router.address
        self.spender_address = self.to_checksum(spender_address)
        self.router = self.w3.eth.contract(address=self.router_address, abi=UNISWAP_V2_ROUTER_ABI)
        self.account = None

        if not read_only:
            private_key = os.getenv(config.chain.private_key_env)
            if not private_key:
                raise LiveModeError(
                    f"Environment variable {config.chain.private_key_env} is missing for live mode."
                )
            self.private_key = private_key
            self.wallet_address = self.to_checksum(config.chain.wallet_address)
            self.account = self.w3.eth.account.from_key(private_key)
            if self.account.address != self.wallet_address:
                raise LiveModeError(
                    "chain.wallet_address does not match the private key loaded from the environment."
                )

        actual_chain_id = int(self.w3.eth.chain_id)
        if actual_chain_id != config.chain.chain_id:
            raise LiveModeError(
                f"RPC chain id mismatch: expected {config.chain.chain_id}, got {actual_chain_id}"
            )

        self._decimals_cache: dict[str, int] = {}
        self._erc20_cache: dict[str, Any] = {}

    def to_checksum(self, address: str) -> str:
        return self.w3.to_checksum_address(address)

    def validate_symbol(self, symbol: SymbolConfig) -> None:
        if not symbol.route.base_token_address:
            raise LiveModeError(f"{symbol.name}: route.base_token_address is required.")
        if not symbol.route.buy_path:
            raise LiveModeError(f"{symbol.name}: route.buy_path is required.")
        if not symbol.route.sell_path:
            raise LiveModeError(f"{symbol.name}: route.sell_path is required.")

    def get_token_decimals(self, token_address: str, configured: int | None = None) -> int:
        if configured is not None:
            return configured
        checksum = self.to_checksum(token_address)
        if checksum in self._decimals_cache:
            return self._decimals_cache[checksum]
        contract = self.erc20_contract(checksum)
        decimals = int(contract.functions.decimals().call())
        self._decimals_cache[checksum] = decimals
        return decimals

    def erc20_contract(self, token_address: str) -> Any:
        checksum = self.to_checksum(token_address)
        if checksum not in self._erc20_cache:
            self._erc20_cache[checksum] = self.w3.eth.contract(address=checksum, abi=ERC20_ABI)
        return self._erc20_cache[checksum]

    def get_token_balance_raw(self, token_address: str) -> int:
        if self.wallet_address is None:
            raise LiveModeError("wallet_address is unavailable in read-only mode.")
        token = self.erc20_contract(token_address)
        return int(token.functions.balanceOf(self.wallet_address).call())

    def to_raw_amount(self, amount: float, decimals: int) -> int:
        scaled = Decimal(str(amount)) * (Decimal(10) ** decimals)
        return int(scaled.to_integral_value(rounding=ROUND_DOWN))

    def from_raw_amount(self, amount_raw: int, decimals: int) -> float:
        return float(Decimal(amount_raw) / (Decimal(10) ** decimals))

    def get_amounts_out(self, amount_in_raw: int, path: list[str]) -> list[int]:
        checksum_path = [self.to_checksum(item) for item in path]
        method = getattr(self.router.functions, self.config.router.quote_method)
        amounts = method(amount_in_raw, checksum_path).call()
        return [int(item) for item in amounts]

    def ensure_allowance(self, token_address: str, min_amount_raw: int) -> str | None:
        self.require_write_mode()
        token = self.erc20_contract(token_address)
        assert self.wallet_address is not None
        allowance = int(token.functions.allowance(self.wallet_address, self.spender_address).call())
        if allowance >= min_amount_raw:
            return None

        approve_amount = (2**256 - 1) if self.config.router.approve_max else min_amount_raw
        function_call = token.functions.approve(self.spender_address, approve_amount)
        return self.send_transaction(function_call)

    def swap_exact_tokens_for_tokens(
        self,
        *,
        amount_in_raw: int,
        amount_out_min_raw: int,
        path: list[str],
        side: str,
    ) -> str:
        self.require_write_mode()
        checksum_path = [self.to_checksum(item) for item in path]
        method_name = (
            self.config.router.buy_swap_method if side == "buy" else self.config.router.sell_swap_method
        )
        deadline = int(self.w3.eth.get_block("latest")["timestamp"]) + self.config.execution.deadline_sec
        assert self.wallet_address is not None
        function_call = getattr(self.router.functions, method_name)(
            amount_in_raw,
            amount_out_min_raw,
            checksum_path,
            self.wallet_address,
            deadline,
        )
        return self.send_transaction(function_call)

    def send_transaction(self, function_call: Any) -> str:
        self.require_write_mode()
        gas_price = int(self.w3.eth.gas_price)
        max_gas_price = self.w3.to_wei(self.config.execution.max_gas_gwei, "gwei")
        if gas_price > max_gas_price:
            raise LiveModeError(
                f"Current gas price {gas_price} is above configured max_gas_gwei {self.config.execution.max_gas_gwei}"
            )

        assert self.wallet_address is not None
        assert self.account is not None
        nonce = self.w3.eth.get_transaction_count(self.wallet_address)
        tx = function_call.build_transaction(
            {
                "from": self.wallet_address,
                "chainId": self.config.chain.chain_id,
                "nonce": nonce,
                "gasPrice": gas_price,
            }
        )
        estimated_gas = int(self.w3.eth.estimate_gas(tx))
        tx["gas"] = int(estimated_gas * 1.15)

        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(
            tx_hash,
            timeout=self.config.router.tx_wait_timeout_sec,
            poll_latency=self.config.router.tx_poll_interval_sec,
        )
        if int(receipt["status"]) != 1:
            raise LiveModeError(f"Transaction reverted: {tx_hash.hex()}")
        return tx_hash.hex()

    def require_write_mode(self) -> None:
        if self.read_only:
            raise LiveModeError("Read-only router client cannot send transactions.")
