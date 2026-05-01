from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"
MEMORY_DIR = PROJECT_DIR / "memory"
API_URL = "https://api.x.ai/v1/responses"
DEFAULT_MODEL = "grok-4.20-reasoning"


def load_env_file(path: Path = ENV_FILE) -> None:
    """
    讀取 `.env` 並載入環境變數。

    輸入：
    - `path`：`.env` 檔案路徑，預設為專案根目錄 `.env`。

    輸出：
    - 無回傳值。

    副作用：
    - 將 `.env` 中尚未存在於 `os.environ` 的 key/value 寫入目前 process。
    - 不覆蓋已存在的環境變數。
    """
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_api_key() -> str:
    """
    取得 Grok / xAI API key。

    輸入：
    - 無直接參數；從環境變數讀取。

    輸出：
    - 回傳 API key 字串。

    支援的環境變數名稱：
    - `XAI_API_KEY`
    - `GROK_API_KEY`
    - `GROKAPI_KEY`

    錯誤：
    - 找不到 API key 時拋出 `RuntimeError`。
    """
    for key_name in ("XAI_API_KEY", "GROK_API_KEY", "GROKAPI_KEY"):
        api_key = os.getenv(key_name)
        if api_key:
            return api_key
    raise RuntimeError("找不到 API key，請在 .env 放入 XAI_API_KEY=你的_key")


def load_agent_memory(memory_dir: Path = MEMORY_DIR) -> str:
    """
    讀取 agent 記憶資料夾。

    輸入：
    - `memory_dir`：記憶資料夾路徑，預設為專案根目錄 `memory/`。

    輸出：
    - 回傳合併後的 markdown 字串。
    - 若資料夾不存在或沒有內容，回傳空字串。

    規則：
    - `mainagent.md` 會被放在最前面。
    - 其他 `*.md` 依檔名排序後接在後面。
    """
    if not memory_dir.exists():
        return ""

    memory_files = sorted(
        memory_file
        for memory_file in memory_dir.glob("*.md")
        if not memory_file.name.startswith("._")
    )
    main_memory = memory_dir / "mainagent.md"
    if main_memory in memory_files:
        memory_files.remove(main_memory)
        memory_files.insert(0, main_memory)

    sections: list[str] = []
    for memory_file in memory_files:
        content = memory_file.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            continue
        sections.append(f"## 記憶檔案：{memory_file.name}\n\n{content}")

    return "\n\n---\n\n".join(sections)


def build_prompt(memory: str, task: str) -> list[dict[str, str]]:
    """
    組合送給 Grok Responses API 的 input。

    輸入：
    - `memory`：已合併的 agent 記憶文字。
    - `task`：這次要模型處理的目前任務文字。

    輸出：
    - 回傳符合 Responses API `input` 格式的 messages list。

    語意：
    - system 訊息定義模型角色。
    - 第一個 user 訊息放長期記憶。
    - 第二個 user 訊息放目前任務。
    """
    return [
        {
            "role": "system",
            "content": (
                "你是一個使用繁體中文回答的專案 Agent。"
                "你會同時參考長期記憶與目前任務，並輸出可執行、清楚的回應。"
            ),
        },
        {
            "role": "user",
            "content": f"以下是目前可用記憶：\n\n{memory or '（目前沒有記憶）'}",
        },
        {
            "role": "user",
            "content": f"以下是目前任務：\n\n{task}",
        },
    ]


def ask_grok_with_memory(memory: str, task: str) -> str:
    """
    將記憶與目前任務送給 Grok，並取得模型文字輸出。

    輸入：
    - `memory`：agent 記憶文字。
    - `task`：目前任務文字。

    輸出：
    - 回傳 Grok 回應中的 `output_text` 字串。

    副作用：
    - 讀取 `.env`。
    - 對 `https://api.x.ai/v1/responses` 發出 HTTP POST。

    錯誤：
    - API 回傳 HTTP error 時拋出 `RuntimeError`，並包含錯誤 body。
    - 回應內沒有可用 `output_text` 時拋出 `RuntimeError`。
    """
    load_env_file()
    api_key = get_api_key()
    model = os.getenv("XAI_MODEL", DEFAULT_MODEL)

    payload = {
        "model": model,
        "input": build_prompt(memory=memory, task=task),
    }

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Grok API 回傳錯誤 {exc.code}: {error_body}") from exc

    output_text = extract_response_text(data)
    if output_text:
        return output_text

    raise RuntimeError(f"無法從回應中取得 output_text，原始回應：{json.dumps(data, ensure_ascii=False)}")


def extract_response_text(data: dict) -> str:
    """
    從 xAI/OpenAI Responses API 回應中取出文字。

    輸入：
    - `data`：API 回傳 JSON dict。

    輸出：
    - 成功時回傳文字。
    - 找不到文字時回傳空字串。

    支援格式：
    - 舊式頂層 `output_text`。
    - Responses API 的 `output[].content[].text`。
    """
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = data.get("output")
    if not isinstance(output, list):
        return ""

    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks).strip()


def ask_grok(task: str) -> str:
    """
    使用預設記憶資料夾呼叫 Grok。

    輸入：
    - `task`：目前任務文字。

    輸出：
    - 回傳 Grok 的文字回答。

    流程：
    - 讀取 `memory/*.md`。
    - 將記憶與任務一起交給 `ask_grok_with_memory()`。
    """
    memory = load_agent_memory()
    return ask_grok_with_memory(memory=memory, task=task)


def main() -> None:
    """
    Grok 最小命令列入口。

    輸入：
    - CLI 參數：會被合併成目前任務文字。
    - 若 CLI 沒有參數，會用 `input()` 要求使用者輸入任務。

    輸出：
    - 將 Grok 回答印到 stdout。

    副作用：
    - 可能讀取 `.env` 與 `memory/*.md`。
    - 可能呼叫 Grok API。
    """
    task = " ".join(sys.argv[1:]).strip()
    if not task:
        task = input("請輸入目前任務：").strip()
    if not task:
        raise SystemExit("沒有輸入文字，程式結束。")

    print(ask_grok(task))


if __name__ == "__main__":
    main()
