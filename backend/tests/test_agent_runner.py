import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from scripts import agent_runner
from scripts import api_server
from scripts import blockchain_sync
from scripts import grok_minimal
from scripts import orchestrator_server


def make_grok_test_task() -> dict[str, Any]:
    """建立 Grok adapter 測試用 task。"""
    return {
        "taskId": "task-grok-1",
        "sellOrder": {
            "id": 99,
            "asset": "WETH",
            "remainingAmount": 1,
            "minUnitPriceUsdc": 2900,
        },
        "candidateBuyOrders": [],
    }


@pytest.fixture(autouse=True)
def isolated_runner_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每個 runner 測試都使用暫存 DB，避免影響本機資料。"""
    data_dir = tmp_path / "databases"

    monkeypatch.setattr(api_server, "DATA_DIR", data_dir)
    monkeypatch.setattr(api_server, "ACCOUNTS_DB", data_dir / "accounts.db")
    monkeypatch.setattr(api_server, "BUY_ORDERS_DB", data_dir / "buy_orders.db")
    monkeypatch.setattr(api_server, "SELL_ORDERS_DB", data_dir / "sell_orders.db")

    monkeypatch.setattr(orchestrator_server, "DATA_DIR", data_dir)
    monkeypatch.setattr(orchestrator_server, "BUY_ORDERS_DB", data_dir / "buy_orders.db")
    monkeypatch.setattr(orchestrator_server, "SELL_ORDERS_DB", data_dir / "sell_orders.db")
    monkeypatch.setattr(orchestrator_server, "ORCHESTRATOR_STATE_DB", data_dir / "orchestrator_state.db")
    monkeypatch.setattr(orchestrator_server, "TIMEOUT_ORDERS_DB", data_dir / "timeout_orders.db")
    monkeypatch.setattr(orchestrator_server, "DECISIONS_DB", data_dir / "decisions.db")
    monkeypatch.setattr(orchestrator_server, "EXECUTIONS_DB", data_dir / "executions.db")

    monkeypatch.setattr(blockchain_sync, "DATA_DIR", data_dir)
    monkeypatch.setattr(blockchain_sync, "ONCHAIN_STATE_DB", data_dir / "onchain_state.db")
    monkeypatch.setattr(blockchain_sync, "EXTERNAL_CONTRACTS_DB", data_dir / "external_contracts.db")

    api_server._init_databases()
    orchestrator_server._ensure_databases()
    blockchain_sync._init_database()
    blockchain_sync._init_external_contracts_database()


def insert_sell_order() -> int:
    """插入一筆沒有本地候選買單的 pending 賣單。"""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(orchestrator_server.SELL_ORDERS_DB) as conn:
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
                queue_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("seller", "free", "WETH", 1, 1, 2900, 3, 0.3, "pending", 0, now, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_runner_handles_agent_external_request_then_final_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 runner 可串起中控、外部合約資料請求、再套用 agent 最終決策。"""
    sell_id = insert_sell_order()
    target = {"intentId": "external-intent-1", "amountIn": "10"}

    def fake_read_uniswap_target(target_data, rpc_url=None):
        return {
            "targetId": blockchain_sync.get_target_id(target_data),
            "chainName": "sp-test",
            "checkedAt": "2026-04-30T00:00:00+00:00",
            "source": "uniswap_api",
            "intent": {"amountIn": target_data["amountIn"]},
            "reads": {"vaultBalance": "10", "filledAmountIn": "0", "remainingAmountIn": "10"},
            "skipped": [],
            "errors": [],
            "isValid": True,
        }

    def fake_agent(task: dict[str, Any], external_context: Optional[dict[str, Any]]) -> dict[str, Any]:
        if external_context is None:
            return {
                "taskId": task["taskId"],
                "decisionStatus": "request_external_contract_data",
                "sellOrderId": task["sellOrder"]["id"],
                "reason": "本地沒有候選買單，要求查外部合約",
                "externalQuery": {
                    "sourceOrderType": "sell",
                    "syncTargets": [target],
                },
            }
        assert external_context["candidates"][0]["candidate"]["reads"]["remainingAmountIn"] == "10"
        return {
            "taskId": task["taskId"],
            "decisionStatus": "rejected",
            "sellOrderId": task["sellOrder"]["id"],
            "failureReason": "測試用 agent 查完外部後仍決定不成交",
            }

    monkeypatch.setattr(blockchain_sync, "_read_uniswap_target", fake_read_uniswap_target)

    result = agent_runner.run_agent_cycle(fake_agent)

    assert result["status"] == "completed_after_external_context"
    assert result["externalRequest"]["checked"] == 1
    assert result["applyResult"]["decisionStatus"] == "rejected"

    with sqlite3.connect(orchestrator_server.SELL_ORDERS_DB) as conn:
        sell = conn.execute("SELECT attempts, status FROM sell_orders WHERE id = ?", (sell_id,)).fetchone()
    with sqlite3.connect(blockchain_sync.EXTERNAL_CONTRACTS_DB) as conn:
        query_count = conn.execute("SELECT COUNT(*) FROM external_contract_queries").fetchone()[0]

    assert sell == (1, "pending")
    assert query_count == 1


def test_parse_grok_decision_accepts_fenced_json() -> None:
    """測試 Grok 即使用 markdown code fence 包住 JSON，也能被解析。"""
    decision = agent_runner.parse_grok_decision(
        """
        ```json
        {
          "decisionStatus": "rejected",
          "sellOrderId": 99,
          "failureReason": "測試拒絕"
        }
        ```
        """
    )

    assert decision["decisionStatus"] == "rejected"
    assert decision["failureReason"] == "測試拒絕"


def test_extract_response_text_reads_responses_output_content() -> None:
    """測試 xAI Responses API 的 output content 文字可被取出。"""
    data = {
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "思考摘要"}]},
            {"type": "message", "content": [{"type": "output_text", "text": "{\"ok\": true}"}]},
        ]
    }

    assert grok_minimal.extract_response_text(data) == '{"ok": true}'


def test_load_agent_memory_prioritizes_mainagent_and_combines_specialized_memory(tmp_path: Path) -> None:
    """測試長期記憶會先讀 mainagent，再合併其他專用記憶。"""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "orchestrator.md").write_text("中控記憶", encoding="utf-8")
    (memory_dir / "mainagent.md").write_text("主腦記憶", encoding="utf-8")
    (memory_dir / "output_format.md").write_text("輸出格式記憶", encoding="utf-8")

    memory = grok_minimal.load_agent_memory(memory_dir)

    assert memory.index("mainagent.md") < memory.index("orchestrator.md")
    assert "主腦記憶" in memory
    assert "中控記憶" in memory
    assert "輸出格式記憶" in memory


def test_build_prompt_treats_memory_as_project_operating_context() -> None:
    """測試送給 Grok 的 system prompt 會明確要求使用長期記憶。"""
    messages = grok_minimal.build_prompt(memory="主腦長期記憶", task="本輪任務")

    assert "長期記憶是專案穩定架構" in messages[0]["content"]
    assert "以目前任務中的明確欄位" in messages[0]["content"]
    assert "主腦長期記憶" in messages[1]["content"]
    assert "本輪任務" in messages[2]["content"]


def test_grok_agent_decide_calls_grok_and_normalizes_external_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 Grok adapter 會呼叫 Grok，並補齊 request_external_contract_data 的必要欄位。"""
    task = make_grok_test_task()
    captured: dict[str, str] = {}

    def fake_ask_grok_with_memory(memory: str, task: str) -> str:
        captured["memory"] = memory
        captured["task"] = task
        return """
        {
          "decisionStatus": "request_external_contract_data",
          "reason": "本地沒有候選買單，需查外部合約",
          "externalQuery": {}
        }
        """

    monkeypatch.setattr(agent_runner.grok_minimal, "load_agent_memory", lambda: "測試記憶")
    monkeypatch.setattr(agent_runner.grok_minimal, "ask_grok_with_memory", fake_ask_grok_with_memory)

    decision = agent_runner.grok_agent_decide(task, None)

    assert captured["memory"] == "測試記憶"
    assert "格式化主腦輸出" in captured["task"]
    assert decision["taskId"] == "task-grok-1"
    assert decision["sellOrderId"] == 99
    assert decision["decisionStatus"] == "request_external_contract_data"
    assert decision["externalQuery"]["sourceOrderType"] == "sell"
    assert decision["externalQuery"]["asset"] == "WETH"
    assert decision["externalQuery"]["amount"] == 1
    assert decision["externalQuery"]["minUnitPriceUsdc"] == 2900
    assert decision["externalQuery"]["syncTargets"] == []


def test_grok_external_request_backfills_uniswap_target_from_sell_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 Grok 漏填 syncTargets 時，後端會用賣單 intent 補 Uniswap V3 target。"""
    task = make_grok_test_task()
    task["sellOrder"]["intentJson"] = {
        "user": "0x1111111111111111111111111111111111111111",
        "tokenIn": "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14",
        "tokenOut": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
        "amountIn": "50000000000000",
        "minAmountOut": "50",
        "deadline": 1999999999,
        "salt": "0x" + "11" * 32,
        "allowPartialFill": True,
        "chainId": 11155111,
        "tokenInChainId": 11155111,
        "tokenOutChainId": 11155111,
        "fee": 100,
        "priceLimit": 0,
        "swapper": "0x1111111111111111111111111111111111111111",
        "recipient": "0x1111111111111111111111111111111111111111",
    }

    monkeypatch.setattr(agent_runner.grok_minimal, "load_agent_memory", lambda: "測試記憶")
    monkeypatch.setattr(
        agent_runner.grok_minimal,
        "ask_grok_with_memory",
        lambda memory, task: """
        {
          "decisionStatus": "request_external_contract_data",
          "reason": "本地沒有候選買單，需查外部合約",
          "externalQuery": {"syncTargets": []}
        }
        """,
    )

    decision = agent_runner.grok_agent_decide(task, None)
    targets = decision["externalQuery"]["syncTargets"]

    assert len(targets) == 1
    assert targets[0]["intentId"] == "sell-order-99-uniswap-v3"
    assert targets[0]["tokenIn"] == task["sellOrder"]["intentJson"]["tokenIn"]
    assert targets[0]["tokenOut"] == task["sellOrder"]["intentJson"]["tokenOut"]
    assert targets[0]["amountIn"] == "50000000000000"
    assert targets[0]["protocols"] == ["V3"]
    assert targets[0]["fee"] == 100
    assert targets[0]["priceLimit"] == 0


def test_grok_agent_decide_rejects_invalid_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 Grok 回傳不合法 decisionStatus 時會被擋下。"""
    monkeypatch.setattr(agent_runner.grok_minimal, "load_agent_memory", lambda: "")
    monkeypatch.setattr(
        agent_runner.grok_minimal,
        "ask_grok_with_memory",
        lambda memory, task: '{"decisionStatus": "matched", "sellOrderId": 99}',
    )

    with pytest.raises(ValueError, match="decisionStatus"):
        agent_runner.grok_agent_decide(make_grok_test_task(), None)
