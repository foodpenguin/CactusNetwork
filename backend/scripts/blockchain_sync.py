from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Crypto.Hash import keccak


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "databases"
ENV_FILE = PROJECT_DIR / ".env"
ONCHAIN_STATE_DB = DATA_DIR / "onchain_state.db"
EXTERNAL_CONTRACTS_DB = DATA_DIR / "external_contracts.db"

DEFAULT_CHAIN_NAME = "sp-sepolia-testnet"
DEFAULT_RPC_URL = ""
DEFAULT_UNISWAP_API_BASE_URL = "https://trade-api.gateway.uniswap.org/v1"
DEFAULT_UNISWAP_CHAIN_ID = 11155111
DEFAULT_UNISWAP_PROTOCOLS = ["V3"]
DEFAULT_UNISWAP_V3_FEE = 100
DEFAULT_SQRT_PRICE_LIMIT_X96 = 0
V3_EXACT_INPUT_SINGLE_SELECTOR = "0x04e45aaf"
SUPPORTED_UNISWAP_CHAIN_IDS = {
    1,
    10,
    56,
    130,
    137,
    143,
    196,
    324,
    480,
    1301,
    1868,
    4217,
    8453,
    42161,
    42220,
    43114,
    59144,
    81457,
    84532,
    7777777,
    11155111,
}
SHELL_METACHARS = set(";|&$`()><\\'\"\n\r")
BALANCE_OF_SELECTOR = "0x70a08231"
VAULT_BALANCES_SELECTOR = "0xc23f001f"
ROUTER_FILLED_AMOUNT_IN_SELECTOR = "0xec980a9c"
INTENT_TYPE = "UserIntent(address user,address tokenIn,address tokenOut,uint256 amountIn,uint256 minAmountOut,uint256 deadline,bytes32 salt,bool allowPartialFill)"
INTENT_TYPEHASH = "0x" + keccak.new(digest_bits=256, data=INTENT_TYPE.encode("utf-8")).hexdigest()


def load_env_file(path: Path = ENV_FILE) -> None:
    """
    讀取專案 `.env`，讓 Uniswap API key 與相關設定可以從環境變數設定。

    輸入：
    - `path`：`.env` 檔案路徑，預設為專案根目錄 `.env`。

    輸出：
    - 無回傳值。

    副作用：
    - 將 `.env` 中尚未存在於 `os.environ` 的 key/value 載入目前 process。
    - 不覆蓋已存在的環境變數。
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _now_iso() -> str:
    """
    取得 UTC ISO 時間字串。

    輸入：
    - 無。

    輸出：
    - 回傳 UTC ISO 格式時間字串。
    """
    return datetime.now(timezone.utc).isoformat()


def _init_database() -> None:
    """
    初始化鏈上同步專用資料庫。

    輸入：
    - 無；固定使用 `ONCHAIN_STATE_DB`。

    輸出：
    - 無回傳值。

    副作用：
    - 建立 `data/databases/onchain_state.db`。
    - 建立最新狀態表 `onchain_states`。
    - 建立差異歷史表 `onchain_state_changes`。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ONCHAIN_STATE_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS onchain_states (
                target_id TEXT PRIMARY KEY,
                chain_name TEXT NOT NULL,
                latest_state_json TEXT NOT NULL,
                latest_state_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_checked_at TEXT NOT NULL,
                last_changed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS onchain_state_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id TEXT NOT NULL,
                chain_name TEXT NOT NULL,
                old_state_json TEXT,
                new_state_json TEXT NOT NULL,
                old_state_hash TEXT,
                new_state_hash TEXT NOT NULL,
                changed_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _init_external_contracts_database() -> None:
    """
    初始化外部合約資料庫。

    輸入：
    - 無；固定使用 `EXTERNAL_CONTRACTS_DB`。

    輸出：
    - 無回傳值。

    副作用：
    - 建立 `data/databases/external_contracts.db`。
    - 建立 agents 查詢外部合約過程用的 query、snapshot、candidate 三張表。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(EXTERNAL_CONTRACTS_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS external_contract_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL UNIQUE,
                source_task_id TEXT,
                source_order_type TEXT NOT NULL,
                source_order_id INTEGER,
                request_reason TEXT,
                query_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS external_contract_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                onchain_state_json TEXT NOT NULL,
                onchain_update_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS external_contract_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                candidate_json TEXT NOT NULL,
                is_valid INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _get_rpc_url() -> str:
    """
    取得 SP/Sepolia 測試鏈 RPC URL。

    輸入：
    - 無；從環境變數讀取。

    輸出：
    - RPC URL 字串。

    讀取順序：
    - `SP_TESTNET_RPC_URL`
    - `SEPOLIA_RPC_URL`
    - `RPC_URL`
    - 文件中的 Sepolia Infura 預設值
    """
    load_env_file()
    return (
        os.getenv("SP_TESTNET_RPC_URL")
        or os.getenv("SEPOLIA_RPC_URL")
        or os.getenv("RPC_URL")
        or DEFAULT_RPC_URL
    )


def _get_uniswap_api_base_url() -> str:
    """
    取得 Uniswap API base URL。

    輸入：
    - 無；從 `.env` 或環境變數讀取。

    輸出：
    - 回傳 Uniswap API base URL。
    """
    load_env_file()
    return os.getenv("UNISWAP_API_BASE_URL") or DEFAULT_UNISWAP_API_BASE_URL


def _get_uniswap_api_key() -> str:
    """
    取得 Uniswap API key。

    輸入：
    - 無；從 `.env` 或環境變數 `UNISWAP_API_KEY` 讀取。

    輸出：
    - 回傳 API key 字串。

    錯誤：
    - 未設定 `UNISWAP_API_KEY` 時拋出 `RuntimeError`。
    """
    load_env_file()
    api_key = os.getenv("UNISWAP_API_KEY")
    if not api_key:
        raise RuntimeError("UNISWAP_API_KEY 尚未設定")
    return api_key


def _get_default_uniswap_chain_id() -> int:
    """
    取得預設 Uniswap chain id。

    輸入：
    - 無；可用 `UNISWAP_CHAIN_ID` 覆蓋，預設 Sepolia `11155111`。

    輸出：
    - 回傳整數 chain id。
    """
    load_env_file()
    return int(os.getenv("UNISWAP_CHAIN_ID") or DEFAULT_UNISWAP_CHAIN_ID)


def _clean_hex(value: str) -> str:
    """
    移除 hex 字串前綴並轉小寫。

    輸入：
    - `value`：可能含 `0x` 前綴的 hex 字串。

    輸出：
    - 不含 `0x` 的小寫 hex 字串。
    """
    return value[2:].lower() if value.startswith("0x") else value.lower()


def _is_address(value: Optional[str]) -> bool:
    """
    檢查字串是否像 EVM address。

    輸入：
    - `value`：待檢查字串或 `None`。

    輸出：
    - 是 20 bytes hex address 時回傳 `True`，否則回傳 `False`。
    """
    if not value:
        return False
    clean = _clean_hex(value)
    return len(clean) == 40 and all(char in "0123456789abcdef" for char in clean)


def _is_bytes32(value: Optional[str]) -> bool:
    """
    檢查字串是否像 bytes32 hex。

    輸入：
    - `value`：待檢查字串或 `None`。

    輸出：
    - 是 32 bytes hex 時回傳 `True`，否則回傳 `False`。
    """
    if not value:
        return False
    clean = _clean_hex(value)
    return len(clean) == 64 and all(char in "0123456789abcdef" for char in clean)


def _contains_shell_metachar(value: str) -> bool:
    """
    檢查字串是否包含 shell metacharacters。

    輸入：
    - `value`：待檢查字串。

    輸出：
    - 包含危險字元時回傳 `True`。
    """
    return any(char in SHELL_METACHARS for char in value)


def _is_non_negative_numeric(value: Any) -> bool:
    """
    檢查 Uniswap amount 是否為非負數字字串。

    輸入：
    - `value`：amount 值。

    輸出：
    - 符合 `^[0-9]+\\.?[0-9]*$` 時回傳 `True`。
    """
    return bool(re.fullmatch(r"[0-9]+\.?[0-9]*", str(value)))


def _validate_uniswap_target(target: dict[str, Any], amount: Any, chain_id: int, token_out_chain_id: int) -> list[str]:
    """
    依 Uniswap swap-integration skill 檢查 API 輸入。

    輸入：
    - `target`：Uniswap quote target。
    - `amount`：本次 quote amount。
    - `chain_id`：tokenIn chain id。
    - `token_out_chain_id`：tokenOut chain id。

    輸出：
    - 回傳錯誤訊息 list；空 list 代表可送 API。
    """
    errors: list[str] = []
    for key in ("tokenIn", "tokenOut", "swapper"):
        value = target.get(key) or (target.get("user") if key == "swapper" else None)
        if value not in (None, "") and not _is_address(str(value)):
            errors.append(f"{key} 不是有效 Ethereum address")
    for key in ("tokenIn", "tokenOut", "swapper", "user", "type", "tradeType", "routingPreference", "urgency"):
        value = target.get(key)
        if isinstance(value, str) and _contains_shell_metachar(value):
            errors.append(f"{key} 含有不允許的 shell metacharacter")
    if amount not in (None, "") and not _is_non_negative_numeric(amount):
        errors.append("amount 必須是非負數字字串")
    if chain_id not in SUPPORTED_UNISWAP_CHAIN_IDS:
        errors.append(f"tokenInChainId 不在 Uniswap supported chains: {chain_id}")
    if token_out_chain_id not in SUPPORTED_UNISWAP_CHAIN_IDS:
        errors.append(f"tokenOutChainId 不在 Uniswap supported chains: {token_out_chain_id}")
    return errors


def _encode_address(value: str) -> str:
    """
    ABI encode address 參數。

    輸入：
    - `value`：EVM address。

    輸出：
    - 32 bytes ABI slot hex，不含 `0x`。
    """
    if not _is_address(value):
        raise ValueError(f"不是有效 address: {value}")
    return _clean_hex(value).rjust(64, "0")


def _encode_bytes32(value: str) -> str:
    """
    ABI encode bytes32 參數。

    輸入：
    - `value`：bytes32 hex。

    輸出：
    - 32 bytes ABI slot hex，不含 `0x`。
    """
    if not _is_bytes32(value):
        raise ValueError(f"不是有效 bytes32: {value}")
    return _clean_hex(value)


def _encode_uint256(value: Any) -> str:
    """
    ABI encode uint256 參數。

    輸入：
    - `value`：非負整數或數字字串。

    輸出：
    - 32 bytes ABI slot hex，不含 `0x`。
    """
    number = int(str(value))
    if number < 0:
        raise ValueError(f"uint256 不可為負數: {value}")
    return hex(number)[2:].rjust(64, "0")


def _encode_uint24(value: Any) -> str:
    """
    ABI encode uint24 參數。

    輸入：
    - `value`：0 到 16777215 的整數。

    輸出：
    - 32 bytes ABI slot hex，不含 `0x`。
    """
    number = int(str(value))
    if number < 0 or number > 16_777_215:
        raise ValueError(f"uint24 超出範圍: {value}")
    return hex(number)[2:].rjust(64, "0")


def _encode_uint160(value: Any) -> str:
    """
    ABI encode uint160 參數。

    輸入：
    - `value`：0 到 2^160 - 1 的整數。

    輸出：
    - 32 bytes ABI slot hex，不含 `0x`。
    """
    number = int(str(value))
    if number < 0 or number >= 2**160:
        raise ValueError(f"uint160 超出範圍: {value}")
    return hex(number)[2:].rjust(64, "0")


def _call_data(selector: str, encoded_args: list[str]) -> str:
    """
    組合 eth_call data。

    輸入：
    - `selector`：4 bytes function selector，例如 `0x70a08231`。
    - `encoded_args`：已 ABI encode 的 32 bytes 參數列表。

    輸出：
    - 完整 calldata hex 字串。
    """
    clean_selector = _clean_hex(selector)
    if len(clean_selector) != 8:
        raise ValueError(f"function selector 必須是 4 bytes: {selector}")
    return "0x" + clean_selector + "".join(encoded_args)


def _json_rpc(method: str, params: list[Any], rpc_url: Optional[str] = None) -> Any:
    """
    發送 JSON-RPC 請求。

    輸入：
    - `method`：JSON-RPC method，例如 `eth_call`。
    - `params`：JSON-RPC params。
    - `rpc_url`：可選 RPC URL，未提供時讀環境變數。

    輸出：
    - 回傳 RPC response 的 `result`。

    錯誤：
    - HTTP error 或 RPC error 會拋出 `RuntimeError`。
    """
    url = rpc_url or _get_rpc_url()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"RPC HTTP 錯誤 {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"RPC 連線錯誤: {exc}") from exc

    if "error" in data:
        raise RuntimeError(f"RPC 回傳錯誤: {json.dumps(data['error'], ensure_ascii=False)}")
    return data.get("result")


def _uniswap_api_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    呼叫 Uniswap Trading API。

    輸入：
    - `path`：API path，例如 `/quote` 或 `/swap`。
    - `payload`：要送出的 JSON body。

    輸出：
    - 回傳 Uniswap API JSON response。

    錯誤：
    - HTTP error、連線錯誤或 JSON 格式錯誤時拋出 `RuntimeError`。
    """
    base_url = _get_uniswap_api_base_url().rstrip("/")
    api_key = _get_uniswap_api_key()
    request = urllib.request.Request(
        f"{base_url}/{path.lstrip('/')}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "CactusNetwork/0.1 (+https://hackmd.io/@uycOP7THSE2LUgKW2WnkZg)",
            "x-api-key": api_key,
            "x-universal-router-version": "2.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Uniswap API HTTP 錯誤 {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Uniswap API 連線錯誤: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Uniswap API 回傳不是 JSON: {exc}") from exc


def _eth_call(contract_address: str, data: str, rpc_url: Optional[str] = None) -> str:
    """
    呼叫 EVM 合約 view function。

    輸入：
    - `contract_address`：目標合約地址。
    - `data`：完整 calldata。
    - `rpc_url`：可選 RPC URL。

    輸出：
    - 回傳 hex encoded result。
    """
    if not _is_address(contract_address):
        raise ValueError(f"不是有效合約地址: {contract_address}")
    return _json_rpc("eth_call", [{"to": contract_address, "data": data}, "latest"], rpc_url)


def simulate_router_call(
    calldata: str,
    router_address: str,
    caller: Optional[str] = None,
    rpc_url: Optional[str] = None,
) -> dict[str, Any]:
    """
    使用 `eth_call` 模擬 router.call(calldata) 是否會成功。

    輸入：
    - `calldata`：主腦輸出的 `routeDetails.Calldata`。
    - `router_address`：要呼叫的 router 合約地址，例如 Sepolia Uniswap V3 SwapRouter。
    - `caller`：可選 `from` address；會影響 router 內的 `msg.sender`。
    - `rpc_url`：可選 RPC URL，未提供時讀 `.env`。

    輸出：
    - 成功時回傳：
      - `success = True`
      - `result`：RPC 回傳 hex。
    - revert 或 RPC error 時回傳：
      - `success = False`
      - `error`：錯誤訊息。
      - `revertReason`：若可解析，回傳 Solidity revert reason。

    副作用：
    - 只做鏈上模擬讀取，不送交易、不改鏈上狀態。
    """
    load_env_file()
    if not _is_address(router_address):
        raise ValueError(f"不是有效 router address: {router_address}")
    if caller is not None and not _is_address(caller):
        raise ValueError(f"不是有效 caller address: {caller}")
    if not isinstance(calldata, str) or not calldata.startswith("0x"):
        raise ValueError("calldata 必須是 0x 開頭的 hex 字串")

    call_object: dict[str, Any] = {
        "to": router_address,
        "data": calldata,
    }
    if caller:
        call_object["from"] = caller

    try:
        result = _json_rpc("eth_call", [call_object, "latest"], rpc_url)
        return {
            "success": True,
            "router": router_address,
            "caller": caller,
            "result": result,
        }
    except RuntimeError as exc:
        error_text = str(exc)
        return {
            "success": False,
            "router": router_address,
            "caller": caller,
            "error": error_text,
            "revertReason": _decode_revert_reason(error_text),
        }


def read_erc20_balance_and_allowance(
    token_address: str,
    owner: str,
    spender: str,
    rpc_url: Optional[str] = None,
) -> dict[str, Any]:
    """
    讀取 ERC20 balance 與 allowance，輔助判斷 router `STF` 失敗原因。

    輸入：
    - `token_address`：ERC20 token 合約地址。
    - `owner`：代幣持有人地址。
    - `spender`：被授權花費代幣的地址，通常是 router。
    - `rpc_url`：可選 RPC URL，未提供時讀 `.env`。

    輸出：
    - 回傳：
      - `balanceRaw`：`balanceOf(owner)` 的 uint256 十進位字串。
      - `allowanceRaw`：`allowance(owner, spender)` 的 uint256 十進位字串。

    副作用：
    - 只做 `eth_call` 讀取，不送交易。
    """
    load_env_file()
    for label, address in (("token_address", token_address), ("owner", owner), ("spender", spender)):
        if not _is_address(address):
            raise ValueError(f"{label} 不是有效 Ethereum address")

    balance_data = _call_data("0x70a08231", [_encode_address(owner)])
    allowance_data = _call_data("0xdd62ed3e", [_encode_address(owner), _encode_address(spender)])
    balance = _decode_uint256(_eth_call(token_address, balance_data, rpc_url))
    allowance = _decode_uint256(_eth_call(token_address, allowance_data, rpc_url))
    return {
        "tokenAddress": token_address,
        "owner": owner,
        "spender": spender,
        "balanceRaw": balance,
        "allowanceRaw": allowance,
    }


def hash_intent(intent: dict[str, Any]) -> str:
    """
    依 `SettlementRouter.hashIntent()` 的 Solidity ABI 規則計算 UserIntent struct hash。

    輸入：
    - `intent`：包含 `user`、`tokenIn`、`tokenOut`、`amountIn`、`minAmountOut`、`deadline`、`salt`、`allowPartialFill` 的 dict。

    輸出：
    - 回傳 `0x` 開頭的 bytes32 intent hash，可直接查 `SettlementRouter.filledAmountIn(intentHash)`。

    副作用：
    - 無；只做本地 keccak256 計算。
    """
    _validate_user_intent(intent)
    encoded_hex = "".join(
        [
            _encode_bytes32(INTENT_TYPEHASH),
            _encode_address(str(intent["user"])),
            _encode_address(str(intent["tokenIn"])),
            _encode_address(str(intent["tokenOut"])),
            _encode_uint256(intent["amountIn"]),
            _encode_uint256(intent["minAmountOut"]),
            _encode_uint256(intent["deadline"]),
            _encode_bytes32(str(intent["salt"])),
            _encode_bool(bool(intent["allowPartialFill"])),
        ]
    )
    digest = keccak.new(digest_bits=256, data=bytes.fromhex(encoded_hex)).hexdigest()
    return f"0x{digest}"


def read_intent_execution_capacity(
    intent: dict[str, Any],
    execute_amount_in: Any,
    rpc_url: Optional[str] = None,
    vault_address: Optional[str] = None,
    router_address: Optional[str] = None,
) -> dict[str, Any]:
    """
    讀取單筆 intent 在鏈上是否還有足夠 vault 餘額與未成交額度。

    輸入：
    - `intent`：UserIntent dict。
    - `execute_amount_in`：本次要消耗的 tokenIn raw amount。
    - `rpc_url`：可選 RPC URL；未提供時讀 `.env`。
    - `vault_address`：可選 IntentVault 地址；未提供時讀 `INTENT_VAULT_ADDRESS`。
    - `router_address`：可選 SettlementRouter 地址；未提供時讀 `SETTLEMENT_ROUTER_ADDRESS`。

    輸出：
    - 回傳：
      - `vaultBalance`：`IntentVault.balances(user, tokenIn)`。
      - `filledAmountIn`：`SettlementRouter.filledAmountIn(intentHash)`。
      - `remainingAmountIn`：`amountIn - filledAmountIn`。
      - `hasEnoughVaultBalance`：vault 餘額是否足夠本次執行。
      - `hasEnoughRemainingAmount`：intent 未成交額度是否足夠本次執行。
      - `isExecutable`：兩者都足夠才為 true。

    副作用：
    - 發送兩次 JSON-RPC `eth_call`。
    - 不送交易，不改鏈上狀態。
    """
    load_env_file()
    _validate_user_intent(intent)
    execute_amount = int(str(execute_amount_in))
    if execute_amount <= 0:
        raise ValueError("execute_amount_in 必須大於 0")

    resolved_vault = vault_address or os.getenv("INTENT_VAULT_ADDRESS")
    resolved_router = router_address or os.getenv("SETTLEMENT_ROUTER_ADDRESS")
    if not _is_address(resolved_vault):
        raise ValueError("缺少或無效 INTENT_VAULT_ADDRESS")
    if not _is_address(resolved_router):
        raise ValueError("缺少或無效 SETTLEMENT_ROUTER_ADDRESS")

    intent_hash = hash_intent(intent)
    vault_data = _call_data(
        os.getenv("VAULT_BALANCES_SELECTOR", VAULT_BALANCES_SELECTOR),
        [_encode_address(str(intent["user"])), _encode_address(str(intent["tokenIn"]))],
    )
    filled_data = _call_data(
        os.getenv("ROUTER_FILLED_AMOUNT_IN_SELECTOR", ROUTER_FILLED_AMOUNT_IN_SELECTOR),
        [_encode_bytes32(intent_hash)],
    )
    vault_balance = int(_decode_uint256(_eth_call(resolved_vault, vault_data, rpc_url)) or "0")
    filled_amount = int(_decode_uint256(_eth_call(resolved_router, filled_data, rpc_url)) or "0")
    amount_in = int(str(intent["amountIn"]))
    remaining_amount = max(amount_in - filled_amount, 0)

    has_vault = vault_balance >= execute_amount
    has_remaining = remaining_amount >= execute_amount
    return {
        "intentHash": intent_hash,
        "user": intent["user"],
        "tokenIn": intent["tokenIn"],
        "executeAmountIn": str(execute_amount),
        "amountIn": str(amount_in),
        "vaultBalance": str(vault_balance),
        "filledAmountIn": str(filled_amount),
        "remainingAmountIn": str(remaining_amount),
        "hasEnoughVaultBalance": has_vault,
        "hasEnoughRemainingAmount": has_remaining,
        "isExecutable": has_vault and has_remaining,
    }


def _validate_user_intent(intent: dict[str, Any]) -> None:
    """
    檢查 UserIntent 是否具備鏈上讀取與 hash 所需欄位。

    輸入：
    - `intent`：UserIntent dict。

    輸出：
    - 驗證成功無回傳；失敗丟出 `ValueError`。
    """
    missing = [field for field in ("user", "tokenIn", "tokenOut", "amountIn", "minAmountOut", "deadline", "salt", "allowPartialFill") if intent.get(field) in (None, "")]
    if missing:
        raise ValueError("UserIntent 缺少欄位: " + ", ".join(missing))
    for field in ("user", "tokenIn", "tokenOut"):
        if not _is_address(str(intent[field])):
            raise ValueError(f"UserIntent.{field} 不是有效 address")
    for field in ("amountIn", "minAmountOut", "deadline"):
        if int(str(intent[field])) < 0:
            raise ValueError(f"UserIntent.{field} 不可為負數")
    if not _is_bytes32(str(intent["salt"])):
        raise ValueError("UserIntent.salt 不是有效 bytes32")
    if not isinstance(intent["allowPartialFill"], bool):
        raise ValueError("UserIntent.allowPartialFill 必須是 bool")


def _encode_bool(value: bool) -> str:
    """
    ABI encode bool 參數。

    輸入：
    - `value`：布林值。

    輸出：
    - 32 bytes ABI slot hex，不含 `0x`。
    """
    return ("1" if value else "0").rjust(64, "0")


def _decode_revert_reason(error_text: str) -> Optional[str]:
    """
    從 RPC error 文字中解析 Solidity `Error(string)` revert reason。

    輸入：
    - `error_text`：RPC error 字串。

    輸出：
    - 可解析時回傳 revert reason，例如 `STF`。
    - 不可解析時回傳 `None`。
    """
    match = re.search(r"0x08c379a0[0-9a-fA-F]+", error_text)
    if not match:
        return None
    data = match.group(0)[10:]
    if len(data) < 128:
        return None
    try:
        length = int(data[64:128], 16)
        encoded_text = data[128 : 128 + length * 2]
        return bytes.fromhex(encoded_text).decode("utf-8", errors="replace")
    except ValueError:
        return None


def _decode_uint256(value: Optional[str]) -> Optional[str]:
    """
    將 eth_call 回傳值解成 uint256 十進位字串。

    輸入：
    - `value`：RPC 回傳 hex 字串。

    輸出：
    - 成功時回傳十進位字串。
    - 空值或 `0x` 回傳 `None`。
    """
    if not value or value == "0x":
        return None
    return str(int(value, 16))


def _env_or_target(target: dict[str, Any], target_key: str, env_key: str) -> Optional[str]:
    """
    從單筆同步目標或環境變數取得設定值。

    輸入：
    - `target`：同步目標 dict。
    - `target_key`：target 內的 key。
    - `env_key`：環境變數名稱。

    輸出：
    - target 值優先，其次環境變數；都沒有則回傳 `None`。
    """
    return target.get(target_key) or os.getenv(env_key)


def _read_uniswap_target(target: dict[str, Any], rpc_url: Optional[str] = None) -> dict[str, Any]:
    """
    使用 Uniswap API 讀取外部可撮合資訊並由後端建立 V3 calldata。

    輸入：
    - `target`：Uniswap quote target，可包含：
      - `tokenIn`
      - `tokenOut`
      - `amount` 或 `amountIn`
      - `swapper`
      - `tokenInChainId` / `tokenOutChainId` / `chainId`
      - `slippageTolerance` 或 `autoSlippage`
      - `protocols`，目前會被強制收斂為 `["V3"]`
      - `routingPreference`
      - `fee`，目前強制使用 V3 pool fee，預設 100
      - `priceLimit` 或 `sqrtPriceLimitX96`，預設 0
    - `rpc_url`：保留相容舊呼叫；Uniswap API 模式不使用。

    輸出：
    - 回傳外部市場狀態 dict，包含 quote、route、Calldata、skipped 與 errors。

    副作用：
    - 發送 Uniswap API `/check_approval` 與 `/quote`。
    - 不呼叫 `/swap`，不需要 permit signature。
    - quote 成功後，後端自行建立 Uniswap V3 `exactInputSingle` calldata。
    """
    load_env_file()
    chain_id = int(target.get("tokenInChainId") or target.get("chainId") or _get_default_uniswap_chain_id())
    token_out_chain_id = int(target.get("tokenOutChainId") or target.get("chainId") or chain_id)
    amount = target.get("amount") or target.get("amountIn") or target.get("executeAmountIn")

    state: dict[str, Any] = {
        "targetId": get_target_id(target),
        "chainName": os.getenv("SP_TESTNET_CHAIN_NAME", DEFAULT_CHAIN_NAME),
        "checkedAt": _now_iso(),
        "source": "uniswap_api",
        "intent": {
            "user": target.get("swapper") or target.get("user"),
            "tokenIn": target.get("tokenIn"),
            "tokenOut": target.get("tokenOut"),
            "amountIn": str(amount) if amount is not None else None,
        },
        "reads": {},
        "uniswap": {},
        "skipped": [],
        "errors": [],
    }

    missing_required = [
        name
        for name, value in {
            "tokenIn": target.get("tokenIn"),
            "tokenOut": target.get("tokenOut"),
            "amount": amount,
            "swapper": target.get("swapper") or target.get("user"),
        }.items()
        if value in (None, "")
    ]
    if missing_required:
        state["errors"].append("Uniswap quote 缺必要欄位: " + ", ".join(missing_required))
        state["isValid"] = False
        return state

    validation_errors = _validate_uniswap_target(target, amount, chain_id, token_out_chain_id)
    if validation_errors:
        state["errors"].extend(validation_errors)
        state["isValid"] = False
        return state

    if target.get("buildApprovalCheck", True):
        _attach_uniswap_approval(state, target, amount, chain_id)
    else:
        state["skipped"].append("approval: buildApprovalCheck=false")

    quote_request = {
        "type": target.get("type") or target.get("tradeType") or "EXACT_INPUT",
        "tokenInChainId": str(chain_id),
        "tokenOutChainId": str(token_out_chain_id),
        "tokenIn": target["tokenIn"],
        "tokenOut": target["tokenOut"],
        "amount": str(amount),
        "swapper": target.get("swapper") or target.get("user"),
        "generatePermitAsTransaction": bool(target.get("generatePermitAsTransaction", False)),
        "permitAmount": target.get("permitAmount", "FULL"),
        "urgency": target.get("urgency", "normal"),
    }
    quote_request["protocols"] = DEFAULT_UNISWAP_PROTOCOLS.copy()
    if target.get("routingPreference") is not None:
        quote_request["routingPreference"] = target["routingPreference"]
    else:
        quote_request["routingPreference"] = "BEST_PRICE"
    if target.get("slippageTolerance") is not None:
        quote_request["slippageTolerance"] = target["slippageTolerance"]
    else:
        quote_request["autoSlippage"] = target.get("autoSlippage", "DEFAULT")

    try:
        quote_response = _uniswap_api_post("/quote", quote_request)
        quote = quote_response.get("quote") if isinstance(quote_response.get("quote"), dict) else {}
        state["uniswap"]["quoteRequest"] = quote_request
        state["uniswap"]["quoteResponse"] = quote_response
        state["reads"].update(_extract_uniswap_quote_reads(quote_response))
        if not quote:
            state["errors"].append("Uniswap quote response 缺 quote")
            state["isValid"] = False
            return state
    except Exception as exc:
        state["errors"].append(f"Uniswap quote: {exc}")
        state["isValid"] = False
        return state

    if target.get("buildCalldata", True):
        _attach_v3_exact_input_single_calldata(state, target)
    else:
        state["skipped"].append("Calldata: buildCalldata=false")

    output_amount = state["reads"].get("amountOut") or state["reads"].get("outputAmount")
    state["isValid"] = not state["errors"] and output_amount not in (None, "", "0")
    return state


def _extract_uniswap_quote_reads(quote_response: dict[str, Any]) -> dict[str, Any]:
    """
    從 Uniswap quote response 整理 agents 需要看的欄位。

    輸入：
    - `quote_response`：`/quote` 回傳 JSON。

    輸出：
    - 回傳扁平化 reads dict。
    """
    quote = quote_response.get("quote") if isinstance(quote_response.get("quote"), dict) else {}
    input_data = quote.get("input") if isinstance(quote.get("input"), dict) else {}
    output_data = quote.get("output") if isinstance(quote.get("output"), dict) else {}
    return {
        "routing": quote_response.get("routing") or quote.get("routing"),
        "quoteId": quote.get("quoteId") or quote_response.get("quoteId"),
        "amountIn": input_data.get("amount") or quote.get("amountIn"),
        "amountOut": output_data.get("amount") or quote.get("amountOut"),
        "gasFee": quote.get("gasFee") or quote_response.get("gasFee"),
        "gasFeeUSD": quote.get("gasFeeUSD") or quote_response.get("gasFeeUSD"),
        "routeString": quote.get("routeString"),
        "priceImpact": quote.get("priceImpact"),
    }


def _attach_uniswap_approval(state: dict[str, Any], target: dict[str, Any], amount: Any, chain_id: int) -> None:
    """
    依 Uniswap skill 流程先檢查 token approval。

    輸入：
    - `state`：目前 Uniswap target state。
    - `target`：原始 target。
    - `amount`：quote amount。
    - `chain_id`：tokenIn chain id。

    輸出：
    - 無回傳；直接把 approval response 寫入 `state`。
    """
    approval_request = {
        "walletAddress": target.get("swapper") or target.get("user"),
        "token": target["tokenIn"],
        "amount": str(amount),
        "chainId": chain_id,
    }
    try:
        approval_response = _uniswap_api_post("/check_approval", approval_request)
        state["uniswap"]["approvalRequest"] = approval_request
        state["uniswap"]["approvalResponse"] = approval_response
        approval = approval_response.get("approval") if isinstance(approval_response.get("approval"), dict) else approval_response
        state["reads"]["approvalRequired"] = bool(approval_response.get("approvalRequired", approval.get("approvalRequired") if isinstance(approval, dict) else False))
        state["reads"]["approvalTransaction"] = approval_response.get("approvalTransaction") or (
            approval.get("approvalTransaction") if isinstance(approval, dict) else None
        )
    except Exception as exc:
        state["errors"].append(f"Uniswap check_approval: {exc}")


def _build_v3_exact_input_single_calldata(
    *,
    token_in: str,
    token_out: str,
    fee: Any,
    recipient: str,
    amount_in: Any,
    amount_out_minimum: Any,
    sqrt_price_limit_x96: Any,
) -> str:
    """
    建立 Uniswap V3 SwapRouter `exactInputSingle` calldata。

    輸入：
    - `token_in`：輸入 token address。
    - `token_out`：輸出 token address。
    - `fee`：V3 pool fee，例如 100。
    - `recipient`：收款 address，通常是合約或 keeper 指定收款者。
    - `amount_in`：實際輸入數量。
    - `amount_out_minimum`：最小輸出數量。
    - `sqrt_price_limit_x96`：價格限制，沒有則用 0。

    輸出：
    - 回傳完整 calldata hex。
    """
    return _call_data(
        V3_EXACT_INPUT_SINGLE_SELECTOR,
        [
            _encode_address(token_in),
            _encode_address(token_out),
            _encode_uint24(fee),
            _encode_address(recipient),
            _encode_uint256(amount_in),
            _encode_uint256(amount_out_minimum),
            _encode_uint160(sqrt_price_limit_x96),
        ],
    )


def _attach_v3_exact_input_single_calldata(state: dict[str, Any], target: dict[str, Any]) -> None:
    """
    quote 成功後自行建立 V3 `exactInputSingle` calldata。

    輸入：
    - `state`：目前 Uniswap target state，需已有 `reads.amountIn` / `reads.amountOut`。
    - `target`：原始 target，可包含 `fee`、`priceLimit`、`sqrtPriceLimitX96`、`recipient`。

    輸出：
    - 無回傳；直接把 `Calldata` 與參數寫入 `state.reads`。
    """
    try:
        fee = target.get("fee", DEFAULT_UNISWAP_V3_FEE)
        sqrt_price_limit = target.get("sqrtPriceLimitX96", target.get("priceLimit", DEFAULT_SQRT_PRICE_LIMIT_X96))
        amount_in = state["reads"].get("amountIn") or target.get("amount") or target.get("amountIn")
        amount_out_minimum = target.get("amountOutMinimum") or target.get("minAmountOut") or state["reads"].get("amountOut")
        recipient = target.get("recipient") or target.get("swapper") or target.get("user")
        if amount_in in (None, ""):
            state["errors"].append("V3 calldata: 缺 amountIn")
            return
        if amount_out_minimum in (None, ""):
            state["errors"].append("V3 calldata: 缺 amountOutMinimum")
            return
        if not recipient:
            state["errors"].append("V3 calldata: 缺 recipient")
            return
        calldata = _build_v3_exact_input_single_calldata(
            token_in=target["tokenIn"],
            token_out=target["tokenOut"],
            fee=fee,
            recipient=recipient,
            amount_in=amount_in,
            amount_out_minimum=amount_out_minimum,
            sqrt_price_limit_x96=sqrt_price_limit,
        )
        state["reads"]["Calldata"] = calldata
        state["reads"]["v3Fee"] = str(fee)
        state["reads"]["sqrtPriceLimitX96"] = str(sqrt_price_limit)
        state["reads"]["amountOutMinimum"] = str(amount_out_minimum)
        state["reads"]["recipient"] = recipient
    except Exception as exc:
        state["errors"].append(f"V3 calldata: {exc}")


def _read_onchain_target(target: dict[str, Any], rpc_url: Optional[str] = None) -> dict[str, Any]:
    """
    舊版 RPC 讀取函式，保留作為低階工具，不再由主要流程呼叫。

    輸入：
    - `target`：同步目標 dict，可包含：
      - `intentId`
      - `intentHash`
      - `user`
      - `tokenIn`
      - `tokenOut`
      - `amountIn`
      - `vaultAddress`
      - `routerAddress`
      - `treasuryAddress`
      - `treasuryToken`
    - `rpc_url`：可選 RPC URL。

    輸出：
    - 回傳鏈上狀態 dict，包含成功讀到的值、skipped 項目與 errors。

    副作用：
    - 發送 JSON-RPC `eth_call`。
    """
    load_env_file()
    vault_address = _env_or_target(target, "vaultAddress", "INTENT_VAULT_ADDRESS")
    router_address = _env_or_target(target, "routerAddress", "SETTLEMENT_ROUTER_ADDRESS")
    treasury_address = _env_or_target(target, "treasuryAddress", "PROTOCOL_TREASURY_ADDRESS")
    treasury_token = _env_or_target(target, "treasuryToken", "TREASURY_TOKEN_ADDRESS") or target.get("tokenOut")
    vault_selector = os.getenv("VAULT_BALANCES_SELECTOR", VAULT_BALANCES_SELECTOR)
    router_selector = os.getenv("ROUTER_FILLED_AMOUNT_IN_SELECTOR", ROUTER_FILLED_AMOUNT_IN_SELECTOR)

    state: dict[str, Any] = {
        "targetId": get_target_id(target),
        "chainName": os.getenv("SP_TESTNET_CHAIN_NAME", DEFAULT_CHAIN_NAME),
        "checkedAt": _now_iso(),
        "source": "json_rpc_eth_call",
        "intent": {
            "intentHash": target.get("intentHash"),
            "user": target.get("user"),
            "tokenIn": target.get("tokenIn"),
            "tokenOut": target.get("tokenOut"),
            "amountIn": str(target.get("amountIn")) if target.get("amountIn") is not None else None,
        },
        "reads": {},
        "skipped": [],
        "errors": [],
    }

    try:
        if vault_address and _is_address(target.get("user")) and _is_address(target.get("tokenIn")):
            data = _call_data(vault_selector, [_encode_address(target["user"]), _encode_address(target["tokenIn"])])
            state["reads"]["vaultBalance"] = _decode_uint256(_eth_call(vault_address, data, rpc_url))
        else:
            state["skipped"].append("vaultBalance: 缺 vaultAddress/user/tokenIn")
    except Exception as exc:
        state["errors"].append(f"vaultBalance: {exc}")

    try:
        if router_address and router_selector and _is_bytes32(target.get("intentHash")):
            data = _call_data(router_selector, [_encode_bytes32(target["intentHash"])])
            state["reads"]["filledAmountIn"] = _decode_uint256(_eth_call(router_address, data, rpc_url))
        else:
            state["skipped"].append("filledAmountIn: 缺 routerAddress/routerSelector/intentHash")
    except Exception as exc:
        state["errors"].append(f"filledAmountIn: {exc}")

    try:
        if treasury_address and _is_address(treasury_token):
            data = _call_data(BALANCE_OF_SELECTOR, [_encode_address(treasury_address)])
            state["reads"]["treasuryBalance"] = _decode_uint256(_eth_call(treasury_token, data, rpc_url))
        else:
            state["skipped"].append("treasuryBalance: 缺 treasuryAddress/treasuryToken")
    except Exception as exc:
        state["errors"].append(f"treasuryBalance: {exc}")

    amount_in = target.get("amountIn")
    filled = state["reads"].get("filledAmountIn")
    if amount_in is not None and filled is not None:
        remaining = max(int(str(amount_in)) - int(str(filled)), 0)
        state["reads"]["remainingAmountIn"] = str(remaining)

    vault_balance = state["reads"].get("vaultBalance")
    remaining_amount = state["reads"].get("remainingAmountIn")
    if vault_balance is not None and remaining_amount is not None:
        state["isValid"] = int(vault_balance) > 0 and int(remaining_amount) > 0
    else:
        state["isValid"] = None

    return state


def get_target_id(target: dict[str, Any]) -> str:
    """
    取得同步目標的穩定 id。

    輸入：
    - `target`：同步目標 dict。

    輸出：
    - 優先回傳 `intentId`，其次 `intentHash`，最後使用 target JSON hash。
    """
    if target.get("intentId"):
        return str(target["intentId"])
    if target.get("intentHash"):
        return str(target["intentHash"])
    raw = json.dumps(target, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _state_hash(state: dict[str, Any]) -> str:
    """
    計算鏈上狀態快照 hash。

    輸入：
    - `state`：鏈上狀態 dict。

    輸出：
    - SHA256 hex digest。
    """
    stable_state = {key: value for key, value in state.items() if key != "checkedAt"}
    raw = json.dumps(stable_state, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _upsert_state(state: dict[str, Any]) -> dict[str, Any]:
    """
    將單筆鏈上狀態差異更新到 DB。

    輸入：
    - `state`：`_read_onchain_target()` 回傳的鏈上狀態 dict。

    輸出：
    - 回傳更新摘要：
      - `targetId`
      - `changed`
      - `latestStateHash`

    副作用：
    - 若第一次看到此 target，寫入 `onchain_states` 並新增一筆 changes。
    - 若 hash 改變，更新 latest state 並新增 changes。
    - 若 hash 未變，只更新 `last_checked_at`。
    """
    _init_database()
    target_id = state["targetId"]
    chain_name = state["chainName"]
    checked_at = state["checkedAt"]
    new_state_json = json.dumps(state, ensure_ascii=False, sort_keys=True)
    new_hash = _state_hash(state)

    with sqlite3.connect(ONCHAIN_STATE_DB) as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            "SELECT latest_state_json, latest_state_hash FROM onchain_states WHERE target_id = ?",
            (target_id,),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO onchain_states (
                    target_id,
                    chain_name,
                    latest_state_json,
                    latest_state_hash,
                    created_at,
                    last_checked_at,
                    last_changed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (target_id, chain_name, new_state_json, new_hash, checked_at, checked_at, checked_at),
            )
            conn.execute(
                """
                INSERT INTO onchain_state_changes (
                    target_id,
                    chain_name,
                    old_state_json,
                    new_state_json,
                    old_state_hash,
                    new_state_hash,
                    changed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (target_id, chain_name, None, new_state_json, None, new_hash, checked_at),
            )
            conn.commit()
            return {"targetId": target_id, "changed": True, "latestStateHash": new_hash}

        if existing["latest_state_hash"] != new_hash:
            conn.execute(
                """
                UPDATE onchain_states
                SET chain_name = ?,
                    latest_state_json = ?,
                    latest_state_hash = ?,
                    last_checked_at = ?,
                    last_changed_at = ?
                WHERE target_id = ?
                """,
                (chain_name, new_state_json, new_hash, checked_at, checked_at, target_id),
            )
            conn.execute(
                """
                INSERT INTO onchain_state_changes (
                    target_id,
                    chain_name,
                    old_state_json,
                    new_state_json,
                    old_state_hash,
                    new_state_hash,
                    changed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    chain_name,
                    existing["latest_state_json"],
                    new_state_json,
                    existing["latest_state_hash"],
                    new_hash,
                    checked_at,
                ),
            )
            conn.commit()
            return {"targetId": target_id, "changed": True, "latestStateHash": new_hash}

        conn.execute(
            "UPDATE onchain_states SET last_checked_at = ? WHERE target_id = ?",
            (checked_at, target_id),
        )
        conn.commit()
        return {"targetId": target_id, "changed": False, "latestStateHash": new_hash}


def update_onchain_state(sync_targets: Optional[list[dict[str, Any]]] = None, rpc_url: Optional[str] = None) -> dict[str, Any]:
    """
    對外提供給未來 agents/runner 呼叫的外部市場狀態更新函式。

    輸入：
    - `sync_targets`：要送給 Uniswap API 查詢的 quote targets list。
    - `rpc_url`：保留相容舊呼叫；Uniswap API 模式不使用。

    輸出：
    - 回傳 dict：
      - `checked`：處理了幾個 target。
      - `changed`：有幾個 target 狀態與 DB 前次快照不同。
      - `unchanged`：有幾個 target 狀態未變。
      - `results`：每個 target 的同步摘要。

    副作用：
    - 呼叫 Uniswap Trading API `/quote`，必要時呼叫 `/swap` 取得 calldata。
    - 寫入 `onchain_state.db`。
    - 只做差異更新；狀態未變時不新增 change history。
    """
    _init_database()
    load_env_file()
    targets = sync_targets or []
    results: list[dict[str, Any]] = []
    changed_count = 0

    for target in targets:
        state = _read_uniswap_target(target, rpc_url)
        update_result = _upsert_state(state)
        changed_count += 1 if update_result["changed"] else 0
        results.append(
            {
                **update_result,
                "isValid": state.get("isValid"),
                "skipped": state.get("skipped", []),
                "errors": state.get("errors", []),
            }
        )

    return {
        "message": "Uniswap API 外部市場資料同步完成",
        "chainName": os.getenv("SP_TESTNET_CHAIN_NAME", DEFAULT_CHAIN_NAME),
        "checked": len(targets),
        "changed": changed_count,
        "unchanged": len(targets) - changed_count,
        "results": results,
    }


def request_external_contract_data(
    order: dict[str, Any],
    query: dict[str, Any],
    rpc_url: Optional[str] = None,
) -> dict[str, Any]:
    """
    記錄 agents 在決策過程中要求查 Uniswap 外部市場資料，並同步快照。

    輸入：
    - `order`：目前 agents 正在判斷的本地訂單資料，通常是 `sellOrder`。
    - `query`：agents 提出的外部查詢需求，可包含：
      - `taskId`
      - `reason`
      - `sourceOrderType`
      - `syncTargets`：要交給 `update_onchain_state()` 的 Uniswap quote targets list。
    - `rpc_url`：可選 RPC URL；未提供時讀 `.env`。

    輸出：
    - 回傳外部查詢摘要，包含 `queryId`、同步數量與整理後候選資料。

    副作用：
    - 寫入 `external_contracts.db`。
    - 呼叫 `update_onchain_state()`，進而使用 Uniswap API 更新 `onchain_state.db`。
    - 不修改本地買單/賣單，不產生成交結果。
    """
    _init_external_contracts_database()
    created_at = _now_iso()
    order_id = _extract_order_id(order)
    source_order_type = str(query.get("sourceOrderType") or order.get("orderType") or "sell")
    query_id = str(query.get("queryId") or f"external:{source_order_type}:{order_id or 'unknown'}:{created_at}")
    targets = _extract_sync_targets(query)

    with sqlite3.connect(EXTERNAL_CONTRACTS_DB) as conn:
        conn.execute(
            """
            INSERT INTO external_contract_queries (
                query_id,
                source_task_id,
                source_order_type,
                source_order_id,
                request_reason,
                query_json,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(query_id) DO UPDATE SET
                source_task_id = excluded.source_task_id,
                source_order_type = excluded.source_order_type,
                source_order_id = excluded.source_order_id,
                request_reason = excluded.request_reason,
                query_json = excluded.query_json,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                query_id,
                query.get("taskId"),
                source_order_type,
                order_id,
                query.get("reason"),
                json.dumps(query, ensure_ascii=False, sort_keys=True),
                "requested",
                created_at,
                created_at,
            ),
        )
        conn.commit()

    sync_result = update_onchain_state(targets, rpc_url)
    candidates: list[dict[str, Any]] = []
    for result in sync_result["results"]:
        target_id = result["targetId"]
        state = _get_latest_onchain_state(target_id)
        if state is None:
            continue
        candidate = {
            "targetId": target_id,
            "chainName": state.get("chainName"),
            "intent": state.get("intent", {}),
            "reads": state.get("reads", {}),
            "isValid": state.get("isValid"),
            "skipped": state.get("skipped", []),
            "errors": state.get("errors", []),
            "checkedAt": state.get("checkedAt"),
        }
        candidates.append(candidate)
        _record_external_snapshot(query_id, target_id, state, result, candidate, created_at)

    status = "completed"
    if not targets:
        status = "no_targets"
    elif any(candidate.get("errors") for candidate in candidates):
        status = "completed_with_errors"

    with sqlite3.connect(EXTERNAL_CONTRACTS_DB) as conn:
        conn.execute(
            """
            UPDATE external_contract_queries
            SET status = ?, updated_at = ?
            WHERE query_id = ?
            """,
            (status, _now_iso(), query_id),
        )
        conn.commit()

    return {
        "status": status,
        "queryId": query_id,
        "sourceOrderType": source_order_type,
        "sourceOrderId": order_id,
        "checked": sync_result["checked"],
        "changed": sync_result["changed"],
        "candidates": candidates,
        "syncResult": sync_result,
    }


def get_external_contract_context(query_id: str) -> dict[str, Any]:
    """
    讀取某次外部合約查詢的完整上下文，供 runner 再交回 agents。

    輸入：
    - `query_id`：`request_external_contract_data()` 回傳的查詢 id。

    輸出：
    - 回傳 dict，包含 query 本身、鏈上 snapshots 與整理後 candidates。

    副作用：
    - 無；只讀取 `external_contracts.db`。
    """
    _init_external_contracts_database()
    with sqlite3.connect(EXTERNAL_CONTRACTS_DB) as conn:
        conn.row_factory = sqlite3.Row
        query = conn.execute(
            "SELECT * FROM external_contract_queries WHERE query_id = ?",
            (query_id,),
        ).fetchone()
        if query is None:
            raise ValueError(f"query_id={query_id} 不存在")
        snapshots = conn.execute(
            """
            SELECT target_id, onchain_state_json, onchain_update_json, created_at
            FROM external_contract_snapshots
            WHERE query_id = ?
            ORDER BY id ASC
            """,
            (query_id,),
        ).fetchall()
        candidates = conn.execute(
            """
            SELECT target_id, candidate_json, is_valid, created_at
            FROM external_contract_candidates
            WHERE query_id = ?
            ORDER BY id ASC
            """,
            (query_id,),
        ).fetchall()

    return {
        "query": dict(query),
        "snapshots": [
            {
                "targetId": row["target_id"],
                "onchainState": json.loads(row["onchain_state_json"]),
                "onchainUpdate": json.loads(row["onchain_update_json"]),
                "createdAt": row["created_at"],
            }
            for row in snapshots
        ],
        "candidates": [
            {
                "targetId": row["target_id"],
                "candidate": json.loads(row["candidate_json"]),
                "isValid": None if row["is_valid"] is None else bool(row["is_valid"]),
                "createdAt": row["created_at"],
            }
            for row in candidates
        ],
    }


def _extract_order_id(order: dict[str, Any]) -> Optional[int]:
    """
    從本地訂單 payload 取出 id。

    輸入：
    - `order`：本地訂單 dict。

    輸出：
    - 有 id 時回傳整數，否則回傳 `None`。
    """
    raw_id = order.get("id") or order.get("orderId") or order.get("sellOrderId") or order.get("buyOrderId")
    return int(raw_id) if raw_id is not None else None


def _extract_sync_targets(query: dict[str, Any]) -> list[dict[str, Any]]:
    """
    從 agents 查詢需求取出鏈上同步 targets。

    輸入：
    - `query`：agents 的外部合約查詢 dict。

    輸出：
    - 回傳 target list；若未提供則回傳空 list。
    """
    if isinstance(query.get("syncTargets"), list):
        return query["syncTargets"]
    if isinstance(query.get("target"), dict):
        return [query["target"]]
    return []


def _get_latest_onchain_state(target_id: str) -> Optional[dict[str, Any]]:
    """
    讀取某個鏈上 target 的最新狀態快照。

    輸入：
    - `target_id`：鏈上 target id。

    輸出：
    - 找到時回傳狀態 dict；找不到時回傳 `None`。
    """
    _init_database()
    with sqlite3.connect(ONCHAIN_STATE_DB) as conn:
        row = conn.execute(
            "SELECT latest_state_json FROM onchain_states WHERE target_id = ?",
            (target_id,),
        ).fetchone()
    return json.loads(row[0]) if row else None


def _record_external_snapshot(
    query_id: str,
    target_id: str,
    state: dict[str, Any],
    update_result: dict[str, Any],
    candidate: dict[str, Any],
    created_at: str,
) -> None:
    """
    將單筆鏈上快照與整理後候選資料寫入外部合約 DB。

    輸入：
    - `query_id`：外部查詢 id。
    - `target_id`：鏈上 target id。
    - `state`：完整鏈上狀態。
    - `update_result`：差異更新摘要。
    - `candidate`：交給 agents 使用的候選資料。
    - `created_at`：紀錄建立時間。

    輸出：
    - 無回傳值。

    副作用：
    - 新增 `external_contract_snapshots` 與 `external_contract_candidates` 紀錄。
    """
    is_valid = candidate.get("isValid")
    with sqlite3.connect(EXTERNAL_CONTRACTS_DB) as conn:
        conn.execute(
            """
            INSERT INTO external_contract_snapshots (
                query_id,
                target_id,
                onchain_state_json,
                onchain_update_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                query_id,
                target_id,
                json.dumps(state, ensure_ascii=False, sort_keys=True),
                json.dumps(update_result, ensure_ascii=False, sort_keys=True),
                created_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO external_contract_candidates (
                query_id,
                target_id,
                candidate_json,
                is_valid,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                query_id,
                target_id,
                json.dumps(candidate, ensure_ascii=False, sort_keys=True),
                None if is_valid is None else int(bool(is_valid)),
                created_at,
            ),
        )
        conn.commit()


def load_targets_from_file(path: Path) -> list[dict[str, Any]]:
    """
    從 JSON 檔讀取同步目標。

    輸入：
    - `path`：JSON 檔路徑。

    輸出：
    - 回傳 target list。

    支援格式：
    - 檔案根層是 list。
    - 或檔案根層是 dict，且含 `targets` list。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("targets"), list):
        return data["targets"]
    raise ValueError("targets JSON 必須是 list，或包含 targets list 的 object")


def get_sync_status() -> dict[str, Any]:
    """
    讀取本地鏈上同步資料庫狀態。

    輸入：
    - 無。

    輸出：
    - 回傳目前 tracked targets 與 change history 數量。
    """
    _init_database()
    with sqlite3.connect(ONCHAIN_STATE_DB) as conn:
        tracked = conn.execute("SELECT COUNT(*) FROM onchain_states").fetchone()[0]
        changes = conn.execute("SELECT COUNT(*) FROM onchain_state_changes").fetchone()[0]
    return {
        "database": str(ONCHAIN_STATE_DB),
        "trackedTargets": tracked,
        "changeRecords": changes,
    }


def run_cli() -> None:
    """
    本地命令列入口。

    輸入：
    - `status`：查看本地同步 DB 狀態。
    - `update --targets <json>`：讀取 targets JSON 並同步鏈上狀態。

    輸出：
    - 將 JSON 結果印到 stdout。
    """
    parser = argparse.ArgumentParser(description="CactusNetwork SP/Sepolia 鏈上狀態同步工具")
    parser.add_argument("command", choices=["status", "update"])
    parser.add_argument("--targets", type=Path, help="sync targets JSON 檔案")
    args = parser.parse_args()

    if args.command == "status":
        result = get_sync_status()
    else:
        if args.targets is None:
            raise SystemExit("update 需要 --targets <json>")
        result = update_onchain_state(load_targets_from_file(args.targets))

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_cli()
