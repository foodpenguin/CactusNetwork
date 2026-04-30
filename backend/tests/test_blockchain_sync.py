import json
import sqlite3
from pathlib import Path

import pytest

from scripts import blockchain_sync


@pytest.fixture(autouse=True)
def isolated_onchain_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每個鏈上同步測試都使用暫存 DB，避免影響正式 onchain_state.db。"""
    data_dir = tmp_path / "databases"
    monkeypatch.setattr(blockchain_sync, "DATA_DIR", data_dir)
    monkeypatch.setattr(blockchain_sync, "ONCHAIN_STATE_DB", data_dir / "onchain_state.db")
    monkeypatch.setattr(blockchain_sync, "EXTERNAL_CONTRACTS_DB", data_dir / "external_contracts.db")
    blockchain_sync._init_database()


def test_call_data_encodes_balance_of_address() -> None:
    """測試 ERC20 balanceOf(address) calldata 編碼。"""
    owner = "0x00000000000000000000000000000000000000ab"
    data = blockchain_sync._call_data(
        blockchain_sync.BALANCE_OF_SELECTOR,
        [blockchain_sync._encode_address(owner)],
    )

    assert data == (
        "0x70a08231"
        "00000000000000000000000000000000000000000000000000000000000000ab"
    )


def test_call_data_encodes_filled_amount_in_bytes32() -> None:
    """測試 SettlementRouter filledAmountIn(bytes32) calldata 編碼。"""
    intent_hash = "0x" + "12" * 32
    data = blockchain_sync._call_data(
        blockchain_sync.ROUTER_FILLED_AMOUNT_IN_SELECTOR,
        [blockchain_sync._encode_bytes32(intent_hash)],
    )

    assert data == "0xec980a9c" + "12" * 32


def test_build_v3_exact_input_single_calldata() -> None:
    """測試後端可自行組 Uniswap V3 exactInputSingle calldata。"""
    calldata = blockchain_sync._build_v3_exact_input_single_calldata(
        token_in="0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
        token_out="0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14",
        fee=100,
        recipient="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        amount_in="100000000",
        amount_out_minimum="11835912129038999",
        sqrt_price_limit_x96=0,
    )

    assert calldata.startswith("0x04e45aaf")
    assert "0000000000000000000000001c7d4b196cb0c7b01d743fbc6116a902379c7238" in calldata.lower()
    assert "000000000000000000000000fff9976782d46cc05630d1f6ebab18b2324d6b14" in calldata.lower()
    assert "0000000000000000000000000000000000000000000000000000000000000064" in calldata.lower()
    assert calldata.endswith("0" * 64)


def test_simulate_router_call_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 router calldata 可用 eth_call 模擬成功。"""
    calls = []

    def fake_json_rpc(method, params, rpc_url=None):
        calls.append((method, params, rpc_url))
        return "0x"

    monkeypatch.setattr(blockchain_sync, "_json_rpc", fake_json_rpc)

    result = blockchain_sync.simulate_router_call(
        calldata="0x04e45aaf",
        router_address="0x3bFA4769FB09eefC5a80d6E87c3B9C650f7Ae48E",
        caller="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        rpc_url="http://mock-rpc",
    )

    assert result["success"] is True
    assert result["result"] == "0x"
    assert calls[0][0] == "eth_call"
    assert calls[0][1][0]["from"] == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def test_simulate_router_call_decodes_revert_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 eth_call revert 時可解析 Solidity Error(string)。"""
    encoded_stf = (
        "0x08c379a0"
        "0000000000000000000000000000000000000000000000000000000000000020"
        "0000000000000000000000000000000000000000000000000000000000000003"
        "5354460000000000000000000000000000000000000000000000000000000000"
    )

    def fake_json_rpc(method, params, rpc_url=None):
        raise RuntimeError(f"RPC 回傳錯誤: {{\"data\": \"{encoded_stf}\", \"message\": \"execution reverted: STF\"}}")

    monkeypatch.setattr(blockchain_sync, "_json_rpc", fake_json_rpc)

    result = blockchain_sync.simulate_router_call(
        calldata="0x04e45aaf",
        router_address="0x3bFA4769FB09eefC5a80d6E87c3B9C650f7Ae48E",
        caller="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    )

    assert result["success"] is False
    assert result["revertReason"] == "STF"


def test_read_erc20_balance_and_allowance(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試可讀取 ERC20 balanceOf 與 allowance。"""
    responses = iter(
        [
            "0x" + hex(1661915267)[2:].rjust(64, "0"),
            "0x" + hex(0)[2:].rjust(64, "0"),
        ]
    )

    def fake_eth_call(contract_address, data, rpc_url=None):
        assert contract_address == "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"
        return next(responses)

    monkeypatch.setattr(blockchain_sync, "_eth_call", fake_eth_call)

    result = blockchain_sync.read_erc20_balance_and_allowance(
        token_address="0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
        owner="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        spender="0x3bFA4769FB09eefC5a80d6E87c3B9C650f7Ae48E",
    )

    assert result["balanceRaw"] == "1661915267"
    assert result["allowanceRaw"] == "0"


def test_update_onchain_state_inserts_first_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試第一次同步會建立 latest state 與一筆 change history。"""
    target = {"intentId": "intent-1", "amountIn": "10"}

    def fake_read_uniswap_target(target_data, rpc_url=None):
        return {
            "targetId": blockchain_sync.get_target_id(target_data),
            "chainName": "sp-test",
            "checkedAt": "2026-04-30T00:00:00+00:00",
            "source": "uniswap_api",
            "reads": {"vaultBalance": "10", "filledAmountIn": "0", "remainingAmountIn": "10"},
            "skipped": [],
            "errors": [],
            "isValid": True,
        }

    monkeypatch.setattr(blockchain_sync, "_read_uniswap_target", fake_read_uniswap_target)

    result = blockchain_sync.update_onchain_state([target], rpc_url="http://mock-rpc")

    assert result["checked"] == 1
    assert result["changed"] == 1
    assert result["unchanged"] == 0
    with sqlite3.connect(blockchain_sync.ONCHAIN_STATE_DB) as conn:
        tracked = conn.execute("SELECT COUNT(*) FROM onchain_states").fetchone()[0]
        changes = conn.execute("SELECT COUNT(*) FROM onchain_state_changes").fetchone()[0]
    assert tracked == 1
    assert changes == 1


def test_update_onchain_state_skips_history_when_snapshot_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試同樣鏈上狀態重複同步時，只更新 checked 時間，不新增差異紀錄。"""
    target = {"intentId": "intent-1", "amountIn": "10"}

    def fake_read_uniswap_target(target_data, rpc_url=None):
        return {
            "targetId": blockchain_sync.get_target_id(target_data),
            "chainName": "sp-test",
            "checkedAt": "2026-04-30T00:00:00+00:00",
            "source": "uniswap_api",
            "reads": {"vaultBalance": "10", "filledAmountIn": "0", "remainingAmountIn": "10"},
            "skipped": [],
            "errors": [],
            "isValid": True,
        }

    monkeypatch.setattr(blockchain_sync, "_read_uniswap_target", fake_read_uniswap_target)

    first = blockchain_sync.update_onchain_state([target])
    second = blockchain_sync.update_onchain_state([target])

    assert first["changed"] == 1
    assert second["changed"] == 0
    assert second["unchanged"] == 1
    with sqlite3.connect(blockchain_sync.ONCHAIN_STATE_DB) as conn:
        changes = conn.execute("SELECT COUNT(*) FROM onchain_state_changes").fetchone()[0]
    assert changes == 1


def test_update_onchain_state_records_new_history_when_snapshot_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試鏈上狀態改變時，會新增一筆差異歷史。"""
    target = {"intentId": "intent-1", "amountIn": "10"}
    filled_amount = {"value": "0"}

    def fake_read_uniswap_target(target_data, rpc_url=None):
        remaining = str(10 - int(filled_amount["value"]))
        return {
            "targetId": blockchain_sync.get_target_id(target_data),
            "chainName": "sp-test",
            "checkedAt": "2026-04-30T00:00:00+00:00",
            "source": "uniswap_api",
            "reads": {
                "vaultBalance": "10",
                "filledAmountIn": filled_amount["value"],
                "remainingAmountIn": remaining,
            },
            "skipped": [],
            "errors": [],
            "isValid": int(remaining) > 0,
        }

    monkeypatch.setattr(blockchain_sync, "_read_uniswap_target", fake_read_uniswap_target)

    blockchain_sync.update_onchain_state([target])
    filled_amount["value"] = "4"
    result = blockchain_sync.update_onchain_state([target])

    assert result["changed"] == 1
    with sqlite3.connect(blockchain_sync.ONCHAIN_STATE_DB) as conn:
        conn.row_factory = sqlite3.Row
        latest = conn.execute("SELECT latest_state_json FROM onchain_states WHERE target_id = ?", ("intent-1",)).fetchone()
        changes = conn.execute("SELECT COUNT(*) FROM onchain_state_changes").fetchone()[0]
    latest_state = json.loads(latest["latest_state_json"])
    assert latest_state["reads"]["filledAmountIn"] == "4"
    assert latest_state["reads"]["remainingAmountIn"] == "6"
    assert changes == 2


def test_read_uniswap_target_reports_missing_quote_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試缺少 Uniswap quote 必要欄位時，讀取結果會清楚標示錯誤。"""
    monkeypatch.setenv("UNISWAP_API_KEY", "test-key")
    state = blockchain_sync._read_uniswap_target({"intentId": "intent-1"})

    assert state["targetId"] == "intent-1"
    assert state["source"] == "uniswap_api"
    assert state["reads"] == {}
    assert state["isValid"] is False
    assert any("tokenIn" in item for item in state["errors"])


def test_read_uniswap_target_fetches_quote_and_builds_v3_calldata(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試 Uniswap API quote 後，後端自行建立 V3 Calldata。"""
    monkeypatch.setenv("UNISWAP_API_KEY", "test-key")
    calls = []

    def fake_uniswap_api_post(path, payload):
        calls.append((path, payload))
        if path == "/check_approval":
            return {"approvalRequired": False}
        if path == "/quote":
            return {
                "requestId": "quote-request",
                "routing": "CLASSIC",
                "permitData": None,
                "quote": {
                    "input": {"token": payload["tokenIn"], "amount": payload["amount"]},
                    "output": {"token": payload["tokenOut"], "amount": "3000000000"},
                    "quoteId": "quote-1",
                    "gasFee": "100",
                    "gasFeeUSD": "0.01",
                    "routeString": "V3",
                    "priceImpact": 0.1,
                },
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(blockchain_sync, "_uniswap_api_post", fake_uniswap_api_post)

    state = blockchain_sync._read_uniswap_target(
        {
            "intentId": "quote-1",
            "tokenIn": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "tokenOut": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "amount": "1000000000000000000",
            "swapper": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
            "slippageTolerance": 0.5,
            "protocols": ["V2", "V3", "V4"],
            "fee": 100,
            "priceLimit": 0,
        }
    )

    assert [call[0] for call in calls] == ["/check_approval", "/quote"]
    assert calls[0][1]["chainId"] == 11155111
    assert calls[1][1]["tokenInChainId"] == "11155111"
    assert calls[1][1]["tokenOutChainId"] == "11155111"
    assert calls[1][1]["protocols"] == ["V3"]
    assert calls[1][1]["routingPreference"] == "BEST_PRICE"
    assert state["source"] == "uniswap_api"
    assert state["reads"]["routing"] == "CLASSIC"
    assert state["reads"]["amountOut"] == "3000000000"
    assert state["reads"]["Calldata"].startswith("0x04e45aaf")
    assert state["reads"]["v3Fee"] == "100"
    assert state["reads"]["sqrtPriceLimitX96"] == "0"
    assert state["isValid"] is True


def test_request_external_contract_data_records_query_snapshot_and_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """測試外部合約資料請求會獨立寫入 external_contracts.db。"""
    target = {"intentId": "external-intent-1", "amountIn": "10"}

    def fake_read_uniswap_target(target_data, rpc_url=None):
        return {
            "targetId": blockchain_sync.get_target_id(target_data),
            "chainName": "sp-test",
            "checkedAt": "2026-04-30T00:00:00+00:00",
            "source": "uniswap_api",
            "intent": {"amountIn": target_data["amountIn"]},
            "reads": {"vaultBalance": "10", "filledAmountIn": "2", "remainingAmountIn": "8"},
            "skipped": [],
            "errors": [],
            "isValid": True,
        }

    monkeypatch.setattr(blockchain_sync, "_read_uniswap_target", fake_read_uniswap_target)

    result = blockchain_sync.request_external_contract_data(
        {"id": 7, "asset": "WETH"},
        {
            "taskId": "task-1",
            "reason": "本地候選不足",
            "sourceOrderType": "sell",
            "syncTargets": [target],
        },
        rpc_url="http://mock-rpc",
    )
    context = blockchain_sync.get_external_contract_context(result["queryId"])

    assert result["status"] == "completed"
    assert result["checked"] == 1
    assert result["candidates"][0]["targetId"] == "external-intent-1"
    assert result["candidates"][0]["reads"]["remainingAmountIn"] == "8"
    assert context["query"]["source_order_id"] == 7
    assert context["query"]["request_reason"] == "本地候選不足"
    assert context["candidates"][0]["candidate"]["isValid"] is True
    with sqlite3.connect(blockchain_sync.EXTERNAL_CONTRACTS_DB) as conn:
        queries = conn.execute("SELECT COUNT(*) FROM external_contract_queries").fetchone()[0]
        snapshots = conn.execute("SELECT COUNT(*) FROM external_contract_snapshots").fetchone()[0]
        candidates = conn.execute("SELECT COUNT(*) FROM external_contract_candidates").fetchone()[0]
    assert (queries, snapshots, candidates) == (1, 1, 1)
