# Keep the LLM port synchronous

`LLMPort`, the services and the routes stay synchronous; the PydanticAI adapter calls `run_sync` and builds a fresh OpenAI client for every call. Messages go out whole on WhatsApp or SMS and the output guardrails check the full reply before the candidate sees it, so streaming brings nothing, and going async would change every port, service, route, test and the CLI for a concurrency gain the FastAPI threadpool already covers at prototype volume. The fresh client per call avoids the HTTP connection pool being bound to the event loop of another threadpool thread ("Event loop is closed"), at the cost of a new connection per call, negligible next to the LLM latency.

## Considered Options

- **Async end to end** (`async` port methods, `async def` routes, awaited services). Idiomatic for FastAPI and PydanticAI, and the path when volume grows; rejected for now because it is a cross-cutting change while the SQLite repository stays synchronous anyway.
- **Sync on one dedicated background event loop.** Keeps one shared client, but is hard to explain and to debug.
