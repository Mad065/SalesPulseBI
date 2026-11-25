---
name: realtime-market-streaming
description: >-
  Defines asynchronous patterns with WebSockets and Server-Sent Events (SSE) for
  streaming live market prices and AI responses without blocking the event loop.
  Use when implementing real-time endpoints, pub/sub broadcasters, or streaming UIs.
---

# Real-Time Market & AI Streaming

Real-time financial feeds and LLM token streams must keep the asyncio event loop responsive, handle slow clients gracefully, and recover cleanly from disconnects.

## Core Rules

1. **Protocol Selection**:
   - **Server-Sent Events (SSE)** (`text/event-stream`): Default for unidirectional server-to-client streams (live price ticks, progress updates, AI/analytics token streaming). Simpler over HTTP/2, supports `Last-Event-ID` resume.
   - **WebSockets**: Use when bidirectional low-latency messaging is required (interactive order-book subscriptions, client-driven channel multiplexing).
2. **Never Block the Event Loop**:
   - NEVER run synchronous CPU-bound work (Pandas/NumPy/DuckDB queries, heavy JSON serialization) or blocking I/O directly inside `async def` stream handlers.
   - Offload blocking work via `await asyncio.to_thread(...)` or a dedicated worker pool.
3. **Backpressure & Coalescing**:
   - Give each subscriber a bounded `asyncio.Queue(maxsize=...)`.
   - For market prices, **coalesce/drop stale ticks** (keep latest price per symbol) when a client falls behind rather than growing memory unboundedly.
   - Throttle UI-bound price broadcasts (e.g., batch updates every 100ms–250ms) instead of pushing every microsecond tick to the DOM.
4. **Heartbeats, Timeouts & Clean Teardown**:
   - Send periodic heartbeat pings/comments (`: keep-alive\n\n` for SSE, `ping` frames for WS) every 15s–30s to detect dead connections and keep proxies open.
   - Always unregister subscribers and cancel background tasks inside `try ... finally` on `WebSocketDisconnect` or `request.is_disconnected()`.
5. **Client Reconnection**:
   - Reconnect with exponential backoff + jitter and cap max delay (e.g., 30s).
   - Resume SSE streams using `Last-Event-ID` where replayable, or trigger a fresh snapshot + delta resync on reconnect.

## Verification Checklist

- Confirm all subscriber queues are bounded (`maxsize > 0`).
- Confirm `finally:` blocks clean up subscriptions and tasks on client disconnect.
- Confirm no synchronous DuckDB/Pandas/requests calls run directly on the event loop in stream handlers.
