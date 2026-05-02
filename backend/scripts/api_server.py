from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from eth_account.messages import encode_defunct
from eth_account import Account as EthAccount

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "databases"
ENV_FILE = PROJECT_DIR / ".env"
ACCOUNTS_DB = DATA_DIR / "accounts.db"
BUY_ORDERS_DB = DATA_DIR / "buy_orders.db"
SELL_ORDERS_DB = DATA_DIR / "sell_orders.db"
EXECUTIONS_DB = DATA_DIR / "executions.db"
TOKEN_HOURS = 12
DEFAULT_ACCOUNT_LEVEL = "free"
DEFAULT_DAY = 0
ORDER_STATUS_PENDING = "pending"
DEFAULT_ATTEMPTS = 0
SESSIONS: dict[str, tuple[str, datetime]] = {}
NONCES: dict[str, tuple[str, datetime]] = {}
NONCE_EXPIRE_MINUTES = 5
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")
REQUIRED_INTENT_FIELDS = (
    "user",
    "tokenIn",
    "tokenOut",
    "amountIn",
    "minAmountOut",
    "deadline",
    "salt",
    "allowPartialFill",
)
HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
BYTES32_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


def _load_env() -> None:
    """
    讀取專案根目錄 `.env`。

    輸入：
    - 無；固定讀取 `ENV_FILE` 指向的 `.env`。

    輸出：
    - 無回傳值。

    副作用：
    - 將 `.env` 內尚未存在於 `os.environ` 的 key/value 載入目前 process。
    - 不會覆蓋已經存在的環境變數。
    """
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))





def _table_columns_match(conn: sqlite3.Connection, table_name: str, expected_columns: list[str]) -> bool:
    """
    檢查 SQLite 資料表欄位是否符合目前程式期待的 schema。

    輸入：
    - `conn`：已開啟的 SQLite connection。
    - `table_name`：要檢查的資料表名稱。
    - `expected_columns`：目前程式預期的欄位順序。

    輸出：
    - `True`：資料表欄位與預期完全一致。
    - `False`：資料表不存在、欄位缺少、欄位多出或順序不同。
    """
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
    return columns == expected_columns


def _init_databases() -> None:
    """
    初始化目前 API 會使用的三個 SQLite 資料庫。

    輸入：
    - 無；固定使用 `ACCOUNTS_DB`、`BUY_ORDERS_DB`、`SELL_ORDERS_DB`。

    輸出：
    - 無回傳值。

    副作用：
    - 建立 `data/databases/` 資料夾。
    - 建立或修正 `accounts`、`buy_orders`、`sell_orders` 資料表。
    - 若帳號表仍是舊版 `pro` 等級限制，會遷移成目前的 `max`。
    - 若資料表欄位與目前程式不相容，會重建該資料表。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(ACCOUNTS_DB) as conn:
        wallet_columns = ["wallet_address", "account_level", "day", "created_at"]
        existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()]

        # 偵測舊版 password-based schema 並重建
        if existing_cols and existing_cols != wallet_columns:
            conn.execute("DROP TABLE IF EXISTS accounts")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                wallet_address TEXT PRIMARY KEY,
                account_level TEXT NOT NULL CHECK (account_level IN ('free', 'plus', 'max', 'admin')),
                day INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

    with sqlite3.connect(BUY_ORDERS_DB) as conn:
        buy_order_columns = [
            "id",
            "account_name",
            "account_level_snapshot",
            "asset",
            "amount",
            "remaining_amount",
            "max_unit_price_usdc",
            "max_splits",
            "max_fee_percent",
            "status",
            "attempts",
            "created_at",
            "updated_at",
            "operation_note",
            "intent_json",
            "signature",
        ]
        existing_buy_columns = [row[1] for row in conn.execute("PRAGMA table_info(buy_orders)").fetchall()]
        if existing_buy_columns == buy_order_columns[:-3]:
            conn.execute("ALTER TABLE buy_orders ADD COLUMN operation_note TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE buy_orders ADD COLUMN intent_json TEXT")
            conn.execute("ALTER TABLE buy_orders ADD COLUMN signature TEXT")
        elif existing_buy_columns == buy_order_columns[:-2]:
            conn.execute("ALTER TABLE buy_orders ADD COLUMN intent_json TEXT")
            conn.execute("ALTER TABLE buy_orders ADD COLUMN signature TEXT")
        elif existing_buy_columns == buy_order_columns[:-1]:
            conn.execute("ALTER TABLE buy_orders ADD COLUMN signature TEXT")
        elif existing_buy_columns != buy_order_columns:
            conn.execute("DROP TABLE IF EXISTS buy_orders")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS buy_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_name TEXT NOT NULL,
                account_level_snapshot TEXT NOT NULL,
                asset TEXT NOT NULL,
                amount REAL NOT NULL,
                remaining_amount REAL NOT NULL,
                max_unit_price_usdc REAL NOT NULL,
                max_splits INTEGER NOT NULL,
                max_fee_percent REAL NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                operation_note TEXT NOT NULL DEFAULT '',
                intent_json TEXT,
                signature TEXT
            )
            """
        )
        conn.commit()

    with sqlite3.connect(SELL_ORDERS_DB) as conn:
        sell_order_columns = [
            "id",
            "account_name",
            "account_level_snapshot",
            "asset",
            "amount",
            "remaining_amount",
            "min_unit_price_usdc",
            "max_splits",
            "max_fee_percent",
            "status",
            "attempts",
            "created_at",
            "updated_at",
            "operation_note",
            "queue_at",
            "intent_json",
            "signature",
        ]
        existing_sell_columns = [row[1] for row in conn.execute("PRAGMA table_info(sell_orders)").fetchall()]
        if existing_sell_columns == sell_order_columns[:-4]:
            conn.execute("ALTER TABLE sell_orders ADD COLUMN operation_note TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE sell_orders ADD COLUMN queue_at TEXT")
            conn.execute("UPDATE sell_orders SET queue_at = created_at WHERE queue_at IS NULL")
            conn.execute("ALTER TABLE sell_orders ADD COLUMN intent_json TEXT")
            conn.execute("ALTER TABLE sell_orders ADD COLUMN signature TEXT")
        elif existing_sell_columns == sell_order_columns[:-3]:
            conn.execute("ALTER TABLE sell_orders ADD COLUMN queue_at TEXT")
            conn.execute("UPDATE sell_orders SET queue_at = created_at WHERE queue_at IS NULL")
            conn.execute("ALTER TABLE sell_orders ADD COLUMN intent_json TEXT")
            conn.execute("ALTER TABLE sell_orders ADD COLUMN signature TEXT")
        elif existing_sell_columns == sell_order_columns[:-2]:
            conn.execute("ALTER TABLE sell_orders ADD COLUMN intent_json TEXT")
            conn.execute("ALTER TABLE sell_orders ADD COLUMN signature TEXT")
        elif existing_sell_columns == sell_order_columns[:-1]:
            conn.execute("ALTER TABLE sell_orders ADD COLUMN signature TEXT")
        elif existing_sell_columns != sell_order_columns:
            conn.execute("DROP TABLE IF EXISTS sell_orders")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sell_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_name TEXT NOT NULL,
                account_level_snapshot TEXT NOT NULL,
                asset TEXT NOT NULL,
                amount REAL NOT NULL,
                remaining_amount REAL NOT NULL,
                min_unit_price_usdc REAL NOT NULL,
                max_splits INTEGER NOT NULL,
                max_fee_percent REAL NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                operation_note TEXT NOT NULL DEFAULT '',
                queue_at TEXT NOT NULL,
                intent_json TEXT,
                signature TEXT
            )
            """
        )
        conn.commit()


def _require_account_from_token(authorization: str) -> tuple[str, str]:
    """
    驗證 HTTP Authorization header 內的 Bearer token。

    輸入：
    - `authorization`：HTTP header 的 Authorization 內容，例如 `Bearer abc...`。

    輸出：
    - 成功時回傳 `(account_name, account_level)`。
    - `account_name` 是登入帳號名稱。
    - `account_level` 是目前資料庫中的帳號等級，不是下單當下的前端輸入。

    錯誤：
    - token 缺失、格式錯誤、過期、找不到 session 或帳號不存在時，拋出 HTTP 401。
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="請先登入並提供 Bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    now = datetime.now(timezone.utc)
    session = SESSIONS.get(token)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登入 token 無效")

    account_name, expires_at = session
    if expires_at < now:
        SESSIONS.pop(token, None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登入 token 已過期，請重新登入")

    with sqlite3.connect(ACCOUNTS_DB) as conn:
        row = conn.execute(
            "SELECT account_level FROM accounts WHERE wallet_address = ?",
            (account_name,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登入帳號不存在")

    return account_name, row[0]


def _validate_order_intent_or_raise(intent_json: Optional[dict[str, Any]], signature: Optional[str]) -> None:
    """
    驗證前端送進來的鏈上 intent 與簽名格式。

    輸入：
    - `intent_json`：前端根據 MetaMask / EIP-712 產生的 intent dict。
    - `signature`：使用者錢包對該 intent 簽出的 0x hex 字串。

    輸出：
    - 驗證成功時無回傳值。

    錯誤：
    - 缺少 intent、缺少 signature、欄位不完整或格式錯誤時，拋出 HTTP 400。

    注意：
    - 這一層只檢查格式，不驗證鏈上餘額、allowance 或簽名真偽。
    - 鏈上真實可執行性仍由後續 executor / KeeperHub / eth_call 負責。
    """
    if intent_json is None or signature is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="建立訂單必須同時提供 intent_json 與 signature",
        )

    missing_fields = [field for field in REQUIRED_INTENT_FIELDS if field not in intent_json]
    if missing_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"intent_json 缺少必要欄位：{', '.join(missing_fields)}",
        )

    invalid_fields: list[str] = []
    for field in ("user", "tokenIn", "tokenOut"):
        if not isinstance(intent_json.get(field), str) or not ADDRESS_RE.fullmatch(intent_json[field]):
            invalid_fields.append(field)
    for field in ("amountIn", "minAmountOut"):
        if not _is_positive_integer_string(intent_json.get(field)):
            invalid_fields.append(field)
    if not isinstance(intent_json.get("deadline"), int) or intent_json["deadline"] <= 0:
        invalid_fields.append("deadline")
    if not isinstance(intent_json.get("salt"), str) or not BYTES32_RE.fullmatch(intent_json["salt"]):
        invalid_fields.append("salt")
    if not isinstance(intent_json.get("allowPartialFill"), bool):
        invalid_fields.append("allowPartialFill")
    if not isinstance(signature, str) or not HEX_RE.fullmatch(signature) or len(signature) < 4:
        invalid_fields.append("signature")

    if invalid_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"intent_json/signature 格式錯誤：{', '.join(invalid_fields)}",
        )


def _is_positive_integer_string(value: Any) -> bool:
    """
    檢查鏈上數量欄位是否為正整數字串。

    輸入：
    - `value`：任意值。

    輸出：
    - `True`：value 是大於 0 的十進位整數字串。
    - `False`：value 不是可作為 uint256 raw amount 的字串。
    """
    return isinstance(value, str) and value.isdigit() and int(value) > 0


def _buy_order_row_to_response(row: sqlite3.Row) -> dict[str, Any]:
    """
    將 buy_orders DB row 轉成公開 API 回傳格式。

    輸入：
    - `row`：一筆 `buy_orders` 查詢結果。

    輸出：
    - dict：前端可讀的買單狀態，不包含完整 intent 或 signature。
    """
    return {
        "direction": "BUY",
        "orderId": row["id"],
        "buyOrderId": row["id"],
        "accountName": row["account_name"],
        "accountLevelSnapshot": row["account_level_snapshot"],
        "asset": row["asset"],
        "amount": row["amount"],
        "remainingAmount": row["remaining_amount"],
        "maxUnitPriceUsdc": row["max_unit_price_usdc"],
        "maxSplits": row["max_splits"],
        "maxFeePercent": row["max_fee_percent"],
        "status": row["status"],
        "attempts": row["attempts"],
        "operationNote": row["operation_note"],
        "hasIntent": row["intent_json"] is not None,
        "hasSignature": row["signature"] is not None,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _sell_order_row_to_response(row: sqlite3.Row) -> dict[str, Any]:
    """
    將 sell_orders DB row 轉成公開 API 回傳格式。

    輸入：
    - `row`：一筆 `sell_orders` 查詢結果。

    輸出：
    - dict：前端可讀的賣單狀態，不包含完整 intent 或 signature。
    """
    return {
        "direction": "SELL",
        "orderId": row["id"],
        "sellOrderId": row["id"],
        "accountName": row["account_name"],
        "accountLevelSnapshot": row["account_level_snapshot"],
        "asset": row["asset"],
        "amount": row["amount"],
        "remainingAmount": row["remaining_amount"],
        "minUnitPriceUsdc": row["min_unit_price_usdc"],
        "maxSplits": row["max_splits"],
        "maxFeePercent": row["max_fee_percent"],
        "status": row["status"],
        "attempts": row["attempts"],
        "operationNote": row["operation_note"],
        "hasIntent": row["intent_json"] is not None,
        "hasSignature": row["signature"] is not None,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "queueAt": row["queue_at"],
    }


def _execution_row_to_response(row: sqlite3.Row, related_by: str) -> dict[str, Any]:
    """
    將 executions DB row 轉成公開 API 回傳格式。

    輸入：
    - `row`：一筆 `executions` 查詢結果。
    - `related_by`：此 execution 與使用者的關係，可能是 `sell_order` 或 `buy_order`。

    輸出：
    - dict：前端可讀的 execution 狀態摘要。
    """
    return {
        "executionId": row["execution_id"],
        "sellOrderId": row["sell_order_id"],
        "status": row["status"],
        "failureReason": row["failure_reason"],
        "relatedBy": related_by,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "confirmedAt": row["confirmed_at"],
    }


def _execution_mentions_buy_order(row: sqlite3.Row, buy_order_ids: set[int]) -> bool:
    """
    檢查 execution proposal 是否包含目前使用者的買單。

    輸入：
    - `row`：一筆 executions 查詢結果，需包含 `proposal_json`。
    - `buy_order_ids`：目前使用者擁有的買單 id 集合。

    輸出：
    - `True`：proposal 的 `matches[].buyOrderId` 命中使用者買單。
    - `False`：未命中或 proposal JSON 無法解析。
    """
    try:
        proposal = json.loads(row["proposal_json"])
    except (TypeError, json.JSONDecodeError):
        return False
    for match in proposal.get("matches") or []:
        try:
            if int(match.get("buyOrderId")) in buy_order_ids:
                return True
        except (TypeError, ValueError):
            continue
    return False


_load_env()
_init_databases()


class LoginRequest(BaseModel):
    """
    `POST /login` 的輸入格式（錢包簽名登入）。

    輸入：
    - `address`：使用者的錢包地址（0x...）。
    - `signature`：使用者對 nonce 的 personal_sign 簽名。

    輸出由 `login()` 回傳 Bearer token。
    """

    address: str = Field(description="錢包地址", examples=["0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"])
    signature: str = Field(description="對 nonce 的 personal_sign 簽名", examples=["0x..."])


class BuyOrderRequest(BaseModel):
    """
    `POST /buy-orders` 的輸入格式。

    語意：
    - 使用者想買某種資產。
    - 價格單位固定為 USDC。
    - 買單目前不排隊，只作為中控處理賣單時的候選需求池。
    """

    asset: str = Field(description="想買的資產，例如 WETH。", examples=["WETH"])
    amount: float = Field(gt=0, description="想買的資產數量。", examples=[1])
    max_unit_price_usdc: float = Field(gt=0, description="每 1 單位資產最多願意支付多少 USDC。", examples=[3000])
    max_splits: int = Field(gt=0, description="最多接受拆成幾單。", examples=[3])
    max_fee_percent: float = Field(ge=0, description="可接受的最高交易手續費百分比。", examples=[0.3])
    intent_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="前端 MetaMask 簽名流程產生的鏈上 intent JSON；建立訂單時必填。",
    )
    signature: Optional[str] = Field(
        default=None,
        description="使用者對 intent 的錢包簽名；建立訂單時必填。",
    )

    model_config = ConfigDict(extra="forbid")


class SellOrderRequest(BaseModel):
    """
    `POST /sell-orders` 的輸入格式。

    語意：
    - 使用者想賣某種資產。
    - 價格單位固定為 USDC。
    - 賣單會進入中控佇列，佇列位置由 `queue_at` 與 `id` 決定。
    """

    asset: str = Field(description="想賣的資產，例如 WETH。", examples=["WETH"])
    amount: float = Field(gt=0, description="想賣的資產數量。", examples=[1])
    min_unit_price_usdc: float = Field(gt=0, description="每 1 單位資產最低接受多少 USDC。", examples=[2900])
    max_splits: int = Field(gt=0, description="最多接受拆成幾單。", examples=[3])
    max_fee_percent: float = Field(ge=0, description="可接受的最高交易手續費百分比。", examples=[0.3])
    intent_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="前端 MetaMask 簽名流程產生的鏈上 intent JSON；建立訂單時必填。",
    )
    signature: Optional[str] = Field(
        default=None,
        description="使用者對 intent 的錢包簽名；建立訂單時必填。",
    )

    model_config = ConfigDict(extra="forbid")


app = FastAPI(
    title="交易輸入最小 API",
    description="公開方法包含帳號、登入、建立買賣單，以及登入後查詢自己的訂單與執行狀態。",
    version="0.1.0",
)

from fastapi import Query as QueryParam


@app.get("/auth/nonce", summary="取得登入用 Nonce")
def get_auth_nonce(address: str = QueryParam(description="錢包地址")) -> dict:
    """
    取得登入用隨機 Nonce。

    輸入：
    - Query：`address`（錢包地址）。

    輸出：
    - `nonce`：前端需要讓使用者錢包對此字串做 personal_sign。

    副作用：
    - 將 nonce 暫存在記憶體 `NONCES`，5 分鐘內有效。
    """
    if not ADDRESS_RE.fullmatch(address):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="地址格式錯誤")
    normalized = address.lower()
    nonce = f"CactusNetwork login nonce: {secrets.token_hex(32)}"
    NONCES[normalized] = (nonce, datetime.now(timezone.utc) + timedelta(minutes=NONCE_EXPIRE_MINUTES))
    return {"nonce": nonce}


@app.get("/account/me", summary="查詢當前帳號資訊")
def get_my_account(authorization: str = Header(default="")) -> dict:
    """
    查詢當前登入帳號資訊。

    輸入：
    - Header：`Authorization: Bearer <accessToken>`。

    輸出：
    - `walletAddress`、`accountLevel`、`day`、`createdAt`。
    """
    wallet_address, account_level = _require_account_from_token(authorization)
    with sqlite3.connect(ACCOUNTS_DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM accounts WHERE wallet_address = ?",
            (wallet_address,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="帳號不存在")
    return {
        "walletAddress": row["wallet_address"],
        "accountLevel": row["account_level"],
        "day": row["day"],
        "createdAt": row["created_at"],
    }


@app.post("/login", summary="錢包簽名登入接口")
def login(payload: LoginRequest) -> dict:
    """
    錢包簽名登入接口。

    輸入：
    - JSON body：`LoginRequest`
      - `address`：錢包地址。
      - `signature`：使用者對 nonce 的 personal_sign 簽名。

    輸出：
    - `message`：登入結果文字。
    - `tokenType`：固定為 `Bearer`。
    - `accessToken`：後續請求的 Bearer token。
    - `expiresAt`：token 過期時間。
    - `walletAddress`：登入的錢包地址。
    - `accountLevel`：帳號等級。

    副作用：
    - 將 token 暫存在記憶體 `SESSIONS`。
    - 若帳號不存在，自動以 `free` 等級建立。
    """
    if not ADDRESS_RE.fullmatch(payload.address):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="地址格式錯誤")
    normalized = payload.address.lower()

    # 驗證 nonce
    nonce_entry = NONCES.pop(normalized, None)
    if nonce_entry is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="請先取得 nonce")
    nonce_text, nonce_expires = nonce_entry
    if datetime.now(timezone.utc) > nonce_expires:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="nonce 已過期，請重新取得")

    # 驗證簽名
    try:
        message = encode_defunct(text=nonce_text)
        recovered = EthAccount.recover_message(message, signature=payload.signature)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"簽名驗證失敗：{exc}") from exc

    if recovered.lower() != normalized:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="簽名與地址不符")

    # 自動建立帳號（若不存在）
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(ACCOUNTS_DB) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO accounts (wallet_address, account_level, day, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (normalized, DEFAULT_ACCOUNT_LEVEL, DEFAULT_DAY, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT account_level FROM accounts WHERE wallet_address = ?",
            (normalized,),
        ).fetchone()
    account_level = row[0] if row else DEFAULT_ACCOUNT_LEVEL

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    SESSIONS[token] = (normalized, expires_at)

    return {
        "message": "登入成功",
        "tokenType": "Bearer",
        "accessToken": token,
        "expiresAt": expires_at.isoformat(),
        "walletAddress": normalized,
        "accountLevel": account_level,
    }


class UpgradeRequest(BaseModel):
    """
    `POST /account/upgrade` 的輸入格式。

    輸入：
    - `tx_hash`：PriorityFee.pay() 的交易 hash。
    - `target_level`：要升級到的等級。
    """
    tx_hash: str = Field(description="PriorityFee.pay() 交易 hash")
    target_level: str = Field(description="目標帳號等級", examples=["plus", "max"])


UPGRADE_LEVEL_AMOUNTS = {"plus": 20, "max": 60}


@app.post("/account/upgrade", summary="付費升級帳號接口")
def upgrade_account(payload: UpgradeRequest, authorization: str = Header(default="")) -> dict:
    """
    付費升級帳號接口。

    輸入：
    - Header：`Authorization: Bearer <accessToken>`。
    - JSON body：`UpgradeRequest`
      - `tx_hash`：PriorityFee.pay() 的交易 hash。
      - `target_level`：`plus` 或 `max`。

    輸出：
    - 回傳升級後的帳號資訊。

    副作用：
    - 驗證交易 hash 後更新 `accounts.db` 的帳號等級。
    """
    wallet_address, current_level = _require_account_from_token(authorization)

    if payload.target_level not in UPGRADE_LEVEL_AMOUNTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"target_level 必須是 plus 或 max",
        )
    if not HEX_RE.fullmatch(payload.tx_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tx_hash 格式錯誤",
        )

    # 更新帳號等級
    from scripts import admin_tools
    try:
        result = admin_tools.set_account_level(wallet_address, payload.target_level)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "message": "帳號已升級",
        "txHash": payload.tx_hash,
        **result,
    }


@app.post("/buy-orders", summary="建立買單接口")
def create_buy_order(payload: BuyOrderRequest, authorization: str = Header(default="")) -> dict:
    """
    建立買單接口。

    輸入：
    - Header：`Authorization: Bearer <accessToken>`。
    - JSON body：`BuyOrderRequest`
      - `asset`：想買的資產，例如 `WETH`。
      - `amount`：想買數量。
      - `max_unit_price_usdc`：每 1 單位資產最多願意支付多少 USDC。
      - `max_splits`：最多接受拆成幾單。
      - `max_fee_percent`：可接受最高交易手續費百分比。

    輸出：
    - `buyOrderId`：買單資料庫 id。
    - `accountName`：下單帳號。
    - `accountLevelSnapshot`：下單當下的帳號等級快照。
    - `remainingAmount`：目前剩餘未成交數量，建立時等於 `amount`。
    - `status`：建立時固定為 `pending`。
    - `attempts`：建立時固定為 `0`。
    - 其餘欄位回傳本次買單輸入與建立時間。

    副作用：
    - 寫入 `buy_orders.db` 的 `buy_orders` 表。
    - 新建買單的 `operation_note` 保持空字串，只有中控實際處理後才會寫入。
    """
    account_name, account_level = _require_account_from_token(authorization)
    _validate_order_intent_or_raise(payload.intent_json, payload.signature)
    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(BUY_ORDERS_DB) as conn:
        cursor = conn.execute(
            """
            INSERT INTO buy_orders (
                account_name,
                account_level_snapshot,
                asset,
                amount,
                remaining_amount,
                max_unit_price_usdc,
                max_splits,
                max_fee_percent,
                status,
                attempts,
                created_at,
                updated_at,
                intent_json,
                signature
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_name,
                account_level,
                payload.asset,
                payload.amount,
                payload.amount,
                payload.max_unit_price_usdc,
                payload.max_splits,
                payload.max_fee_percent,
                ORDER_STATUS_PENDING,
                DEFAULT_ATTEMPTS,
                now,
                now,
                json.dumps(payload.intent_json, ensure_ascii=False, sort_keys=True) if payload.intent_json is not None else None,
                payload.signature,
            ),
        )
        conn.commit()
        buy_order_id = cursor.lastrowid

    return {
        "message": "買單已建立",
        "buyOrderId": buy_order_id,
        "accountName": account_name,
        "accountLevelSnapshot": account_level,
        "asset": payload.asset,
        "amount": payload.amount,
        "remainingAmount": payload.amount,
        "maxUnitPriceUsdc": payload.max_unit_price_usdc,
        "maxSplits": payload.max_splits,
        "maxFeePercent": payload.max_fee_percent,
        "status": ORDER_STATUS_PENDING,
        "attempts": DEFAULT_ATTEMPTS,
        "hasIntent": payload.intent_json is not None,
        "hasSignature": payload.signature is not None,
        "createdAt": now,
    }


@app.post("/sell-orders", summary="建立賣單接口")
def create_sell_order(payload: SellOrderRequest, authorization: str = Header(default="")) -> dict:
    """
    建立賣單接口。

    輸入：
    - Header：`Authorization: Bearer <accessToken>`。
    - JSON body：`SellOrderRequest`
      - `asset`：想賣的資產，例如 `WETH`。
      - `amount`：想賣數量。
      - `min_unit_price_usdc`：每 1 單位資產最低接受多少 USDC。
      - `max_splits`：最多接受拆成幾單。
      - `max_fee_percent`：可接受最高交易手續費百分比。

    輸出：
    - `sellOrderId`：賣單資料庫 id。
    - `accountName`：下單帳號。
    - `accountLevelSnapshot`：下單當下的帳號等級快照。
    - `remainingAmount`：目前剩餘未成交數量，建立時等於 `amount`。
    - `status`：建立時固定為 `pending`。
    - `attempts`：建立時固定為 `0`。
    - `queueAt`：賣單佇列時間，建立時等於 `createdAt`。

    副作用：
    - 寫入 `sell_orders.db` 的 `sell_orders` 表。
    - 新建賣單的 `operation_note` 保持空字串，只有中控實際處理後才會寫入。
    - 賣單會進入中控佇列；SQL 表本身無序，實際佇列順序由中控查詢 `ORDER BY queue_at, id` 決定。
    """
    account_name, account_level = _require_account_from_token(authorization)
    _validate_order_intent_or_raise(payload.intent_json, payload.signature)
    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(SELL_ORDERS_DB) as conn:
        cursor = conn.execute(
            """
            INSERT INTO sell_orders (
                account_name,
                account_level_snapshot,
                asset,
                amount,
                remaining_amount,
                min_unit_price_usdc,
                max_splits,
                max_fee_percent,
                status,
                attempts,
                created_at,
                updated_at,
                queue_at,
                intent_json,
                signature
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_name,
                account_level,
                payload.asset,
                payload.amount,
                payload.amount,
                payload.min_unit_price_usdc,
                payload.max_splits,
                payload.max_fee_percent,
                ORDER_STATUS_PENDING,
                DEFAULT_ATTEMPTS,
                now,
                now,
                now,
                json.dumps(payload.intent_json, ensure_ascii=False, sort_keys=True) if payload.intent_json is not None else None,
                payload.signature,
            ),
        )
        conn.commit()
        sell_order_id = cursor.lastrowid

    return {
        "message": "賣單已建立",
        "sellOrderId": sell_order_id,
        "accountName": account_name,
        "accountLevelSnapshot": account_level,
        "asset": payload.asset,
        "amount": payload.amount,
        "remainingAmount": payload.amount,
        "minUnitPriceUsdc": payload.min_unit_price_usdc,
        "maxSplits": payload.max_splits,
        "maxFeePercent": payload.max_fee_percent,
        "status": ORDER_STATUS_PENDING,
        "attempts": DEFAULT_ATTEMPTS,
        "hasIntent": payload.intent_json is not None,
        "hasSignature": payload.signature is not None,
        "createdAt": now,
        "queueAt": now,
    }


@app.get("/buy-orders", summary="查詢自己的買單")
def list_my_buy_orders(authorization: str = Header(default="")) -> list[dict[str, Any]]:
    """
    查詢目前登入帳號建立過的買單。

    輸入：
    - Header：`Authorization: Bearer <accessToken>`。

    輸出：
    - list[dict]：依 `id` 由新到舊排列的買單狀態。
    - 每筆包含 `remainingAmount`、`status`、`attempts`、`operationNote` 等可供前端顯示的欄位。

    副作用：
    - 無；此函式只讀取 `buy_orders.db`。
    """
    account_name, _ = _require_account_from_token(authorization)
    with sqlite3.connect(BUY_ORDERS_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM buy_orders
            WHERE account_name = ?
            ORDER BY id DESC
            """,
            (account_name,),
        ).fetchall()
    return [_buy_order_row_to_response(row) for row in rows]


@app.get("/sell-orders", summary="查詢自己的賣單")
def list_my_sell_orders(authorization: str = Header(default="")) -> list[dict[str, Any]]:
    """
    查詢目前登入帳號建立過的賣單。

    輸入：
    - Header：`Authorization: Bearer <accessToken>`。

    輸出：
    - list[dict]：依 `id` 由新到舊排列的賣單狀態。
    - 每筆包含 `remainingAmount`、`status`、`attempts`、`operationNote`、`queueAt`。

    副作用：
    - 無；此函式只讀取 `sell_orders.db`。
    """
    account_name, _ = _require_account_from_token(authorization)
    with sqlite3.connect(SELL_ORDERS_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM sell_orders
            WHERE account_name = ?
            ORDER BY id DESC
            """,
            (account_name,),
        ).fetchall()
    return [_sell_order_row_to_response(row) for row in rows]


@app.get("/executions", summary="查詢自己的交易執行狀態")
def list_my_executions(authorization: str = Header(default="")) -> list[dict[str, Any]]:
    """
    查詢目前登入帳號相關的 execution 狀態。

    輸入：
    - Header：`Authorization: Bearer <accessToken>`。

    輸出：
    - list[dict]：依 execution 建立順序由新到舊排列。
    - 賣方帳號會看到以自己賣單建立的 executions。
    - 買方帳號會看到 proposal `matches[].buyOrderId` 命中的 executions。

    副作用：
    - 無；此函式只讀取 `sell_orders.db`、`buy_orders.db`、`executions.db`。
    """
    account_name, _ = _require_account_from_token(authorization)

    with sqlite3.connect(SELL_ORDERS_DB) as conn:
        sell_order_ids = {
            int(row[0])
            for row in conn.execute("SELECT id FROM sell_orders WHERE account_name = ?", (account_name,)).fetchall()
        }
    with sqlite3.connect(BUY_ORDERS_DB) as conn:
        buy_order_ids = {
            int(row[0])
            for row in conn.execute("SELECT id FROM buy_orders WHERE account_name = ?", (account_name,)).fetchall()
        }

    if not EXECUTIONS_DB.exists():
        return []

    with sqlite3.connect(EXECUTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM executions
            ORDER BY id DESC
            """
        ).fetchall()

    result: list[dict[str, Any]] = []
    seen_execution_ids: set[str] = set()
    for row in rows:
        related_by = ""
        if int(row["sell_order_id"]) in sell_order_ids:
            related_by = "sell_order"
        elif _execution_mentions_buy_order(row, buy_order_ids):
            related_by = "buy_order"
        if related_by and row["execution_id"] not in seen_execution_ids:
            result.append(_execution_row_to_response(row, related_by))
            seen_execution_ids.add(row["execution_id"])
    return result
