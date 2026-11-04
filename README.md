# HWiNFOterm

A Windows Terminal-first live hardware dashboard for HWiNFO Shared Memory Support. HWiNFOterm reads the SM2 mapping in read-only mode and presents it as a keyboard- and mouse-operable tile workspace.

## Start

Open PowerShell in this folder and install the local package:

```powershell
.\setup.ps1
.\Start-HWiNFOterm.cmd
```

Before starting HWiNFOterm, open HWiNFO's Sensors window and enable **Shared Memory Support**. The TUI starts in live HWiNFO mode. If the mapping is absent, inactive, or stops updating, it reports that state; it never fills the dashboard with synthetic readings unless `--demo` was explicitly requested.

HWiNFOterm needs Python 3.11 or newer and a Windows Terminal session. It uses the Python standard library and Windows shared-memory APIs; HWiNFO is not bundled.

## Features

- Live HWiNFO sensor catalog with stable sensor/instance/reading keys, units, type, current value, and the provider's minimum, maximum, and average values.
- Four persistent dashboard tiles for key metrics, a searchable sensor list, recent history, and threshold watches.
- Pin readings to the metrics tile; double-click a catalog row to add it; double-right-click a metric to remove it.
- Double-click a live metric to add it to Recent History, then double-right-click its history row to remove it. `B` toggles metric title/value labels inside the bars.
- The Recent tab shows history-only metric cards in 1–6 columns. Drag the chart/readout divider to resize that metric's plot width; drag its bottom-right corner to change chart height. Column gutters resize all cards in a column. Cards stack independently so a short chart does not leave a tall empty row. Create, rename, and delete metric groups; toggle recent metrics into a group and switch between groups.
- Set each tracked metric's history chart height from 1–8 rows with `,` and `.` and cycle its Block (default), Stem, or Point presentation with `;`. Settings persist per metric.
- History charts include a slim right-side readout for the visible Y-axis peak and recorded maximum. Press `A` on History, Recent, or the focused dashboard History tile to show or hide 1-minute and 5-minute rolling averages; averages appear after each full window has been collected.
- History colours map each chart's current visible value range across the full theme LUT, independently of chart height.
- Add hysteresis watches with separate trigger and rearm values and an optional continuous duration. Watch state and a bounded event log persist locally.
- Reorder, hide, restore, focus, and maximize tiles. Drag the one-cell split dividers or adjust them from the keyboard. Small terminals switch to a focused-tile view.
- A command palette exposes application actions to both keyboard and pointer users. Clickable tabs, tile controls, sensor rows, modal actions, SGR mouse support, and local wheel scrolling are available in Windows Terminal.
- Declarative semantic themes, including Cyberwave and Corrosion, deterministic colour-depth fallback, ASCII mode, monochrome mode, and a LUTEXskin-inspired 256-entry palette composer with anchor stops, sRGB/linear interpolation, cyclic palettes, exact hex editing, and seeded variation. The theme LUT colours metric bars and recent-history charts.
- One-shot CLI reads for scripts and troubleshooting. Synthetic sample data is available only with the explicit `--demo` option.

## Commands

```powershell
# Run the interactive dashboard
hwinfo-term

# List available HWiNFO readings and stable keys
hwinfo-term --scan

# Read one snapshot as JSON
hwinfo-term --once --json

# Preview the interface with explicitly synthetic data
hwinfo-term --demo

# Use ASCII and disable mouse input
hwinfo-term --ascii --no-mouse
```

The scan and one-shot modes can run without an interactive terminal. They still require HWiNFO's shared-memory mapping unless `--demo` is supplied.

## Development checks

Run the standard-library unit suite and bytecode compilation from PowerShell:

```powershell
$env:PYTHONPATH = "src"
py -3 -m unittest discover -s tests -v
py -3 -m compileall -q src tests
```

To smoke-check the interactive screen without HWiNFO, run `py -3 -m hwinfo_term --demo` and press `Q` to quit.

## Controls

| Key | Action |
| --- | --- |
| `1`–`6`, `?` | Dashboard, Sensors, History, Alerts, Theme, Source, Help |
| `7` | Recent History grid |
| `Ctrl+P` | Search every command |
| `Tab`, `M`, `H`, `R`, `V` | Focus, maximize, hide, restore all, move a tile |
| `[`, `]`, `-`, `=` | Resize dashboard splits |
| `/`, `P`, `W`, `X` | Filter sensors, pin a sensor, add a watch, remove a watch |
| `B` | Toggle titles and values inside metric bars |
| `G` | Manage Recent History metric groups (on the Recent tab) |
| `←`, `→`, `[`, `]` | Change history group and grid columns (on the Recent tab); drag gutters to resize column widths |
| `,`, `.` | Reduce or increase the selected history chart's height (1–8 rows) on Recent or the focused dashboard history tile |
| `;` | Cycle the selected history chart through Block, Stem, and Point styles |
| `A` | Show or hide 1-minute and 5-minute averages in history chart readouts |
| `T`, `E` | Select the next theme, edit the palette |
| `Q` | Quit and restore the terminal |

On the dashboard, double-click a sensor row to add it to Live Metrics, double-click a metric row to add it to Recent History, and double-right-click a row in either tile to remove it. Tile maximize and close controls always act on the tile they are drawn on. Recent History charts rescale their LUT mapping to the values currently visible in each chart. On the Recent tab, drag the chart/readout divider to set the plot width per metric; drag the bottom-right corner to change its chart height, or a column gutter to resize all cards in that column.

The palette editor uses arrow keys to select and adjust stops, `A` to add a stop, `X` to remove an unlocked stop, `L` to lock or unlock, `H` for exact hex input, `,` and `.` to move a stop, `C` for cyclic mode, `I` for interpolation, `+` and `-` for seeded variation, `S` to advance the seed, Enter to commit, and Escape to discard the preview.

## Local state

Configuration, tile layout, pinned or excluded metrics, Recent History entries and groups, its column count and widths, per-metric chart/readout widths, chart heights and styles, the in-bar label option, watches, and events live in `%LOCALAPPDATA%\HWiNFOterm\config.json`. A previous configuration is copied to `config.json.bak` before writes. Use `--state-dir` to select a different directory.

The reader opens `Global\HWiNFO_SENS_SM2` with read permission and coordinates access with `Global\HWiNFO_SM2_MUTEX`. It validates the signature, version, section ranges, element sizes, counts, and mapping bounds before reading. It accepts SM2 version 2, identifies packed versus four-byte-aligned reading values from the descriptors and sampled numbers, and reads the extended UTF-8 labels when present. It reports unsupported or ambiguous layouts instead of guessing. Sensor and reading labels are rendered as plain text; terminal control characters are stripped.

HWiNFO may deactivate Shared Memory Support after a time limit depending on its license. HWiNFOterm reports the resulting stale or unavailable state and does not alter HWiNFO settings or try to bypass that limit. See [HWiNFO's license comparison](https://www.hwinfo.com/licenses/).

## UI references

The workspace follows the tile, command parity, compact-mode, and capability-aware rendering guidance in the [TUI UI/UX specification](https://github.com/techrote/specdocz/tree/main/uiuxs/tuiuiux). Theme roles and editable LUT anchors follow the [LUTEXskin specification](https://github.com/techrote/specdocz/tree/main/uiuxs/lutexskin).

## Scope

HWiNFOterm is an independent implementation focused on live HWiNFO monitoring in Windows Terminal. It includes sensor browsing, pinning, history, local threshold watches, CLI snapshots, and TUI themes. It does not bundle HWiNFO or include the reference project's Qt tray process, generic agent/API service, or MCP gateway.

For the source-based feature review and coverage notes, see [ASSESSMENT.md](ASSESSMENT.md).
