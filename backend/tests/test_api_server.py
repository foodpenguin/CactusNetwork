import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts import api_server
from scripts.api_server import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_api_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "databases"

    monkeypatch.setattr(api_server, "DATA_DIR", data_dir)
    monkeypatch.setattr(api_server, "ACCOUNTS_DB", data_dir / "accounts.db")
    monkeypatch.setattr(api_server, "BUY_ORDERS_DB", data_dir / "buy_orders.db")
    monkeypatch.setattr(api_server, "SELL_ORDERS_DB", data_dir / "sell_orders.db")

    api_server.SESSIONS.clear()
    api_server._init_databases()


def clear_databases() -> None:
    """清空測試會碰到的資料表，讓每次測試從乾淨狀態開始。"""
    api_server.SESSIONS.clear()
    for db_path, table_name in [
        (api_server.ACCOUNTS_DB, "accounts"),
        (api_server.BUY_ORDERS_DB, "buy_orders"),
        (api_server.SELL_ORDERS_DB, "sell_orders"),
    ]:
        with sqlite3.connect(db_path) as conn:
            conn.execute(f"DELETE FROM {table_name}")
            conn.commit()


def unique_account_name(prefix: str = "test_user") -> str:
    """產生唯一測試帳號，避免撞到本機既有資料。"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def create_account_and_login() -> tuple[str, str]:
    """建立帳號並登入，回傳帳號名稱與 Bearer token。"""
    account_name = unique_account_name()
    password = "test_password_123"

    response = client.post(
        "/accounts",
        json={
            "account_name": account_name,
            "password": password,
            "public_key": "0xTestPublicKey",
        },
    )
    assert response.status_code == 200

    response = client.post(
        "/login",
        json={
            "account_name": account_name,
            "password": password,
        },
    )
    assert response.status_code == 200
    return account_name, response.json()["accessToken"]


def test_create_account_success_and_defaults() -> None:
    """測試建立帳號成功，且 account_level/day 使用系統預設值。"""
    clear_databases()
    account_name = unique_account_name()

    response = client.post(
        "/accounts",
        json={
            "account_name": account_name,
            "password": "test_password_123",
            "public_key": "0xAccountPublicKey",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["accountName"] == account_name
    assert data["publicKey"] == "0xAccountPublicKey"
    assert data["accountLevel"] == "free"
    assert data["day"] == 0

    with sqlite3.connect(api_server.ACCOUNTS_DB) as conn:
        row = conn.execute(
            "SELECT account_name, public_key, account_level, day FROM accounts WHERE account_name = ?",
            (account_name,),
        ).fetchone()
    assert row == (account_name, "0xAccountPublicKey", "free", 0)


def test_create_account_rejects_frontend_only_fields() -> None:
    """測試建立帳號時，前端不能傳 account_level 或 day。"""
    clear_databases()

    response = client.post(
        "/accounts",
        json={
            "account_name": unique_account_name("bad_level"),
            "password": "test_password_123",
            "public_key": "0xBadLevel",
            "account_level": "admin",
        },
    )
    assert response.status_code == 422

    response = client.post(
        "/accounts",
        json={
            "account_name": unique_account_name("bad_day"),
            "password": "test_password_123",
            "public_key": "0xBadDay",
            "day": 10,
        },
    )
    assert response.status_code == 422


def test_login_success_and_failure() -> None:
    """測試登入成功會回 token，錯誤密碼會被拒絕。"""
    clear_databases()
    account_name = unique_account_name()
    password = "test_password_123"
    client.post(
        "/accounts",
        json={
            "account_name": account_name,
            "password": password,
            "public_key": "0xLoginPublicKey",
        },
    )

    response = client.post("/login", json={"account_name": account_name, "password": password})
    assert response.status_code == 200
    data = response.json()
    assert data["tokenType"] == "Bearer"
    assert data["accessToken"]
    assert data["expiresAt"]

    response = client.post("/login", json={"account_name": account_name, "password": "wrong_password"})
    assert response.status_code == 401


def test_buy_and_sell_orders_require_login() -> None:
    """測試未登入不能建立買單或賣單。"""
    clear_databases()

    response = client.post(
        "/buy-orders",
        json={
            "asset": "WETH",
            "amount": 1,
            "max_unit_price_usdc": 3000,
            "max_splits": 3,
            "max_fee_percent": 0.3,
        },
    )
    assert response.status_code == 401

    response = client.post(
        "/sell-orders",
        json={
            "asset": "WETH",
            "amount": 1,
            "min_unit_price_usdc": 2900,
            "max_splits": 3,
            "max_fee_percent": 0.3,
        },
    )
    assert response.status_code == 401


def test_create_buy_order_success_and_database_defaults() -> None:
    """測試登入後可建立買單，且 DB 預設值正確。"""
    clear_databases()
    account_name, token = create_account_and_login()

    response = client.post(
        "/buy-orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "asset": "WETH",
            "amount": 1,
            "max_unit_price_usdc": 3000,
            "max_splits": 3,
            "max_fee_percent": 0.3,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["accountName"] == account_name
    assert data["accountLevelSnapshot"] == "free"
    assert data["remainingAmount"] == 1
    assert data["status"] == "pending"
    assert data["attempts"] == 0

    with sqlite3.connect(api_server.BUY_ORDERS_DB) as conn:
        row = conn.execute(
            """
            SELECT account_name, account_level_snapshot, asset, amount, remaining_amount,
                   max_unit_price_usdc, max_splits, max_fee_percent, status, attempts, operation_note
            FROM buy_orders
            WHERE id = ?
            """,
            (data["buyOrderId"],),
        ).fetchone()
    assert row == (account_name, "free", "WETH", 1.0, 1.0, 3000.0, 3, 0.3, "pending", 0, "")


def test_create_sell_order_success_and_database_defaults() -> None:
    """測試登入後可建立賣單，且 DB 預設值正確。"""
    clear_databases()
    account_name, token = create_account_and_login()

    response = client.post(
        "/sell-orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "asset": "WETH",
            "amount": 1,
            "min_unit_price_usdc": 2900,
            "max_splits": 3,
            "max_fee_percent": 0.3,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["accountName"] == account_name
    assert data["accountLevelSnapshot"] == "free"
    assert data["remainingAmount"] == 1
    assert data["status"] == "pending"
    assert data["attempts"] == 0
    assert data["queueAt"] == data["createdAt"]

    with sqlite3.connect(api_server.SELL_ORDERS_DB) as conn:
        row = conn.execute(
            """
            SELECT account_name, account_level_snapshot, asset, amount, remaining_amount,
                   min_unit_price_usdc, max_splits, max_fee_percent, status, attempts, operation_note, queue_at
            FROM sell_orders
            WHERE id = ?
            """,
            (data["sellOrderId"],),
        ).fetchone()
    assert row == (account_name, "free", "WETH", 1.0, 1.0, 2900.0, 3, 0.3, "pending", 0, "", data["createdAt"])


def test_orders_can_store_frontend_intent_and_signature() -> None:
    """測試買單/賣單可保存前端 MetaMask intent_json 與 signature。"""
    clear_databases()
    _, token = create_account_and_login()
    buy_intent = {
        "user": "0xBuyer",
        "tokenIn": "0xUSDC",
        "tokenOut": "0xWETH",
        "amountIn": "3000",
        "minAmountOut": "1",
        "deadline": 1999999999,
        "salt": "0xbuy",
        "allowPartialFill": True,
    }
    sell_intent = {
        "user": "0xSeller",
        "tokenIn": "0xWETH",
        "tokenOut": "0xUSDC",
        "amountIn": "1",
        "minAmountOut": "2900",
        "deadline": 1999999999,
        "salt": "0xsell",
        "allowPartialFill": True,
    }

    buy_response = client.post(
        "/buy-orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "asset": "WETH",
            "amount": 1,
            "max_unit_price_usdc": 3000,
            "max_splits": 3,
            "max_fee_percent": 0.3,
            "intent_json": buy_intent,
            "signature": "0xbuy_signature",
        },
    )
    sell_response = client.post(
        "/sell-orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "asset": "WETH",
            "amount": 1,
            "min_unit_price_usdc": 2900,
            "max_splits": 3,
            "max_fee_percent": 0.3,
            "intent_json": sell_intent,
            "signature": "0xsell_signature",
        },
    )

    assert buy_response.status_code == 200
    assert sell_response.status_code == 200
    assert buy_response.json()["hasIntent"] is True
    assert buy_response.json()["hasSignature"] is True
    assert sell_response.json()["hasIntent"] is True
    assert sell_response.json()["hasSignature"] is True

    with sqlite3.connect(api_server.BUY_ORDERS_DB) as conn:
        buy_row = conn.execute(
            "SELECT intent_json, signature FROM buy_orders WHERE id = ?",
            (buy_response.json()["buyOrderId"],),
        ).fetchone()
    with sqlite3.connect(api_server.SELL_ORDERS_DB) as conn:
        sell_row = conn.execute(
            "SELECT intent_json, signature FROM sell_orders WHERE id = ?",
            (sell_response.json()["sellOrderId"],),
        ).fetchone()

    assert json.loads(buy_row[0]) == buy_intent
    assert buy_row[1] == "0xbuy_signature"
    assert json.loads(sell_row[0]) == sell_intent
    assert sell_row[1] == "0xsell_signature"


def test_openapi_only_exposes_four_public_methods() -> None:
    """測試目前前端文件只顯示四個公開方法。"""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert set(paths.keys()) == {"/accounts", "/login", "/buy-orders", "/sell-orders"}
