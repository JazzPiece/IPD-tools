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

## All Flags

### Mode flags

| Flag | What it does |
|------|-------------|
| _(none)_ | Layout the file and write it (backup created first) |
| `--preview` | Print new coordinates to screen, **don't write** |
| `--restore` | Restore the most recent auto-backup for this file |

---

### Layout style flags _(mutually exclusive — pick one or use auto default)_

| Flag | Example | What it does |
|------|---------|-------------|
| _(auto)_ | | Wrap into ~3 horizontal bands for a balanced square canvas |
| `--flat` | `--flat` | Single row; branch/error paths drop below and return vertically (rectangular bracket). Start and End stay on the same y-axis. |
| `--no-wrap` | `--no-wrap` | One long horizontal row, no wrapping |
| `--bands N` | `--bands 2` | Force exactly N horizontal bands |
| `--max-cols N` | `--max-cols 12` | Wrap after exactly N columns per band |

> `--flat` is the cleanest option for most processes — branches form a neat bracket below the main flow.
> `--bands 2` is good for medium processes. `--bands 4` packs large processes tighter vertically.

---

### Spacing flags

| Flag | Default | What it does |
|------|---------|-------------|
| `--col-width N` | `160` | Pixels between columns (horizontal gap) |
| `--row-height N` | `100` | Pixels between parallel rows (vertical gap) |
| `--band-gap N` | `80` | Extra vertical gap between bands when wrapping |

> `--col-width 200` gives more breathing room. `--col-width 120` compresses wide processes.

---

### Canvas origin flags

| Flag | Default | What it does |
|------|---------|-------------|
| `--start-x N` | `40` | Left margin in pixels |
| `--start-y N` | `80` | Top margin in pixels |

---

### All flags at a glance

```
python lpd_layout.py MyProcess.lpd                        # auto layout
python lpd_layout.py MyProcess.lpd --preview              # preview only
python lpd_layout.py MyProcess.lpd --restore              # undo

python lpd_layout.py MyProcess.lpd --flat                 # rectangular bracket style
python lpd_layout.py MyProcess.lpd --no-wrap              # single long row
python lpd_layout.py MyProcess.lpd --bands 2              # 2 horizontal bands
python lpd_layout.py MyProcess.lpd --max-cols 12          # wrap at 12 cols

python lpd_layout.py MyProcess.lpd --col-width 200        # wider columns
python lpd_layout.py MyProcess.lpd --row-height 120       # taller rows
python lpd_layout.py MyProcess.lpd --band-gap 120         # more space between bands

python lpd_layout.py MyProcess.lpd --flat --col-width 180 --row-height 120
```

---

## How it works

### Columns (X position) — Longest-path relaxation

Each node's column equals the longest path in edges from START to that node. The algorithm walks nodes in **topological order** and relaxes: `col[successor] = max(col[successor], col[node] + 1)`. This guarantees nodes further downstream always sit to the right — the flow always reads left-to-right with no backtracking.

Side-branch targets are placed at the **same column as their BRANCH parent** so the entry edge draws as a vertical line in IDP Studio.

### Rows (Y position) — Topological order with pre-assignment

Nodes are processed in **topological order** (Kahn's algorithm), so every predecessor is resolved before its successors. When a BRANCH node is processed, it immediately pre-assigns rows to all its outgoing targets via a `pending` dict — the heaviest downstream path inherits the current row, each additional path gets the next unused row below. Merge points automatically receive `min(predecessor rows)`, which snaps the flow back to the main row.

Error handler nodes (nodes whose only incoming edges are ERROR type, e.g. `NotifyError9000`) are automatically placed on a dedicated row below the main flow so they never overlap with `End` or other row-0 nodes.

> **Why not DFS?** The original prototype used DFS for row assignment. If a side-branch node was visited first, it would lock downstream merge-point nodes onto the wrong row and the main flow would shift down. Topological order eliminates this entirely — each node is processed exactly once, after all its predecessors are settled.

### Branch analysis — BFS (exclusive reachable count)

To decide which outgoing path from a BRANCH node is the "main" branch (stays on the current row), the script runs a **BFS** from each branch target and counts how many nodes are reachable *exclusively* from that target (not shared with other paths). The target with the most exclusive descendants is the main branch; the others drop to new rows below.

### Safe band-cut columns — BFS (common descendants)

To prevent band wrapping from cutting mid-branch, the script runs a **BFS** from each pair of branch targets to find their common descendants (the merge point). Any column between the BRANCH node and its merge point is marked unsafe for wrapping — bands only break at clean gaps between branch sections.

### Algorithm summary

| Phase | Algorithm |
|-------|-----------|
| Column (X) | Longest-path relaxation on topological order |
| Row (Y) | Topological order (Kahn's) + pending pre-assignment |
| Main branch detection | BFS — exclusive reachable count per target |
| Safe wrap columns | BFS — common descendants (merge point) |

### Flat mode (`--flat`)

After row assignment, the last node of each side branch is moved to the **same column as the merge point**. Combined with the vertical entry edge (branch-entry alignment), this forms a rectangular bracket shape:

```
START → A → BRANCH → C → D → MERGE → END
                ↓               ↑
                B ──────────────┘
```

### Backup & restore

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
