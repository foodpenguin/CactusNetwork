from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "databases"
ENV_FILE = PROJECT_DIR / ".env"
ACCOUNTS_DB = DATA_DIR / "accounts.db"
BUY_ORDERS_DB = DATA_DIR / "buy_orders.db"
SELL_ORDERS_DB = DATA_DIR / "sell_orders.db"
TOKEN_HOURS = 12
DEFAULT_ACCOUNT_LEVEL = "free"
DEFAULT_DAY = 0
ORDER_STATUS_PENDING = "pending"
DEFAULT_ATTEMPTS = 0
SESSIONS: dict[str, tuple[str, datetime]] = {}


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


def _hash_password(password: str, salt: str) -> str:
    """
    將使用者密碼轉成不可逆 hash。

    輸入：
    - `password`：使用者輸入的明文密碼。
    - `salt`：建立帳號時產生的隨機鹽值。

    輸出：
    - 回傳十六進位格式的 PBKDF2-SHA256 hash 字串。

    副作用：
    - 無；此函式不會寫入資料庫。
    """
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()


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
        account_columns = ["account_name", "password_hash", "salt", "public_key", "account_level", "day", "created_at"]
        account_schema_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'accounts'"
        ).fetchone()
        account_schema_sql = account_schema_row[0] if account_schema_row else ""
        legacy_level_check = (
            _table_columns_match(conn, "accounts", account_columns)
            and "'pro'" in account_schema_sql
            and "'max'" not in account_schema_sql
        )

        if legacy_level_check:
            conn.execute("ALTER TABLE accounts RENAME TO accounts_legacy_level_check")
        elif not _table_columns_match(conn, "accounts", account_columns):
            conn.execute("DROP TABLE IF EXISTS accounts")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                account_name TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                public_key TEXT NOT NULL,
                account_level TEXT NOT NULL CHECK (account_level IN ('free', 'plus', 'max', 'admin')),
                day INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        if legacy_level_check:
            conn.execute(
                """
                INSERT INTO accounts (
                    account_name,
                    password_hash,
                    salt,
                    public_key,
                    account_level,
                    day,
                    created_at
                )
                SELECT
                    account_name,
                    password_hash,
                    salt,
                    public_key,
                    CASE account_level WHEN 'pro' THEN 'max' ELSE account_level END,
                    day,
                    created_at
                FROM accounts_legacy_level_check
                """
            )
            conn.execute("DROP TABLE accounts_legacy_level_check")
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
            "SELECT account_level FROM accounts WHERE account_name = ?",
            (account_name,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登入帳號不存在")

    return account_name, row[0]


_load_env()
_init_databases()


class CreateAccountRequest(BaseModel):
    """
    `POST /accounts` 的輸入格式。

    前端只能輸入：
    - `account_name`
    - `password`
    - `public_key`

    前端不能輸入：
    - `account_level`：一律由後端預設為 `free`。
    - `day`：一律由後端預設為 `0`。
    """

    account_name: str = Field(description="帳號名稱", examples=["admin"])
    password: str = Field(min_length=8, description="帳號密碼，至少 8 個字元", examples=["your_password"])
    public_key: str = Field(description="帳號對應之公鑰", examples=["0x1234...abcd"])

    model_config = ConfigDict(extra="forbid")


class LoginRequest(BaseModel):
    """
    `POST /login` 的輸入格式。

    輸入：
    - `account_name`：要登入的帳號名稱。
    - `password`：帳號密碼。

    輸出由 `login()` 回傳 Bearer token。
    """

    account_name: str = Field(description="登入帳號名稱", examples=["admin"])
    password: str = Field(description="登入密碼", examples=["your_password"])


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
        description="前端 MetaMask 簽名流程產生的鏈上 intent JSON；尚未接錢包時可省略。",
    )
    signature: Optional[str] = Field(
        default=None,
        description="使用者對 intent 的錢包簽名；尚未接錢包時可省略。",
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
        description="前端 MetaMask 簽名流程產生的鏈上 intent JSON；尚未接錢包時可省略。",
    )
    signature: Optional[str] = Field(
        default=None,
        description="使用者對 intent 的錢包簽名；尚未接錢包時可省略。",
    )

    model_config = ConfigDict(extra="forbid")


app = FastAPI(
    title="交易輸入最小 API",
    description="目前只提供四個公開方法：建立帳號、登入帳號、建立買單、建立賣單。",
    version="0.1.0",
)


@app.post("/accounts", summary="建立帳號接口")
def create_account(payload: CreateAccountRequest) -> dict:
    """
    建立帳號接口。

    輸入：
    - JSON body：`CreateAccountRequest`
      - `account_name`：帳號名稱。
      - `password`：帳號密碼，至少 8 個字元。
      - `public_key`：帳號對應公鑰。

    輸出：
    - `message`：建立結果文字。
    - `accountName`：建立完成的帳號名稱。
    - `publicKey`：帳號公鑰。
    - `accountLevel`：固定為 `free`。
    - `day`：固定為 `0`。
    - `createdAt`：UTC ISO 格式建立時間。

    副作用：
    - 寫入 `accounts.db` 的 `accounts` 表。
    - 密碼只保存 hash 與 salt，不保存明文。
    """
    now = datetime.now(timezone.utc).isoformat()
    salt = secrets.token_hex(16)
    try:
        with sqlite3.connect(ACCOUNTS_DB) as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    account_name,
                    password_hash,
                    salt,
                    public_key,
                    account_level,
                    day,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.account_name,
                    _hash_password(payload.password, salt),
                    salt,
                    payload.public_key,
                    DEFAULT_ACCOUNT_LEVEL,
                    DEFAULT_DAY,
                    now,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="帳號名稱已存在") from exc

    return {
        "message": "帳號已建立",
        "accountName": payload.account_name,
        "publicKey": payload.public_key,
        "accountLevel": DEFAULT_ACCOUNT_LEVEL,
        "day": DEFAULT_DAY,
        "createdAt": now,
    }


@app.post("/login", summary="登入接口")
def login(payload: LoginRequest) -> dict:
    """
    登入接口。

    輸入：
    - JSON body：`LoginRequest`
      - `account_name`：帳號名稱。
      - `password`：帳號密碼。

    輸出：
    - `message`：登入結果文字。
    - `tokenType`：固定為 `Bearer`。
    - `accessToken`：後續建立買單或賣單時要放進 Authorization header 的 token。
    - `expiresAt`：token 過期時間。

    副作用：
    - 將 token 暫存在記憶體 `SESSIONS`，目前重啟 API 後 session 會消失。
    """
    with sqlite3.connect(ACCOUNTS_DB) as conn:
        row = conn.execute(
            "SELECT password_hash, salt FROM accounts WHERE account_name = ?",
            (payload.account_name,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="帳號或密碼錯誤")

        password_hash, salt = row
        if not hmac.compare_digest(_hash_password(payload.password, salt), password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="帳號或密碼錯誤")

        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
        SESSIONS[token] = (payload.account_name, expires_at)

    return {
        "message": "登入成功",
        "tokenType": "Bearer",
        "accessToken": token,
        "expiresAt": expires_at.isoformat(),
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
