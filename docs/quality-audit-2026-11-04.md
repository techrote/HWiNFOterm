# HWiNFOterm quality audit and repairs

Audit date: 4 November 2026. Baseline: [`7e4380dbfe5c0b2d44f3b6e194fb3aa0bafd1d49`](https://github.com/techrote/HWiNFOterm/commit/7e4380dbfe5c0b2d44f3b6e194fb3aa0bafd1d49), tree `e09d3a7afdcdfc4073d3d457767ecf6a9c0434d3`.

## Assessment

The baseline is a useful, compact standard-library implementation with an explicit demo mode, a read-only HWiNFO adapter, local settings, and a substantial terminal workspace. Its eight existing tests passed, but they did not cover several important failure paths. The baseline repository had no CI workflow, Actions runs, releases, or pull requests at audit time.

The most serious findings affect telemetry truthfulness and threshold-watch correctness. Further findings affect configuration recovery, terminal input, Unicode display, navigation, and rendering cost. The repairs below keep the current application architecture and runtime dependency policy. They do not certify compatibility with a live HWiNFO installation.

## Findings and corrections

| Priority | Reproduced baseline problem | Correction and regression coverage |
| --- | --- | --- |
| High | An aligned 320-byte reading with four NaNs could be misidentified as packed, yielding `0.0` and tiny finite statistics. Extended aligned rows had the same problem. | Select only the supported 316/320/460/464-byte row descriptors. Numeric plausibility cannot override a known descriptor. Unknown strides fail explicitly. Tests cover all supported forms, unavailable values, and invalid descriptors. |
| High | `source_error()` was immediately undone by `snapshot()`, restoring connected/good status without a successful read. Removed sensors also retained apparently good values. | Keep provider failures unavailable until a successful read; keep stale readings stale until freshness is established. Mark removed values/statistics missing immediately. Current-value fields explicitly show `OFFLINE`, `STALE`, or missing values even without status chrome or colour. |
| High | Pending watches retained their timer when readings fell below the trigger but stayed above the rearm threshold. Stale/error frames with no new sample skipped timer reset. A recovered interruption between UI frames could also count toward duration. | Apply hysteresis only after activation. Pending watches reset on below-trigger, stale/missing/unavailable, interrupted, or skipped observations. A persistent interruption counter preserves failures across recovery. Durations use a monotonic clock and cannot inherit a pending interval from a previous process. Active state is preserved until fresh rearm evidence. |
| Medium | Corrupt primary configuration ignored a usable backup; saving defaults could overwrite that backup with the corrupt primary. | Recover from the backup with a warning. Preserve good backup contents when the primary is damaged. Both writes use complete, flushed temporary files and atomic replacement. Failure-injection tests check original-file preservation and temporary-file cleanup. |
| Medium | Wrong-type colour modes and duplicated hidden tiles could crash startup. Invalid theme interpolation also crashed; nonfinite theme data prevented subsequent saves. A NaN watch duration became an immediate alarm. | Normalize these inputs defensively. Preserve valid settings and report malformed ones. Reject nonfinite watch durations; require an actual boolean for persisted active state. |
| Medium | Duplicate Cards assignments disappeared on reload. Running the demo first initialized the default live dashboard with synthetic sensor IDs. | Preserve ordered duplicate card slots up to the UI's 256-card limit. Isolate default demo state in a `demo` subdirectory; retain exact explicit `--state-dir` behavior. |
| Medium | Focus-out/unknown CSI sequences swallowed subsequent keys; fragmented mouse reports could turn into command text. | Recognize complete CSI/SS3 boundaries, preserve following characters, and retain bounded partial reports across calls. Decode combined modifiers and supported navigation keys. |
| Medium | Overwriting either half of a wide glyph left inconsistent compositor cells. ASCII fills bypassed ASCII mode; POSIX UTF-8 was decoded one byte at a time. | Clear overlapping wide glyphs, extend diff boundaries across both cells, retain combining marks on wide bases, normalize fills, and decode UTF-8 incrementally. |
| Medium | Failed terminal entry or a closed output stream could prevent mode restoration. EOF could loop without ending the app; Ctrl+C was swallowed by dialogs. | Restore modes when context entry fails or output is closed, stop the input reader before restoration, propagate explicit EOF, and permit control-key exit from overlays/editor state. |
| Medium | Keyboard sensor/watch selection could move offscreen. Watch scrolling changed an offset that rendering ignored; excessive sensor scrolling could show no rows. | Clamp list offsets, bring keyboard selections into view, and slice watches with global indices retained for selection and deletion. |
| Medium | Every fallback-colour style conversion repeated a full nearest-palette search. | Add bounded caches to the pure 256/16-colour conversion functions. The benchmark below verifies identical output bytes. |
| Medium | Plain CLI output accepted provider control characters. Redirected JSON containing extended labels could raise `UnicodeEncodeError` with a cp1252 stream. | Strip terminal controls in plain output; emit lossless ASCII-escaped JSON. Reject nonfinite timing arguments. |
| Medium | Duplicate reading IDs silently overwrote earlier readings; metadata changes could be lost; Python parsing held the provider's mutex. | Reject duplicate identities, compare complete immutable readings, and release native resources after copying the coherent bytes, before Python parsing. Distinguish `WAIT_FAILED` from timeout. |

Implementation: [`hwinfo.py`](../src/hwinfo_term/hwinfo.py), [`model.py`](../src/hwinfo_term/model.py), [`config.py`](../src/hwinfo_term/config.py), [`cli.py`](../src/hwinfo_term/cli.py), [`terminal.py`](../src/hwinfo_term/terminal.py), [`theme.py`](../src/hwinfo_term/theme.py), and [`tui.py`](../src/hwinfo_term/tui.py).

## Executed validation

The combined suite contains **95 tests**. On the Linux audit host with Python 3.12.14, **90 passed and five native Windows tests were explicitly skipped**. The original eight tests remain passing. Bytecode compilation and `git diff --check` passed. A wheel was built successfully without adding runtime dependencies.

Tests cover parser and handle lifecycle failures, availability and catalog changes, recovered interruptions, continuous watch timing, active hysteresis, config backup failures, CLI encoding, terminal protocol boundaries, Unicode rendering, navigation, and all nine views with both visible and hidden chrome at 40×10, 68×18, and 120×40.

A real Linux pseudo-terminal smoke run exercised demo provenance, Cards navigation, F2, the command palette, and Ctrl+C while the overlay was open. It exited with code 0, emitted the alternate-screen restoration sequence, and restored the terminal's original termios state. This is Linux terminal execution, not Windows Terminal acceptance.

The [Quality workflow](../.github/workflows/quality.yml) installs the package, runs tests, compiles source, and invokes the installed CLI on Windows and Linux with Python 3.11 and 3.14. It uses read-only repository permissions, pinned action commits, and cancellation of superseded runs. Hosted results belong to the exact commit shown in the PR's checks; the local results above do not stand in for those hosted results.

The five Windows integration tests use unique `Local\\HWiNFOterm-test-…` mappings/mutexes, exercise real Win32 APIs, and check ownership from a second thread. They cover successful read-only copies, missing mappings, invalid bounds, parser failures, contention, and recovery. They do not impersonate a live HWiNFO mapping or require HWiNFO to be installed.

## Fallback-colour measurement

Synthetic workload: render a 120×40 canvas with a different LUT-selected foreground on each cell, using the same compositor and palette lookup before and after caching. Timings use Python 3.12.14 on Linux x86-64. Uncached figures are medians of three runs; warm cached figures are medians of five. The uncached comparison calls the original pure nearest-colour function through its cache wrapper's `__wrapped__` attribute.

| Mode | Uncached median | Cached, first frame | Cached, warm median |
| --- | ---: | ---: | ---: |
| 256 colours | 1,829.97 ms | 39.00 ms | 14.06 ms |
| 16 colours | 128.62 ms | 16.45 ms | 15.40 ms |

All compared output bytes were identical. Output SHA-256 values were `9c7f0808bdf2109657f35dd93437739e6d09fae5c10eb08199479b2d04963408` for 256 colours and `d79a5d8f15bbd9dcdd59dc961c710ee479c9f4bb26cf7d1c789c51b678bc819c` for 16 colours. This isolates the colour-conversion bottleneck; it is not a whole-application FPS or hardware-monitoring benchmark.

## Remaining acceptance and limitations

- **Live HWiNFO:** compare `--scan`, `--once --json`, labels, units, min/max/average, and current values against a real HWiNFO Sensors window. Record HWiNFO version and observed header/row descriptors. Test stopping/restarting the provider and Shared Memory Support becoming unavailable. Unsupported future strides fail visibly rather than being guessed.
- **Windows Terminal interaction:** check click/drag/reorder, wheel scrolling, focus-loss cancellation, fast Escape followed by a command, F2, control-key exit from an overlay, resizing, bottom-row wrapping, and restoration to the shell. Mocked mode tests and native mapping tests do not exercise this interaction path.
- **Known Unicode-input ambiguity:** the Windows `msvcrt.getwch()` path uses U+00E0 as an extended-key prefix. A mocked Windows stream containing literal `àM` becomes Right-arrow input. A successful VT mode request is insufficient evidence to reinterpret that prefix: Microsoft's [`_getwch` contract](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/getch-getwch?view=msvc-170) still specifies legacy prefixes, while [`SetConsoleMode`](https://learn.microsoft.com/en-us/windows/console/setconsolemode) describes VT retrieval through `ReadFile`/`ReadConsole`. Resolve this with native input qualification before changing the backend. General grapheme-cluster/emoji rendering is also not established by the wide-character fixes.
- **History retention:** the time window and 1,800-point per-sensor cap both apply. At one fresh sample every 0.25 seconds, the point cap is roughly 7.5 minutes. Retired sensor metadata/history remains cached for pinned-card continuity; catalog churn is not globally bounded by this patch.
- **Maintainability and distribution:** `tui.py` remains a large module; splitting state, watch evaluation, and layout is a future maintenance task rather than part of these repairs. The baseline contains no license file or published release, and this patch does not choose a license or publish a release.

These are implementation repairs with automated regression coverage. Release readiness still requires the live Windows/HWiNFO acceptance above.
