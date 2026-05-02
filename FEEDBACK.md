# Developer Experience Feedback - CactusNetwork

This document provides technical feedback regarding specific integration challenges encountered while building with the Uniswap API.

---

## 1. Calldata Compatibility with Custom Smart Contracts
* **Issue:** The `methodParameters.calldata` returned by the `/quote` endpoint is strictly optimized for the `UniversalRouter`. For our custom `SettlementRouter`, the lack of lightweight, on-chain tools to decode Universal Router commands made it difficult to verify trade paths and fee tiers without importing heavy dependencies. Consequently, our backend had to discard the API-provided calldata and manually reconstruct standard V3 calldata.
* **Suggestion:** Provide an optional parameter in the API request (e.g., `routerPreference: "V3Router"`) that allows developers to receive standard V3/V4 calldata. This would lower the barrier for integrating the API with non-standard settlement layers.

## 2. LLM-Optimized API Structures for AI Agents
* **Issue:** When using LLM agents (such as Grok) to generate `/quote` requests, the models frequently encounter formatting errors with complex nested JSON and specific Enum values (e.g., the hierarchical requirements for `tokenInChainId`). Current API documentation is primarily designed for human developers and lacks schemas specifically optimized for machine consumption.
* **Suggestion:** Publish a flattened OpenAPI schema specifically optimized for LLMs. A dedicated endpoint that accepts a simplified JSON structure and returns standardized route details would significantly improve the success rate and efficiency of integrating AI agents with the Uniswap ecosystem.

## 3. Cloudflare Blocking for Direct Python API Calls
* **Issue:** Directly calling the Uniswap API from Python (e.g., `requests`/`httpx`) can intermittently be blocked by Cloudflare. This makes headless/agent integrations flaky even when the request payload is valid.
* **Suggestion:** Provide an officially supported server-to-server access pattern (or guidance) that is resilient to Cloudflare challenges, such as documented required headers (e.g., a stable `User-Agent`), recommended retry/backoff strategy, and/or a dedicated API key mode that avoids bot mitigation false positives.