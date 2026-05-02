import json
import sqlite3
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
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
    monkeypatch.setattr(api_server, "EXECUTIONS_DB", data_dir / "executions.db")

    api_server.SESSIONS.clear()
    api_server.NONCES.clear()
    api_server._init_databases()


def clear_databases() -> None:
    """清空測試會碰到的資料表，讓每次測試從乾淨狀態開始。"""
    api_server.SESSIONS.clear()
    api_server.NONCES.clear()
    for db_path, table_name in [
        (api_server.ACCOUNTS_DB, "accounts"),
        (api_server.BUY_ORDERS_DB, "buy_orders"),
        (api_server.SELL_ORDERS_DB, "sell_orders"),
        (api_server.EXECUTIONS_DB, "executions"),
    ]:
        if not db_path.exists():
            continue
        with sqlite3.connect(db_path) as conn:
            conn.execute(f"DELETE FROM {table_name}")
            conn.commit()


def _signature_hex(raw_signature: bytes) -> str:
    """將 eth-account 回傳的簽名字節轉成 0x hex 字串。"""
    signature = raw_signature.hex()
    return signature if signature.startswith("0x") else f"0x{signature}"


def create_wallet_and_login() -> tuple[str, str]:
    """建立測試錢包、簽 nonce 登入，回傳標準化錢包地址與 Bearer token。"""
    account = Account.create()

    nonce_response = client.get("/auth/nonce", params={"address": account.address})
    assert nonce_response.status_code == 200
    nonce = nonce_response.json()["nonce"]

    signed = Account.sign_message(encode_defunct(text=nonce), account.key)
    response = client.post(
        "/login",
        json={
            "address": account.address,
            "signature": _signature_hex(signed.signature),
        },
    )
    assert response.status_code == 200
    return account.address.lower(), response.json()["accessToken"]


def valid_intent(user: str = "0x1111111111111111111111111111111111111111") -> dict:
    """建立公開 API 測試用的完整鏈上 intent。"""
    return {
        "user": user,
        "tokenIn": "0x2222222222222222222222222222222222222222",
        "tokenOut": "0x3333333333333333333333333333333333333333",
        "amountIn": "1000000000000000000",
        "minAmountOut": "3000000000",
        "deadline": 1999999999,
        "salt": "0x" + "ab" * 32,
        "allowPartialFill": True,
    }


def valid_signature() -> str:
    """建立公開 API 測試用的 0x hex signature。"""
    return "0x" + "cd" * 65


def test_wallet_login_auto_creates_account_success_and_defaults() -> None:
    """測試錢包簽名登入會自動建立帳號，且 account_level/day 使用系統預設值。"""
    clear_databases()
    account = Account.create()
    nonce = client.get("/auth/nonce", params={"address": account.address}).json()["nonce"]
    signed = Account.sign_message(encode_defunct(text=nonce), account.key)

    response = client.post(
        "/login",
        json={
            "address": account.address,
            "signature": _signature_hex(signed.signature),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["walletAddress"] == account.address.lower()
    assert data["accountLevel"] == "free"
    assert data["tokenType"] == "Bearer"
    assert data["accessToken"]

    with sqlite3.connect(api_server.ACCOUNTS_DB) as conn:
        row = conn.execute(
            "SELECT wallet_address, account_level, day FROM accounts WHERE wallet_address = ?",
            (account.address.lower(),),
        ).fetchone()
    assert row == (account.address.lower(), "free", 0)


def test_wallet_login_rejects_invalid_address_and_wrong_signature() -> None:
    """測試登入會拒絕錯誤地址格式與地址不相符的簽名。"""
    clear_databases()

    response = client.get("/auth/nonce", params={"address": "not-an-address"})
    assert response.status_code == 400

    account = Account.create()
    other_account = Account.create()
    nonce = client.get("/auth/nonce", params={"address": account.address}).json()["nonce"]
    signed = Account.sign_message(encode_defunct(text=nonce), other_account.key)
    response = client.post(
        "/login",
        json={
            "address": account.address,
            "signature": _signature_hex(signed.signature),
        },
    )
    assert response.status_code == 401


def test_login_success_and_failure() -> None:
    """測試錢包登入成功會回 token，重複使用 nonce 會被拒絕。"""
    clear_databases()
    account = Account.create()
    nonce = client.get("/auth/nonce", params={"address": account.address}).json()["nonce"]
    signed = Account.sign_message(encode_defunct(text=nonce), account.key)
    signature = _signature_hex(signed.signature)

    response = client.post("/login", json={"address": account.address, "signature": signature})
    assert response.status_code == 200
    data = response.json()
    assert data["tokenType"] == "Bearer"
    assert data["accessToken"]
    assert data["expiresAt"]
    assert data["walletAddress"] == account.address.lower()

    response = client.post("/login", json={"address": account.address, "signature": signature})
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
            "intent_json": valid_intent(),
            "signature": valid_signature(),
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
            "intent_json": valid_intent(),
            "signature": valid_signature(),
        },
    )
    assert response.status_code == 401


def test_create_buy_order_success_and_database_defaults() -> None:
    """測試登入後可建立買單，且 DB 預設值正確。"""
    clear_databases()
    account_name, token = create_wallet_and_login()

    response = client.post(
        "/buy-orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "asset": "WETH",
            "amount": 1,
            "max_unit_price_usdc": 3000,
            "max_splits": 3,
            "max_fee_percent": 0.3,
            "intent_json": valid_intent(),
            "signature": valid_signature(),
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
    account_name, token = create_wallet_and_login()

    response = client.post(
        "/sell-orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "asset": "WETH",
            "amount": 1,
            "min_unit_price_usdc": 2900,
            "max_splits": 3,
            "max_fee_percent": 0.3,
            "intent_json": valid_intent(),
            "signature": valid_signature(),
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
    _, token = create_wallet_and_login()
    buy_intent = valid_intent("0x4444444444444444444444444444444444444444")
    sell_intent = valid_intent("0x5555555555555555555555555555555555555555")

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
            "signature": valid_signature(),
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
            "signature": valid_signature(),
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
    assert buy_row[1] == valid_signature()
    assert json.loads(sell_row[0]) == sell_intent
    assert sell_row[1] == valid_signature()


def test_create_order_rejects_incomplete_intent_before_insert() -> None:
    """測試建單時會先擋掉不完整 intent，避免壞單進入中控。"""
    clear_databases()
    _, token = create_wallet_and_login()

    response = client.post(
        "/sell-orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "asset": "WETH",
            "amount": 1,
            "min_unit_price_usdc": 2900,
            "max_splits": 3,
            "max_fee_percent": 0.3,
            "intent_json": {"probe": "not_chain_ready"},
            "signature": valid_signature(),
        },
    )

    assert response.status_code == 400
    assert "intent_json 缺少必要欄位" in response.json()["detail"]

    with sqlite3.connect(api_server.SELL_ORDERS_DB) as conn:
        count = conn.execute("SELECT count(*) FROM sell_orders").fetchone()[0]
    assert count == 0


def test_create_order_requires_signature_with_intent() -> None:
    """測試建單時 intent 與 signature 必須一起提供。"""
    clear_databases()
    _, token = create_wallet_and_login()

    response = client.post(
        "/buy-orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "asset": "WETH",
            "amount": 1,
            "max_unit_price_usdc": 3000,
            "max_splits": 3,
            "max_fee_percent": 0.3,
            "intent_json": valid_intent(),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "建立訂單必須同時提供 intent_json 與 signature"


def test_user_can_list_own_orders() -> None:
    """測試登入使用者可以查詢自己的買單與賣單狀態。"""
    clear_databases()
    account_name, token = create_wallet_and_login()

    buy_response = client.post(
        "/buy-orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "asset": "WETH",
            "amount": 2,
            "max_unit_price_usdc": 3000,
            "max_splits": 4,
            "max_fee_percent": 0.3,
            "intent_json": valid_intent(),
            "signature": valid_signature(),
        },
    )
    sell_response = client.post(
        "/sell-orders",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "asset": "WETH",
            "amount": 1,
            "min_unit_price_usdc": 2900,
            "max_splits": 2,
            "max_fee_percent": 0.2,
            "intent_json": valid_intent(),
            "signature": valid_signature(),
        },
    )

    buy_list = client.get("/buy-orders", headers={"Authorization": f"Bearer {token}"})
    sell_list = client.get("/sell-orders", headers={"Authorization": f"Bearer {token}"})

    assert buy_list.status_code == 200
    assert sell_list.status_code == 200
    assert buy_list.json()[0]["buyOrderId"] == buy_response.json()["buyOrderId"]
    assert buy_list.json()[0]["accountName"] == account_name
    assert buy_list.json()[0]["operationNote"] == ""
    assert sell_list.json()[0]["sellOrderId"] == sell_response.json()["sellOrderId"]
    assert sell_list.json()[0]["accountName"] == account_name
    assert sell_list.json()[0]["queueAt"] == sell_response.json()["queueAt"]


def test_user_can_list_related_executions_for_sell_and_buy_orders() -> None:
    """測試使用者可以查到自己賣單或買單相關的 execution 狀態。"""
    clear_databases()
    seller_name, seller_token = create_wallet_and_login()
    _, buyer_token = create_wallet_and_login()

    sell_response = client.post(
        "/sell-orders",
        headers={"Authorization": f"Bearer {seller_token}"},
        json={
            "asset": "WETH",
            "amount": 1,
            "min_unit_price_usdc": 2900,
            "max_splits": 2,
            "max_fee_percent": 0.2,
            "intent_json": valid_intent(),
            "signature": valid_signature(),
        },
    )
    buy_response = client.post(
        "/buy-orders",
        headers={"Authorization": f"Bearer {buyer_token}"},
        json={
            "asset": "WETH",
            "amount": 1,
            "max_unit_price_usdc": 3000,
            "max_splits": 2,
            "max_fee_percent": 0.2,
            "intent_json": valid_intent(),
            "signature": valid_signature(),
        },
    )

    api_server.EXECUTIONS_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(api_server.EXECUTIONS_DB) as conn:
        conn.execute(
            """
            CREATE TABLE executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL,
                task_id TEXT,
                sell_order_id INTEGER NOT NULL,
                proposal_json TEXT NOT NULL,
                execution_payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                confirmation_json TEXT,
                apply_result_json TEXT,
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                confirmed_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO executions (
                execution_id,
                sell_order_id,
                proposal_json,
                execution_payload_json,
                status,
                failure_reason,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "execution:test:1",
                sell_response.json()["sellOrderId"],
                json.dumps({"matches": [{"buyOrderId": buy_response.json()["buyOrderId"], "filledAmount": 1}]}),
                json.dumps({"actionType": 1}),
                "failed",
                "測試失敗原因",
                "2026-05-01T00:00:00+00:00",
                "2026-05-01T00:01:00+00:00",
            ),
        )
        conn.commit()

    seller_executions = client.get("/executions", headers={"Authorization": f"Bearer {seller_token}"})
    buyer_executions = client.get("/executions", headers={"Authorization": f"Bearer {buyer_token}"})

    assert seller_executions.status_code == 200
    assert buyer_executions.status_code == 200
    assert seller_executions.json()[0]["executionId"] == "execution:test:1"
    assert seller_executions.json()[0]["relatedBy"] == "sell_order"
    assert seller_executions.json()[0]["failureReason"] == "測試失敗原因"
    assert buyer_executions.json()[0]["executionId"] == "execution:test:1"
    assert buyer_executions.json()[0]["relatedBy"] == "buy_order"

    assert seller_executions.json()[0]["sellOrderId"] == sell_response.json()["sellOrderId"]
    assert seller_executions.json()[0]["status"] == "failed"
    assert seller_name.startswith("0x")


def test_openapi_exposes_public_methods() -> None:
    """測試公開文件顯示帳號、訂單建立與查詢方法。"""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert set(paths.keys()) == {
        "/auth/nonce",
        "/account/me",
        "/login",
        "/account/upgrade",
        "/buy-orders",
        "/sell-orders",
        "/executions",
    }
    assert set(paths["/auth/nonce"].keys()) == {"get"}
    assert set(paths["/account/me"].keys()) == {"get"}
    assert set(paths["/login"].keys()) == {"post"}
    assert set(paths["/account/upgrade"].keys()) == {"post"}
    assert set(paths["/buy-orders"].keys()) == {"get", "post"}
    assert set(paths["/sell-orders"].keys()) == {"get", "post"}
    assert set(paths["/executions"].keys()) == {"get"}
