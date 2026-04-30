from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from scripts import api_server
from scripts import orchestrator_server


VALID_ACCOUNT_LEVELS = {"free", "plus", "max", "admin"}


def set_account_level(account_name: str, level: str) -> dict[str, Any]:
    """
    後台調整帳號等級。

    輸入：
    - `account_name`：要調整的帳號名稱。
    - `level`：新的帳號等級，只能是 `free`、`plus`、`max`、`admin`。

    輸出：
    - 回傳調整後的帳號公開資訊。

    副作用：
    - 更新 `accounts.db.accounts.account_level`。
    - 不會更新既有訂單的 `account_level_snapshot`，因為訂單快照代表下單當下等級。
    """
    normalized_level = level.strip()
    if normalized_level not in VALID_ACCOUNT_LEVELS:
        raise ValueError("account level 必須是 free、plus、max 或 admin")

    api_server._init_databases()
    with sqlite3.connect(api_server.ACCOUNTS_DB) as conn:
        cursor = conn.execute(
            "UPDATE accounts SET account_level = ? WHERE account_name = ?",
            (normalized_level, account_name),
        )
        conn.commit()
    if cursor.rowcount != 1:
        raise ValueError(f"account_name={account_name} 不存在")
    return get_account(account_name)


def set_account_day(account_name: str, day: int) -> dict[str, Any]:
    """
    後台調整帳號 day 欄位。

    輸入：
    - `account_name`：要調整的帳號名稱。
    - `day`：新的 day 數值，必須大於等於 0。

    輸出：
    - 回傳調整後的帳號公開資訊。

    副作用：
    - 更新 `accounts.db.accounts.day`。
    """
    normalized_day = int(day)
    if normalized_day < 0:
        raise ValueError("day 必須大於等於 0")

    api_server._init_databases()
    with sqlite3.connect(api_server.ACCOUNTS_DB) as conn:
        cursor = conn.execute(
            "UPDATE accounts SET day = ? WHERE account_name = ?",
            (normalized_day, account_name),
        )
        conn.commit()
    if cursor.rowcount != 1:
        raise ValueError(f"account_name={account_name} 不存在")
    return get_account(account_name)


def get_account(account_name: str) -> dict[str, Any]:
    """
    讀取單一帳號公開資訊。

    輸入：
    - `account_name`：帳號名稱。

    輸出：
    - 回傳帳號資訊，不包含 `password_hash` 與 `salt`。
    """
    api_server._init_databases()
    with sqlite3.connect(api_server.ACCOUNTS_DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT account_name, public_key, account_level, day, created_at
            FROM accounts
            WHERE account_name = ?
            """,
            (account_name,),
        ).fetchone()
    if row is None:
        raise ValueError(f"account_name={account_name} 不存在")
    return _account_row_to_dict(row)


def list_accounts(level: str | None = None) -> list[dict[str, Any]]:
    """
    列出帳號公開資訊。

    輸入：
    - `level`：可選帳號等級篩選。

    輸出：
    - 回傳帳號資訊 list，不包含 `password_hash` 與 `salt`。
    """
    api_server._init_databases()
    params: list[Any] = []
    where_sql = ""
    if level:
        if level not in VALID_ACCOUNT_LEVELS:
            raise ValueError("level 必須是 free、plus、max 或 admin")
        where_sql = "WHERE account_level = ?"
        params.append(level)

    with sqlite3.connect(api_server.ACCOUNTS_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT account_name, public_key, account_level, day, created_at
            FROM accounts
            {where_sql}
            ORDER BY created_at ASC, account_name ASC
            """,
            params,
        ).fetchall()
    return [_account_row_to_dict(row) for row in rows]


def list_orders(account_name: str | None = None, status: str | None = None, limit: int = 100) -> dict[str, Any]:
    """
    查詢買單與賣單。

    輸入：
    - `account_name`：可選帳號篩選。
    - `status`：可選訂單狀態篩選。
    - `limit`：買單與賣單各自最多回傳幾筆。

    輸出：
    - 回傳 `buyOrders` 與 `sellOrders`。
    """
    api_server._init_databases()
    orchestrator_server._ensure_databases()
    safe_limit = max(1, int(limit))
    buy_where, buy_params = _build_order_where(account_name, status)
    sell_where, sell_params = _build_order_where(account_name, status)

    with sqlite3.connect(api_server.BUY_ORDERS_DB) as conn:
        conn.row_factory = sqlite3.Row
        buy_rows = conn.execute(
            f"""
            SELECT *
            FROM buy_orders
            {buy_where}
            ORDER BY datetime(created_at) ASC, id ASC
            LIMIT ?
            """,
            [*buy_params, safe_limit],
        ).fetchall()

    with sqlite3.connect(api_server.SELL_ORDERS_DB) as conn:
        conn.row_factory = sqlite3.Row
        sell_rows = conn.execute(
            f"""
            SELECT *
            FROM sell_orders
            {sell_where}
            ORDER BY datetime(queue_at) ASC, id ASC
            LIMIT ?
            """,
            [*sell_params, safe_limit],
        ).fetchall()

    return {
        "buyOrders": [_buy_order_row_to_dict(row) for row in buy_rows],
        "sellOrders": [_sell_order_row_to_dict(row) for row in sell_rows],
    }


def get_order_book_snapshot(limit_per_side: int = 20) -> dict[str, Any]:
    """
    取得目前訂單簿與中控隊列摘要。

    輸入：
    - `limit_per_side`：買單與賣單各自最多附帶幾筆 pending 明細。

    輸出：
    - 回傳隊列數量、狀態分布與 pending 訂單樣本。
    """
    api_server._init_databases()
    orchestrator_server._ensure_databases()
    safe_limit = max(1, int(limit_per_side))
    return {
        "queueStatus": orchestrator_server.get_queue_status(),
        "buyStatusCounts": _count_by_status(api_server.BUY_ORDERS_DB, "buy_orders"),
        "sellStatusCounts": _count_by_status(api_server.SELL_ORDERS_DB, "sell_orders"),
        "pendingSample": list_orders(status="pending", limit=safe_limit),
    }


def get_decision(task_id: str) -> dict[str, Any]:
    """
    讀取一筆 decisions 紀錄。

    輸入：
    - `task_id`：中控派給 agents 的 task id。

    輸出：
    - 回傳 task、agent decision、apply result 等可回放資訊。
    """
    orchestrator_server._ensure_databases()
    with sqlite3.connect(orchestrator_server.DECISIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM decisions WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"task_id={task_id} 不存在")
    return _decision_row_to_dict(row)


def get_execution(execution_id: str) -> dict[str, Any]:
    """
    讀取一筆 execution 提案或確認紀錄。

    輸入：
    - `execution_id`：execution id。

    輸出：
    - 回傳 proposal、payload、confirmation 與 apply result。
    """
    orchestrator_server._ensure_databases()
    with sqlite3.connect(orchestrator_server.EXECUTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM executions WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"execution_id={execution_id} 不存在")
    return _execution_row_to_dict(row)


def run_cli() -> None:
    """
    後台工具命令列入口。

    輸入：
    - 子命令與參數，例如 `account`、`set-level`、`orders`、`snapshot`。

    輸出：
    - 將查詢或更新結果以 JSON 印到 stdout。
    """
    parser = argparse.ArgumentParser(description="CactusNetwork admin tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    account_parser = subparsers.add_parser("account")
    account_parser.add_argument("account_name")

    accounts_parser = subparsers.add_parser("accounts")
    accounts_parser.add_argument("--level")

    set_level_parser = subparsers.add_parser("set-level")
    set_level_parser.add_argument("account_name")
    set_level_parser.add_argument("level")

    set_day_parser = subparsers.add_parser("set-day")
    set_day_parser.add_argument("account_name")
    set_day_parser.add_argument("day", type=int)

    orders_parser = subparsers.add_parser("orders")
    orders_parser.add_argument("--account-name")
    orders_parser.add_argument("--status")
    orders_parser.add_argument("--limit", type=int, default=100)

    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--limit-per-side", type=int, default=20)

    decision_parser = subparsers.add_parser("decision")
    decision_parser.add_argument("task_id")

    execution_parser = subparsers.add_parser("execution")
    execution_parser.add_argument("execution_id")

    args = parser.parse_args()
    if args.command == "account":
        result = get_account(args.account_name)
    elif args.command == "accounts":
        result = list_accounts(level=args.level)
    elif args.command == "set-level":
        result = set_account_level(args.account_name, args.level)
    elif args.command == "set-day":
        result = set_account_day(args.account_name, args.day)
    elif args.command == "orders":
        result = list_orders(account_name=args.account_name, status=args.status, limit=args.limit)
    elif args.command == "snapshot":
        result = get_order_book_snapshot(limit_per_side=args.limit_per_side)
    elif args.command == "decision":
        result = get_decision(args.task_id)
    elif args.command == "execution":
        result = get_execution(args.execution_id)
    else:
        raise ValueError(f"未知 command：{args.command}")

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def _build_order_where(account_name: str | None, status: str | None) -> tuple[str, list[Any]]:
    """
    建立訂單查詢 WHERE 條件。

    輸入：
    - `account_name`：可選帳號名稱。
    - `status`：可選狀態。

    輸出：
    - 回傳 `(where_sql, params)`。
    """
    filters: list[str] = []
    params: list[Any] = []
    if account_name:
        filters.append("account_name = ?")
        params.append(account_name)
    if status:
        filters.append("status = ?")
        params.append(status)
    if not filters:
        return "", []
    return "WHERE " + " AND ".join(filters), params


def _account_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """
    將 accounts row 轉成公開 dict。

    輸入：
    - `row`：SQLite Row。

    輸出：
    - 回傳不含密碼 hash/salt 的帳號資訊。
    """
    return {
        "accountName": row["account_name"],
        "publicKey": row["public_key"],
        "accountLevel": row["account_level"],
        "day": row["day"],
        "createdAt": row["created_at"],
    }


def _buy_order_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """
    將買單 row 轉成後台查詢 dict。

    輸入：
    - `row`：SQLite Row。

    輸出：
    - 回傳買單資訊。
    """
    return {
        "id": row["id"],
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
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "operationNote": row["operation_note"],
        "intentJson": _json_or_none(row["intent_json"]),
        "hasSignature": bool(row["signature"]),
    }


def _sell_order_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """
    將賣單 row 轉成後台查詢 dict。

    輸入：
    - `row`：SQLite Row。

    輸出：
    - 回傳賣單資訊。
    """
    return {
        "id": row["id"],
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
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "operationNote": row["operation_note"],
        "queueAt": row["queue_at"],
        "intentJson": _json_or_none(row["intent_json"]),
        "hasSignature": bool(row["signature"]),
    }


def _decision_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """
    將 decisions row 轉成可回放 dict。

    輸入：
    - `row`：SQLite Row。

    輸出：
    - 回傳 decision 紀錄。
    """
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "sellOrderId": row["sell_order_id"],
        "candidateBuyOrderIds": _json_or_none(row["candidate_buy_order_ids_json"]) or [],
        "task": _json_or_none(row["task_json"]),
        "decisionStatus": row["decision_status"],
        "agentDecision": _json_or_none(row["agent_decision_json"]),
        "applyResult": _json_or_none(row["apply_result_json"]),
        "failureReason": row["failure_reason"],
        "createdAt": row["created_at"],
        "appliedAt": row["applied_at"],
    }


def _execution_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """
    將 executions row 轉成後台查詢 dict。

    輸入：
    - `row`：SQLite Row。

    輸出：
    - 回傳 execution 紀錄。
    """
    return {
        "id": row["id"],
        "executionId": row["execution_id"],
        "taskId": row["task_id"],
        "sellOrderId": row["sell_order_id"],
        "proposal": _json_or_none(row["proposal_json"]),
        "executionPayload": _json_or_none(row["execution_payload_json"]),
        "status": row["status"],
        "confirmation": _json_or_none(row["confirmation_json"]),
        "applyResult": _json_or_none(row["apply_result_json"]),
        "failureReason": row["failure_reason"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "confirmedAt": row["confirmed_at"],
    }


def _count_by_status(db_path: Path, table_name: str) -> dict[str, int]:
    """
    統計指定訂單表各 status 數量。

    輸入：
    - `db_path`：資料庫路徑。
    - `table_name`：資料表名稱。

    輸出：
    - 回傳 `{status: count}`。
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT status, COUNT(*)
            FROM {table_name}
            GROUP BY status
            ORDER BY status ASC
            """
        ).fetchall()
    return {status: count for status, count in rows}


def _json_or_none(raw_json: str | None) -> Any:
    """
    將 JSON 字串轉回 Python 值。

    輸入：
    - `raw_json`：JSON 字串或 `None`。

    輸出：
    - 可解析時回傳解析結果；空值回傳 `None`。
    """
    if raw_json in (None, ""):
        return None
    return json.loads(raw_json)


if __name__ == "__main__":
    run_cli()
