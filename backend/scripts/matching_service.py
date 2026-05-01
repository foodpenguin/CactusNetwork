from __future__ import annotations

import argparse
import json
import time
from typing import Any

from scripts import agent_runner
from scripts import orchestrator_server


DEFAULT_AGENT = "grok"
DEFAULT_DRAIN_MAX_CYCLES = 100


def run_matching_once(agent: str = DEFAULT_AGENT, candidate_limit: int = 5) -> dict[str, Any]:
    """
    執行一次後端媒合流程。

    輸入：
    - `agent`：使用哪個主腦來源，可為 `main-brain`、`grok`、`simulated`。
    - `candidate_limit`：本輪最多提供幾筆候選買單給主腦。

    輸出：
    - 回傳 timeout refresh 結果與 runner 結果。

    副作用：
    - 會先呼叫 `orchestrator_server.refresh_timeouts()`。
    - 會呼叫 `agent_runner.run_agent_cycle()` 推進一輪決策。
    - 可能寫入 `decisions.db`、`executions.db`、`external_contracts.db`。
    """
    timeout_result = orchestrator_server.refresh_timeouts()
    runner_result = agent_runner.run_agent_cycle(
        _select_agent_decide(agent),
        candidate_limit=candidate_limit,
    )
    return {
        "status": "matching_cycle_completed",
        "agent": agent,
        "timeoutRefresh": timeout_result,
        "runnerResult": runner_result,
    }


def run_matching_loop(
    agent: str = DEFAULT_AGENT,
    candidate_limit: int = 5,
    interval_seconds: int = 15 * 60,
    max_cycles: int | None = None,
) -> list[dict[str, Any]]:
    """
    連續執行後端媒合流程。

    輸入：
    - `agent`：使用哪個主腦來源。
    - `candidate_limit`：每輪最多提供幾筆候選買單。
    - `interval_seconds`：每輪間隔秒數。
    - `max_cycles`：最多執行幾輪；`None` 代表持續執行。

    輸出：
    - 回傳已執行輪次的結果 list。若 `max_cycles=None`，通常不會自然回傳。

    副作用：
    - 週期性呼叫 `run_matching_drain()`，每輪會處理到沒有可派發賣單。
    """
    results: list[dict[str, Any]] = []
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        results.append(run_matching_drain(agent=agent, candidate_limit=candidate_limit))
        cycle += 1
        if max_cycles is not None and cycle >= max_cycles:
            break
        time.sleep(max(1, int(interval_seconds)))
    return results


def run_matching_drain(
    agent: str = DEFAULT_AGENT,
    candidate_limit: int = 5,
    max_cycles: int = DEFAULT_DRAIN_MAX_CYCLES,
) -> dict[str, Any]:
    """
    持續執行媒合，直到沒有可派發的 pending 賣單。

    輸入：
    - `agent`：使用哪個主腦來源，正式預設為 `grok`。
    - `candidate_limit`：每輪最多提供幾筆候選買單。
    - `max_cycles`：安全上限，避免資料異常導致無限循環。

    輸出：
    - 回傳本次 drain 的所有 cycle 結果與停止原因。

    副作用：
    - 每個 cycle 都會呼叫 `run_matching_once()`。
    - 已有 `proposed/dispatched` execution 的賣單會被視為等待鏈上結果，不會重複派發。
    """
    safe_max_cycles = max(1, int(max_cycles))
    cycles: list[dict[str, Any]] = []
    stop_reason = "max_cycles_reached"

    for _ in range(safe_max_cycles):
        result = run_matching_once(agent=agent, candidate_limit=candidate_limit)
        cycles.append(result)
        runner_status = result.get("runnerResult", {}).get("status")
        if runner_status == "no_task":
            stop_reason = "no_processable_sell_order"
            break
        if runner_status == "waiting_for_final_agent_decision":
            stop_reason = "waiting_for_final_agent_decision"
            break

    return {
        "status": "matching_drain_completed",
        "agent": agent,
        "stopReason": stop_reason,
        "cyclesRun": len(cycles),
        "cycles": cycles,
    }


def run_cli() -> None:
    """
    後端媒合服務命令列入口。

    輸入：
    - `once`：跑一輪媒合。
    - `drain`：持續跑到沒有可派發賣單。
    - `loop`：每隔一段時間執行一次 drain。

    輸出：
    - 將結果以 JSON 印到 stdout。
    """
    parser = argparse.ArgumentParser(description="CactusNetwork matching service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    once_parser = subparsers.add_parser("once")
    once_parser.add_argument("--agent", choices=["main-brain", "grok", "simulated"], default=DEFAULT_AGENT)
    once_parser.add_argument("--candidate-limit", type=int, default=5)

    drain_parser = subparsers.add_parser("drain")
    drain_parser.add_argument("--agent", choices=["main-brain", "grok", "simulated"], default=DEFAULT_AGENT)
    drain_parser.add_argument("--candidate-limit", type=int, default=5)
    drain_parser.add_argument("--max-cycles", type=int, default=DEFAULT_DRAIN_MAX_CYCLES)

    loop_parser = subparsers.add_parser("loop")
    loop_parser.add_argument("--agent", choices=["main-brain", "grok", "simulated"], default=DEFAULT_AGENT)
    loop_parser.add_argument("--candidate-limit", type=int, default=5)
    loop_parser.add_argument("--interval-seconds", type=int, default=15 * 60)
    loop_parser.add_argument("--max-cycles", type=int)

    args = parser.parse_args()
    if args.command == "once":
        result = run_matching_once(agent=args.agent, candidate_limit=args.candidate_limit)
    elif args.command == "drain":
        result = run_matching_drain(
            agent=args.agent,
            candidate_limit=args.candidate_limit,
            max_cycles=args.max_cycles,
        )
    elif args.command == "loop":
        result = run_matching_loop(
            agent=args.agent,
            candidate_limit=args.candidate_limit,
            interval_seconds=args.interval_seconds,
            max_cycles=args.max_cycles,
        )
    else:
        raise ValueError(f"未知 command：{args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def _select_agent_decide(agent: str) -> agent_runner.AgentDecide:
    """
    選擇 runner 使用的主腦決策函式。

    輸入：
    - `agent`：`main-brain`、`grok` 或 `simulated`。

    輸出：
    - 回傳可交給 `agent_runner.run_agent_cycle()` 的函式。
    """
    if agent == "grok":
        return agent_runner.grok_agent_decide
    if agent == "simulated":
        return agent_runner.simulated_agent_decide
    if agent == "main-brain":
        from scripts import main_brain

        return main_brain.decide
    raise ValueError("agent 必須是 main-brain、grok 或 simulated")


if __name__ == "__main__":
    run_cli()
