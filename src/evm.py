from __future__ import annotations

import json
import os
from dataclasses import dataclass
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
        "inputs": [],
        "name": "WETH",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
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

TRANSFER_EVENT_TOPIC0 = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)


class LiveModeError(RuntimeError):
    """Raised when live trading prerequisites are not satisfied."""


@dataclass(frozen=True)
class SubmittedTransaction:
    tx_hash: str
    nonce: int
    gas_price_wei: int


@dataclass(frozen=True)
class PendingPoolTransaction:
    tx_hash: str
    nonce: int
    from_address: str
    to_address: str | None
    value_wei: int
    input_data: str
    gas_price_wei: int | None
    source: str

    def is_zero_value_self_transfer(self, wallet_address: str | None) -> bool:
        if not wallet_address:
            return False
        wallet_lower = wallet_address.lower()
        to_address = (self.to_address or "").lower()
        input_data = self.input_data.lower()
        return (
            self.from_address.lower() == wallet_lower
            and to_address == wallet_lower
            and self.value_wei == 0
            and input_data in {"", "0x", "0x0", "0x00"}
        )


class TransactionSendError(LiveModeError):
    def __init__(
        self,
        message: str,
        *,
        tx_hash: str | None = None,
        nonce: int | None = None,
        gas_price_wei: int | None = None,
    ) -> None:
        super().__init__(message)
        self.tx_hash = tx_hash
        self.nonce = nonce
        self.gas_price_wei = gas_price_wei


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
        if not read_only and not config.chain.wallet_address.strip():
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
        self.router = self.w3.eth.contract(
            address=self.router_address,
            abi=self._load_router_abi(),
        )
        self.account = None
        configured_wallet_address = config.chain.wallet_address.strip()

        if configured_wallet_address:
            try:
                self.wallet_address = self.to_checksum(configured_wallet_address)
            except ValueError as exc:
                raise LiveModeError(
                    f"Invalid chain.wallet_address: {configured_wallet_address}"
                ) from exc

        if not read_only:
            private_key = os.getenv(config.chain.private_key_env)
            if not private_key:
                raise LiveModeError(
                    f"Environment variable {config.chain.private_key_env} is missing for live mode."
                )
            self.private_key = private_key
            if self.wallet_address is None:
                raise LiveModeError("chain.wallet_address is required for live mode.")
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
        self._wrapped_native_token_address: str | None = None

    def _load_router_abi(self) -> list[dict[str, Any]]:
        abi_path = self.config.router.router_abi_path
        if abi_path is None:
            return UNISWAP_V2_ROUTER_ABI
        try:
            payload = json.loads(abi_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise LiveModeError(f"Failed reading router ABI file {abi_path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LiveModeError(f"Failed parsing router ABI file {abi_path}: {exc}") from exc

        if isinstance(payload, dict) and "abi" in payload:
            payload = payload["abi"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise LiveModeError(
                    f"router ABI payload in {abi_path} is not valid JSON: {exc}"
                ) from exc
        if not isinstance(payload, list):
            raise LiveModeError(
                f"router ABI file {abi_path} must contain a JSON ABI array or an object with an abi field."
            )
        return payload

    def to_checksum(self, address: str) -> str:
        return self.w3.to_checksum_address(address)

    def validate_symbol(self, symbol: SymbolConfig) -> None:
        if not symbol.route.base_token_address:
            raise LiveModeError(f"{symbol.name}: route.base_token_address is required.")
        if symbol.route.base_token_address.lower() == "0x0000000000000000000000000000000000000000":
            raise LiveModeError(f"{symbol.name}: route.base_token_address is placeholder.")
        if not symbol.route.quote_token_address:
            raise LiveModeError(f"{symbol.name}: route.quote_token_address is required.")
        if symbol.route.quote_token_address.lower() == "0x0000000000000000000000000000000000000000":
            raise LiveModeError(f"{symbol.name}: route.quote_token_address is placeholder.")
        if not symbol.route.buy_path:
            raise LiveModeError(f"{symbol.name}: route.buy_path is required.")
        if not symbol.route.sell_path:
            raise LiveModeError(f"{symbol.name}: route.sell_path is required.")
        if any(
            address.lower() == "0x0000000000000000000000000000000000000000"
            for address in symbol.route.buy_path
        ):
            raise LiveModeError(f"{symbol.name}: route.buy_path contains placeholder address.")
        if any(
            address.lower() == "0x0000000000000000000000000000000000000000"
            for address in symbol.route.sell_path
        ):
            raise LiveModeError(f"{symbol.name}: route.sell_path contains placeholder address.")
        if symbol.route.buy_path[0].lower() != symbol.route.quote_token_address.lower():
            raise LiveModeError(f"{symbol.name}: route.buy_path must start with quote token.")
        if symbol.route.buy_path[-1].lower() != symbol.route.base_token_address.lower():
            raise LiveModeError(f"{symbol.name}: route.buy_path must end with base token.")
        if symbol.route.sell_path[0].lower() != symbol.route.base_token_address.lower():
            raise LiveModeError(f"{symbol.name}: route.sell_path must start with base token.")
        if symbol.route.sell_path[-1].lower() != symbol.route.quote_token_address.lower():
            raise LiveModeError(f"{symbol.name}: route.sell_path must end with quote token.")

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
            raise LiveModeError("wallet_address is required to query token balances.")
        token = self.erc20_contract(token_address)
        return int(token.functions.balanceOf(self.wallet_address).call())

    def get_native_balance_wei(self, wallet_address: str | None = None) -> int:
        address = wallet_address or self.wallet_address
        if address is None:
            raise LiveModeError("wallet_address is required to query native balance.")
        return int(self.w3.eth.get_balance(self.to_checksum(address)))

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

    def get_allowance_raw(self, token_address: str) -> int:
        if self.wallet_address is None:
            raise LiveModeError("wallet_address is required to query allowance.")
        token = self.erc20_contract(token_address)
        return int(token.functions.allowance(self.wallet_address, self.spender_address).call())

    def get_wallet_transaction_count(self, block_identifier: str = "latest") -> int:
        if self.wallet_address is None:
            raise LiveModeError("wallet_address is required to query nonces.")
        return int(self.w3.eth.get_transaction_count(self.wallet_address, block_identifier))

    def get_wallet_nonce_state(self) -> tuple[int, int]:
        return (
            self.get_wallet_transaction_count("latest"),
            self.get_wallet_transaction_count("pending"),
        )

    def ensure_allowance(
        self,
        token_address: str,
        min_amount_raw: int,
        *,
        nonce: int | None = None,
        min_gas_price_wei: int | None = None,
    ) -> SubmittedTransaction | None:
        self.require_write_mode()
        token = self.erc20_contract(token_address)
        allowance = self.get_allowance_raw(token_address)
        if allowance >= min_amount_raw:
            return None

        approve_amount = (2**256 - 1) if self.config.router.approve_max else min_amount_raw
        function_call = token.functions.approve(self.spender_address, approve_amount)
        return self.send_transaction(
            function_call,
            nonce=nonce,
            min_gas_price_wei=min_gas_price_wei,
        )

    def estimate_approve_gas_cost_usd(self, token_address: str, min_amount_raw: int) -> float | None:
        self.require_write_mode()
        token = self.erc20_contract(token_address)
        assert self.wallet_address is not None
        allowance = int(token.functions.allowance(self.wallet_address, self.spender_address).call())
        if allowance >= min_amount_raw:
            return 0.0

        approve_amount = (2**256 - 1) if self.config.router.approve_max else min_amount_raw
        function_call = token.functions.approve(self.spender_address, approve_amount)
        return self._estimate_function_gas_cost_usd(function_call)

    def swap_exact_tokens_for_tokens(
        self,
        *,
        amount_in_raw: int,
        amount_out_min_raw: int,
        path: list[str],
        side: str,
        nonce: int | None = None,
        min_gas_price_wei: int | None = None,
    ) -> SubmittedTransaction:
        self.require_write_mode()
        function_call = self._build_swap_function_call(
            amount_in_raw=amount_in_raw,
            amount_out_min_raw=amount_out_min_raw,
            path=path,
            side=side,
        )
        return self.send_transaction(
            function_call,
            nonce=nonce,
            min_gas_price_wei=min_gas_price_wei,
        )

    def estimate_swap_gas_cost_usd(
        self,
        *,
        amount_in_raw: int,
        amount_out_min_raw: int,
        path: list[str],
        side: str,
    ) -> float | None:
        self.require_write_mode()
        function_call = self._build_swap_function_call(
            amount_in_raw=amount_in_raw,
            amount_out_min_raw=amount_out_min_raw,
            path=path,
            side=side,
        )
        return self._estimate_function_gas_cost_usd(function_call)

    def estimate_swap_bundle_gas_cost_usd(
        self,
        *,
        approve_token_address: str,
        min_approve_amount_raw: int,
        amount_in_raw: int,
        amount_out_min_raw: int,
        path: list[str],
        side: str,
    ) -> float | None:
        self.require_write_mode()
        approve_cost = self.estimate_approve_gas_cost_usd(
            approve_token_address,
            min_approve_amount_raw,
        )
        if approve_cost is None:
            return None

        swap_cost = self.estimate_swap_gas_cost_usd(
            amount_in_raw=amount_in_raw,
            amount_out_min_raw=amount_out_min_raw,
            path=path,
            side=side,
        )
        if swap_cost is None:
            return None

        return approve_cost + swap_cost

    def _build_swap_function_call(
        self,
        *,
        amount_in_raw: int,
        amount_out_min_raw: int,
        path: list[str],
        side: str,
    ) -> Any:
        self.require_write_mode()
        checksum_path = [self.to_checksum(item) for item in path]
        method_name = (
            self.config.router.buy_swap_method if side == "buy" else self.config.router.sell_swap_method
        )
        deadline = int(self.w3.eth.get_block("latest")["timestamp"]) + self.config.execution.deadline_sec
        assert self.wallet_address is not None
        return getattr(self.router.functions, method_name)(
            amount_in_raw,
            amount_out_min_raw,
            checksum_path,
            self.wallet_address,
            deadline,
        )

    def send_transaction(
        self,
        function_call: Any,
        *,
        nonce: int | None = None,
        min_gas_price_wei: int | None = None,
    ) -> SubmittedTransaction:
        self.require_write_mode()
        gas_price = self._resolve_gas_price_wei(min_gas_price_wei=min_gas_price_wei)
        max_gas_price = self.w3.to_wei(self.config.execution.max_gas_gwei, "gwei")
        if gas_price > max_gas_price:
            raise LiveModeError(
                f"Requested gas price {gas_price} is above configured max_gas_gwei {self.config.execution.max_gas_gwei}"
            )

        assert self.wallet_address is not None
        assert self.account is not None
        tx_nonce = nonce
        if tx_nonce is None:
            tx_nonce = self.get_wallet_transaction_count("pending")
        tx = function_call.build_transaction(
            {
                "from": self.wallet_address,
                "chainId": self.config.chain.chain_id,
                "nonce": tx_nonce,
                "gasPrice": gas_price,
            }
        )
        estimated_gas = int(self.w3.eth.estimate_gas(tx))
        tx["gas"] = int(estimated_gas * 1.15)

        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hash_hex = tx_hash.hex()
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(
                tx_hash,
                timeout=self.config.router.tx_wait_timeout_sec,
                poll_latency=self.config.router.tx_poll_interval_sec,
            )
        except Exception as exc:
            raise TransactionSendError(
                f"Failed waiting for receipt: {exc}",
                tx_hash=tx_hash_hex,
                nonce=tx_nonce,
                gas_price_wei=gas_price,
            ) from exc
        if int(receipt["status"]) != 1:
            raise TransactionSendError(
                f"Transaction reverted: {tx_hash_hex}",
                tx_hash=tx_hash_hex,
                nonce=tx_nonce,
                gas_price_wei=gas_price,
            )
        return SubmittedTransaction(tx_hash=tx_hash_hex, nonce=tx_nonce, gas_price_wei=gas_price)

    def send_native_transfer(
        self,
        *,
        to_address: str,
        value_wei: int,
        nonce: int | None = None,
        min_gas_price_wei: int | None = None,
    ) -> SubmittedTransaction:
        self.require_write_mode()
        gas_price = self._resolve_gas_price_wei(min_gas_price_wei=min_gas_price_wei)
        max_gas_price = self.w3.to_wei(self.config.execution.max_gas_gwei, "gwei")
        if gas_price > max_gas_price:
            raise LiveModeError(
                f"Requested gas price {gas_price} is above configured max_gas_gwei {self.config.execution.max_gas_gwei}"
            )

        assert self.wallet_address is not None
        assert self.account is not None
        tx_nonce = nonce
        if tx_nonce is None:
            tx_nonce = self.get_wallet_transaction_count("pending")
        tx = {
            "from": self.wallet_address,
            "to": self.to_checksum(to_address),
            "value": int(value_wei),
            "chainId": self.config.chain.chain_id,
            "nonce": tx_nonce,
            "gasPrice": gas_price,
        }
        estimated_gas = int(self.w3.eth.estimate_gas(tx))
        tx["gas"] = int(estimated_gas * 1.15)

        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hash_hex = tx_hash.hex()
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(
                tx_hash,
                timeout=self.config.router.tx_wait_timeout_sec,
                poll_latency=self.config.router.tx_poll_interval_sec,
            )
        except Exception as exc:
            raise TransactionSendError(
                f"Failed waiting for receipt: {exc}",
                tx_hash=tx_hash_hex,
                nonce=tx_nonce,
                gas_price_wei=gas_price,
            ) from exc
        if int(receipt["status"]) != 1:
            raise TransactionSendError(
                f"Transaction reverted: {tx_hash_hex}",
                tx_hash=tx_hash_hex,
                nonce=tx_nonce,
                gas_price_wei=gas_price,
            )
        return SubmittedTransaction(tx_hash=tx_hash_hex, nonce=tx_nonce, gas_price_wei=gas_price)

    def cancel_transaction(
        self,
        *,
        nonce: int,
        min_gas_price_wei: int | None = None,
    ) -> SubmittedTransaction:
        if self.wallet_address is None:
            raise LiveModeError("wallet_address is required to cancel pending transactions.")
        return self.send_native_transfer(
            to_address=self.wallet_address,
            value_wei=0,
            nonce=nonce,
            min_gas_price_wei=min_gas_price_wei,
        )

    def _estimate_function_gas_cost_usd(self, function_call: Any) -> float | None:
        self.require_write_mode()
        if self.wallet_address is None:
            raise LiveModeError("wallet_address is required to estimate gas.")

        quote_per_native = self._quote_value_per_native_token()
        if quote_per_native is None:
            return None

        gas_price = int(self.w3.eth.gas_price)
        tx = function_call.build_transaction(
            {
                "from": self.wallet_address,
                "chainId": self.config.chain.chain_id,
                "nonce": self.get_wallet_transaction_count("pending"),
                "gasPrice": gas_price,
            }
        )
        estimated_gas = int(self.w3.eth.estimate_gas(tx))
        buffered_gas = int(estimated_gas * 1.15)
        native_cost = (Decimal(buffered_gas) * Decimal(gas_price)) / (Decimal(10) ** 18)
        return float(native_cost * Decimal(str(quote_per_native)))

    def _resolve_gas_price_wei(self, *, min_gas_price_wei: int | None) -> int:
        gas_price = int(self.w3.eth.gas_price)
        if min_gas_price_wei is not None:
            gas_price = max(gas_price, int(min_gas_price_wei))
        return gas_price

    def _quote_value_per_native_token(self) -> float | None:
        wrapped_native = self.get_wrapped_native_token_address()
        quote_token_address = self.to_checksum(self.config.market.quote_token_address)
        if wrapped_native == quote_token_address:
            return 1.0

        try:
            quote_decimals = self.get_token_decimals(
                quote_token_address,
                self.config.market.quote_token_decimals,
            )
            amounts = self.get_amounts_out(10**18, [wrapped_native, quote_token_address])
        except Exception:
            return None
        return self.from_raw_amount(amounts[-1], quote_decimals)

    def get_wrapped_native_token_address(self) -> str:
        if self._wrapped_native_token_address is None:
            self._wrapped_native_token_address = self.to_checksum(self.router.functions.WETH().call())
        return self._wrapped_native_token_address

    def get_transaction_receipt_status(self, tx_hash: str) -> int | None:
        receipt = self.get_transaction_receipt(tx_hash)
        if receipt is None:
            return None
        return int(receipt["status"])

    def get_transaction_receipt(self, tx_hash: str) -> Any | None:
        try:
            return self.w3.eth.get_transaction_receipt(tx_hash)
        except Exception as exc:
            if exc.__class__.__name__ == "TransactionNotFound":
                return None
            raise

    def get_transaction(self, tx_hash: str) -> Any | None:
        try:
            return self.w3.eth.get_transaction(tx_hash)
        except Exception as exc:
            if exc.__class__.__name__ == "TransactionNotFound":
                return None
            raise

    def get_wallet_pending_transactions_by_nonce(
        self,
    ) -> dict[int, list[PendingPoolTransaction]] | None:
        if self.wallet_address is None:
            raise LiveModeError("wallet_address is required to inspect wallet txpool state.")

        loaders = (
            self._load_pending_transactions_from_txpool_content_from,
            self._load_pending_transactions_from_txpool_content,
            self._load_pending_transactions_from_pending_block,
        )
        for loader in loaders:
            pending_txs = loader()
            if pending_txs is None:
                continue
            grouped: dict[int, list[PendingPoolTransaction]] = {}
            for tx in pending_txs:
                grouped.setdefault(tx.nonce, []).append(tx)
            return grouped
        return None

    def _load_pending_transactions_from_txpool_content_from(
        self,
    ) -> list[PendingPoolTransaction] | None:
        result = self._rpc_request_result("txpool_contentFrom", [self.wallet_address])
        if result is None:
            return None
        if not isinstance(result, dict):
            return []

        transactions: dict[str, PendingPoolTransaction] = {}
        for pool_name in ("pending", "queued"):
            nonce_map = result.get(pool_name, {})
            if not isinstance(nonce_map, dict):
                continue
            for entry in nonce_map.values():
                self._collect_txpool_entry_transactions(
                    entry,
                    source=f"txpool_contentFrom:{pool_name}",
                    collector=transactions,
                )
        return list(transactions.values())

    def _load_pending_transactions_from_txpool_content(
        self,
    ) -> list[PendingPoolTransaction] | None:
        result = self._rpc_request_result("txpool_content", [])
        if result is None:
            return None
        if not isinstance(result, dict):
            return []

        wallet_address = self.wallet_address
        assert wallet_address is not None
        transactions: dict[str, PendingPoolTransaction] = {}
        for pool_name in ("pending", "queued"):
            address_map = result.get(pool_name, {})
            if not isinstance(address_map, dict):
                continue
            for raw_address, nonce_map in address_map.items():
                normalized = self._normalize_address(raw_address)
                if normalized is None or normalized.lower() != wallet_address.lower():
                    continue
                self._collect_txpool_entry_transactions(
                    nonce_map,
                    source=f"txpool_content:{pool_name}",
                    collector=transactions,
                )
        return list(transactions.values())

    def _load_pending_transactions_from_pending_block(
        self,
    ) -> list[PendingPoolTransaction] | None:
        result = self._rpc_request_result("eth_getBlockByNumber", ["pending", True])
        if result is None:
            return None
        if not isinstance(result, dict):
            return []

        transactions: dict[str, PendingPoolTransaction] = {}
        for entry in result.get("transactions", []) or []:
            transaction = self._parse_pending_pool_transaction(
                entry,
                tx_hash=(entry or {}).get("hash") if isinstance(entry, dict) else None,
                source="eth_getBlockByNumber:pending",
            )
            if transaction is not None:
                transactions[transaction.tx_hash.lower()] = transaction
        return list(transactions.values())

    def _collect_txpool_entry_transactions(
        self,
        raw: Any,
        *,
        source: str,
        collector: dict[str, PendingPoolTransaction],
    ) -> None:
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            if isinstance(value, dict) and self._looks_like_transaction_entry(value):
                transaction = self._parse_pending_pool_transaction(
                    value,
                    tx_hash=value.get("hash") or key,
                    source=source,
                )
                if transaction is not None:
                    collector[transaction.tx_hash.lower()] = transaction
                continue
            if isinstance(value, dict):
                self._collect_txpool_entry_transactions(
                    value,
                    source=source,
                    collector=collector,
                )

    def _looks_like_transaction_entry(self, raw: dict[str, Any]) -> bool:
        return "nonce" in raw and "from" in raw

    def _parse_pending_pool_transaction(
        self,
        raw: Any,
        *,
        tx_hash: Any,
        source: str,
    ) -> PendingPoolTransaction | None:
        if not isinstance(raw, dict):
            return None
        wallet_address = self.wallet_address
        if wallet_address is None:
            return None

        from_address = self._normalize_address(raw.get("from"))
        if from_address is None or from_address.lower() != wallet_address.lower():
            return None

        nonce = self._parse_rpc_int(raw.get("nonce"))
        if nonce is None:
            return None

        normalized_hash = self._normalize_hash(tx_hash or raw.get("hash"))
        if not normalized_hash:
            return None

        gas_price_wei = self._parse_rpc_int(raw.get("gasPrice"))
        if gas_price_wei is None:
            gas_price_wei = self._parse_rpc_int(raw.get("maxFeePerGas"))
        value_wei = self._parse_rpc_int(raw.get("value")) or 0
        input_data = str(raw.get("input") or raw.get("data") or "0x")
        if input_data and not input_data.startswith("0x"):
            input_data = "0x" + input_data

        return PendingPoolTransaction(
            tx_hash=normalized_hash,
            nonce=nonce,
            from_address=from_address,
            to_address=self._normalize_address(raw.get("to")),
            value_wei=value_wei,
            input_data=input_data,
            gas_price_wei=gas_price_wei,
            source=source,
        )

    def _rpc_request_result(self, method: str, params: list[Any]) -> Any | None:
        provider = getattr(self.w3, "provider", None)
        if provider is None or not hasattr(provider, "make_request"):
            return None
        try:
            response = provider.make_request(method, params)
        except Exception:
            return None
        if not isinstance(response, dict):
            return None
        if response.get("error") is not None:
            return None
        return response.get("result")

    def _parse_rpc_int(self, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            if text.startswith("0x"):
                return int(text, 16)
            return int(text)
        except ValueError:
            return None

    def _normalize_hash(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text.lower()

    def _normalize_address(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return self.to_checksum(text)
        except Exception:
            return text.lower()

    def get_transaction_gas_cost_usd(self, tx_hash: str) -> float | None:
        receipt = self.get_transaction_receipt(tx_hash)
        if receipt is None:
            return None

        gas_used = int(receipt.get("gasUsed", 0) or 0)
        if gas_used <= 0:
            return 0.0

        gas_price_wei = receipt.get("effectiveGasPrice")
        if gas_price_wei is None:
            tx = self.get_transaction(tx_hash)
            if tx is None:
                return None
            gas_price_wei = tx.get("gasPrice")
        if gas_price_wei is None:
            return None

        quote_per_native = self._quote_value_per_native_token()
        if quote_per_native is None:
            return None

        native_cost = (Decimal(gas_used) * Decimal(int(gas_price_wei))) / (Decimal(10) ** 18)
        return float(native_cost * Decimal(str(quote_per_native)))

    def get_erc20_transfer_deltas_raw(
        self,
        tx_hash: str,
        token_addresses: list[str],
    ) -> dict[str, int]:
        if self.wallet_address is None:
            raise LiveModeError("wallet_address is required to parse receipt transfers.")

        receipt = self.get_transaction_receipt(tx_hash)
        if receipt is None:
            raise LiveModeError(f"Receipt not found for tx {tx_hash}")

        tracked_addresses = {
            self.to_checksum(token_address): 0
            for token_address in token_addresses
        }
        tracked_by_lower = {
            checksum.lower(): checksum
            for checksum in tracked_addresses
        }
        wallet_lower = self.wallet_address.lower()

        for log in receipt.get("logs", []):
            log_address = str(log.get("address", "")).lower()
            tracked_address = tracked_by_lower.get(log_address)
            if tracked_address is None:
                continue

            topics = list(log.get("topics", []))
            if len(topics) < 3:
                continue
            if self._topic_hex(topics[0]).lower() != TRANSFER_EVENT_TOPIC0:
                continue

            from_address = self._topic_address(topics[1]).lower()
            to_address = self._topic_address(topics[2]).lower()
            amount_raw = self._hex_to_int(log.get("data", "0x0"))
            if amount_raw <= 0:
                continue
            if from_address == wallet_lower:
                tracked_addresses[tracked_address] -= amount_raw
            if to_address == wallet_lower:
                tracked_addresses[tracked_address] += amount_raw

        return tracked_addresses

    def _topic_hex(self, topic: Any) -> str:
        if isinstance(topic, bytes):
            return "0x" + topic.hex()
        if hasattr(topic, "hex"):
            value = topic.hex()
            return value if value.startswith("0x") else "0x" + value
        value = str(topic)
        return value if value.startswith("0x") else "0x" + value

    def _topic_address(self, topic: Any) -> str:
        topic_hex = self._topic_hex(topic)
        return self.to_checksum("0x" + topic_hex[-40:])

    def _hex_to_int(self, value: Any) -> int:
        if isinstance(value, (bytes, bytearray)):
            return int.from_bytes(value, byteorder="big")
        if hasattr(value, "hex") and not isinstance(value, str):
            value = value.hex()
        text = str(value)
        if text.startswith("0x"):
            return int(text, 16)
        return int(text, 16)

    def require_write_mode(self) -> None:
        if self.read_only:
            raise LiveModeError("Read-only router client cannot send transactions.")
