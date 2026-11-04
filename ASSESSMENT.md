# DashMinimix source assessment

Review date: 2026-11-04

This assessment is based on the checked-in source in [DashMinimix](https://github.com/techrote/DashMinimix), rather than treating its README as a complete specification. HWiNFOterm was created independently in an initially empty target repository. No DashMinimix source files or implementation code were copied.

## What the source actually contains

| Area | Evidence in the repository | Assessment |
| --- | --- | --- |
| HWiNFO input | [`hwinfo.py`](https://github.com/techrote/DashMinimix/blob/main/src/dashminimix/hwinfo.py) opens `Global\\HWiNFO_SENS_SM2` read-only while holding `Global\\HWiNFO_SM2_MUTEX`, then parses SM2 headers and readings. | There is a concrete HWiNFO adapter. Its format parsing is implementation evidence, not a vendor guarantee. |
| Telemetry and processing | [`core.py`](https://github.com/techrote/DashMinimix/blob/main/src/dashminimix/core.py) validates inputs, processing graphs and streams; it implements history, quality, watches, reads, summaries, selections, pagination, and declarative transforms. | The project is a general telemetry hub, not only a hardware dashboard. The processing path is large and needs its own review. |
| Inputs | [`adapters.py`](https://github.com/techrote/DashMinimix/blob/main/src/dashminimix/adapters.py) provides HWiNFO and HTTP polling; agent and simulated inputs are handled elsewhere in the hub. | External data ingestion is part of the source's feature set. |
| Local service | [`api.py`](https://github.com/techrote/DashMinimix/blob/main/src/dashminimix/api.py) serves a bearer-token authenticated loopback API and Server-Sent Events. [`mcp.py`](https://github.com/techrote/DashMinimix/blob/main/src/dashminimix/mcp.py) exposes hub operations over MCP stdio. | These are integration surfaces separate from its visual interface. |
| Desktop UI | [`desktop.py`](https://github.com/techrote/DashMinimix/blob/main/src/dashminimix/desktop.py) implements a Qt dashboard, tray icon, settings, layout, and sensor rows. | It is not a terminal interface and is not reused here. |
| HWiNFO test evidence | The repository has parser tests built from synthetic mapping bytes. | Those fixtures exercise parsing assumptions; they do not demonstrate a live HWiNFO session. |

The repository description advertises ESP-NOW input and PlasmaTerm output. In the reviewed implementation, accepted input types are `agent`, `simulated`, `http`, and `hwinfo`; non-empty output configuration is rejected with a message that PlasmaTerm output is deferred. I found no ESP-NOW adapter in the checked-in source reviewed, so those repository-description claims are not counted as implemented features.

## HWiNFOterm coverage

HWiNFOterm implements a read-only SM2 version 2 reader, bounded sensor history, a searchable sensor catalog, pinning, threshold watches, dashboard layout controls, one-shot text/JSON reads, and a Windows Terminal TUI with semantic themes and palette editing. The reader distinguishes packed and four-byte-aligned value prefixes, supports the known extended UTF-8 row sizes, and rejects unsupported or ambiguous layouts. It reports missing, stale, unsupported, or malformed source data instead of substituting demo values. Demo readings require the explicit `--demo` flag.

The current implementation does not include DashMinimix's generic input and pipeline engine, HTTP or agent inputs, persistent telemetry database, service API/SSE, MCP gateway, or Qt tray app. Those capabilities are outside this HWiNFO TUI's current scope and would need a separate design and security review before being added. HWiNFOterm's TUI settings and watch state are local JSON; its telemetry history is in memory.

The SM2 reader has not been validated against a live HWiNFO mapping in this work. A [separate compatibility report](https://github.com/oliMarler/ATLAS-HWINFO-Kafka-Stream/blob/main/MANUAL.md) documents packed and aligned value prefixes in HWiNFO 8.x, which informed the format checks; it is third-party evidence, not an official schema. HWiNFOterm recognizes version 2 and rejects other versions rather than attempting a guessed layout.

Verification performed on 2026-11-04: `compileall` completed, all 8 standard-library unit tests passed, and the interactive demo accepted `Q`, exited successfully, and restored the terminal. A live scan was attempted while HWiNFO64 was running in the same Windows session, but Windows returned `ERROR_FILE_NOT_FOUND` for both the expected mutex and mapping. The live reader therefore remains unverified until HWiNFO publishes those objects.
