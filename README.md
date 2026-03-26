# IPD Auto-Layout

Auto-organizes nodes in **Infor IDP / IPA** process files `.lpd` so the flow reads cleanly left-to-right, with branch paths on separate rows and long flows wrapping into neat horizontal bands.

Infor Process Designer has no built-in auto-arrange. After months of edits, process files end up with overlapping nodes, negative coordinates, and spaghetti layouts. This script fixes that in seconds.

---

## Requirements

- **Python 3.6+** — no third-party packages needed (stdlib only)

Check your Python version:
```
python --version
```

---

## Installation

```
git clone https://github.com/JazzPiece/IPD-Auto-Layout.git
cd IPD-Auto-Layout
```

Or just download `lpd_layout.py` directly. No install step needed.

---

## Usage

```
python lpd_layout.py <file.lpd> [options]
```

### Quick start

```bash
# Auto-layout with good defaults (creates backup first)
python lpd_layout.py MyProcess.lpd

# Preview what would change — nothing is written
python lpd_layout.py MyProcess.lpd --preview

# Didn't like the result? Undo it instantly
python lpd_layout.py MyProcess.lpd --restore

# Cleanest look for branchy processes — Start and End on the same line
python lpd_layout.py MyProcess.lpd --flat
```

---

## All Options

### Modes

| Flag | What it does |
|------|-------------|
| _(none)_ | Layout the file and write it (backup created first) |
| `--preview` | Print new coordinates to screen, **don't write** |
| `--restore` | Restore the most recent auto-backup for this file |

### Layout style

| Flag | What it does |
|------|-------------|
| _(auto)_ | Wrap into ~3 horizontal bands for a balanced square canvas |
| `--flat` | Single row; side branches drop below and return vertically — rectangular bracket shape. Start and End stay on the same y-axis. |
| `--no-wrap` | One long horizontal row, no wrapping |
| `--bands N` | Force exactly N horizontal bands (e.g. `--bands 2`) |
| `--max-cols N` | Wrap after exactly N columns per band (e.g. `--max-cols 12`) |

> **Tip:** `--flat` is the cleanest option for most processes — branches form a neat bracket below the main flow.
> **Tip:** `--bands 2` is good for medium processes. `--bands 4` packs large processes tighter vertically.

### Spacing

| Flag | Default | What it does |
|------|---------|-------------|
| `--col-width N` | `160` | Pixels between columns (horizontal gap) |
| `--row-height N` | `100` | Pixels between parallel rows (vertical gap) |
| `--band-gap N` | `80` | Extra vertical gap between bands when wrapping |

> `--col-width 200` gives more breathing room. `--col-width 120` compresses wide processes.

### Canvas origin

| Flag | Default | What it does |
|------|---------|-------------|
| `--start-x N` | `40` | Left margin in pixels |
| `--start-y N` | `80` | Top margin in pixels |

---

## How it works

**Columns (X position):**
Each node's column = the longest path (in edges) from START to that node. Nodes further downstream get higher column numbers — so the flow always reads left to right. Side-branch targets are placed at the **same column as their BRANCH parent**, making the entry edge draw as a vertical line in IDP Studio.

**Rows (Y position):**
Nodes are processed in topological order. The main flow stays on row 0 (top). When a `BRANCH` node splits the flow, the heaviest downstream path inherits the current row; each additional path gets the next unused row below. Merge points (where paths converge) automatically snap back to the lowest predecessor row.

**Flat mode (`--flat`):**
After row assignment, the last node of each side branch is moved to the **same column as the merge point**. Combined with the vertical entry edge, this forms a rectangular bracket:

```
START → A → BRANCH → C → D → MERGE → END
                ↓               ↑
                B ──────────────┘
```

**Bands (wrapping):**
Columns are grouped into horizontal bands. Band boundaries are only placed at "safe" columns — gaps between branch sections, never mid-branch. The default auto-targets ~3 bands for a balanced canvas.

**Backup & restore:**
Every run creates a timestamped backup before writing:
```
MyProcess_layout_backup_20260326_143012.lpd
```
`--restore` finds the newest backup for that file and copies it back.

---

## Output example

```
Parsing: test.lpd
Backup:  test_layout_backup_20260326_082345.lpd
  Nodes: 22  |  Columns: 21  |  Parallel rows: 2  |  1 band of ~21 cols
  Canvas: ~3400 x 280 px
Done.    test.lpd
Tip:     To undo -> python lpd_layout.py "test.lpd" --restore
```

---

## Tested on

- Infor Landmark / IDP version `9.1.0` (Landmark 2026)
- Multi-tenant hosted Infor
- Processes from 22 nodes (simple) to 178 nodes (14 branches, 24 iterators)

---

## License

MIT

---
