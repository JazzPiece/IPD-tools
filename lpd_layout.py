#!/usr/bin/env python3
"""
lpd_layout.py — Auto-layout organizer for Infor IDP/IPA .lpd/.idp process files

Recomputes x/y coordinates for every activity node so the flow reads
cleanly left-to-right, with parallel branch paths on separate horizontal rows.

Usage:
  python lpd_layout.py <file.lpd>                      # layout + auto-backup
  python lpd_layout.py <file.lpd> --preview            # show new coords, don't write
  python lpd_layout.py <file.lpd> --restore            # restore most recent backup
  python lpd_layout.py <file.lpd> --col-width 220      # custom column width
  python lpd_layout.py <file.lpd> --row-height 150     # custom row height

Requirements: Python 3.6+ (stdlib only, no pip installs needed)
"""

import argparse
import glob
import math
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from datetime import datetime

# ── Layout constants (overridable via CLI) ────────────────────────────────────
COL_WIDTH  = 160   # pixels between columns  (horizontal spacing)
ROW_HEIGHT = 100   # pixels between parallel rows (vertical spacing)
BAND_GAP   = 80    # fixed extra gap between bands (vertical)
START_X    = 40    # left margin
START_Y    = 80    # top margin (main flow anchored here)
MAX_COLS   = 0     # 0 = auto-detect (targets ~3 bands); override with --max-cols or --bands

# Node types that are iterators and MUST have a paired ItEnd
ITERATOR_TYPES = {
    'ITERFR', 'QUERY', 'LM', 'LOOP', 'DATAEX',
    'LDAPQ', 'RMQR', 'IONIN', 'SQLQR', 'FORMTXN',
}


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_lpd(filepath):
    """Return (tree, root, activities_dict, edges_list)."""
    ET.register_namespace('', '')  # suppress ns0 prefix noise
    tree = ET.parse(filepath)
    root = tree.getroot()

    activities = {}
    for act in root.findall('.//activity'):
        aid = act.get('id')
        if aid:
            activities[aid] = act

    edges = []
    for edge in root.findall('.//edge'):
        edges.append({
            'id':   edge.get('id'),
            'from': edge.get('from'),
            'to':   edge.get('to'),
            'type': edge.get('edgeType', 'NORMAL'),
        })

    return tree, root, activities, edges


# ── Graph construction ────────────────────────────────────────────────────────

def build_graph(activities, edges):
    """Return (out_edges, in_edges) as dicts of node_id → list of (neighbor, edge_type)."""
    out_edges = defaultdict(list)
    in_edges  = defaultdict(list)
    all_ids   = set(activities.keys())

    for e in edges:
        src, dst = e['from'], e['to']
        if src in all_ids and dst in all_ids:
            out_edges[src].append((dst, e['type']))
            in_edges[dst].append((src, e['type']))

    return dict(out_edges), dict(in_edges)


# ── Topological sort (Kahn's algorithm) ──────────────────────────────────────

def topological_sort(node_ids, out_edges, in_edges):
    """Return nodes in topological order. Handles disconnected nodes."""
    in_degree = {n: 0 for n in node_ids}
    for n in node_ids:
        for (dst, _) in out_edges.get(n, []):
            in_degree[dst] = in_degree.get(dst, 0) + 1

    queue = deque(n for n in node_ids if in_degree[n] == 0)
    order = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for (dst, _) in out_edges.get(n, []):
            in_degree[dst] -= 1
            if in_degree[dst] == 0:
                queue.append(dst)

    # Any remaining nodes (cycles — shouldn't exist in valid LPD) appended last
    remaining = set(node_ids) - set(order)
    order.extend(sorted(remaining))
    return order


# ── Branch analysis ───────────────────────────────────────────────────────────

def branch_exclusive_counts(branch_targets, out_edges):
    """
    For each branch target, count nodes reachable ONLY from it (exclusive descendants).
    The target with the most exclusive nodes is the "main" (heaviest) path.
    """
    def bfs(start):
        visited, q = set(), deque([start])
        while q:
            n = q.popleft()
            if n in visited:
                continue
            visited.add(n)
            for (dst, _) in out_edges.get(n, []):
                q.append(dst)
        return visited

    reachable = {t: bfs(t) for t in branch_targets}
    result = {}
    for t in branch_targets:
        others = set().union(*(v for k, v in reachable.items() if k != t))
        result[t] = len(reachable[t] - others)
    return result


def find_main_branch_targets(node_ids, out_edges, activities):
    """
    For each BRANCH node, identify which outgoing target is the "main" branch
    (most exclusive descendants → stays on current row, continues right).
    Returns {branch_node_id: main_target_id}.
    """
    main = {}
    for n in node_ids:
        if activities[n].get('activityType') != 'BRANCH':
            continue
        targets = [dst for dst, t in out_edges.get(n, []) if t == 'BRANCH']
        if not targets:
            continue
        if len(targets) == 1:
            main[n] = targets[0]
        else:
            excl = branch_exclusive_counts(targets, out_edges)
            main[n] = max(targets, key=lambda t: excl.get(t, 0))
    return main


# ── Column assignment ─────────────────────────────────────────────────────────

def assign_columns(node_ids, out_edges, in_edges, activities):
    """
    Longest-path column assignment with branch-entry alignment:
    - Main branch target (heaviest exclusive path) → col N+1  (continues right)
    - Side branch targets                          → col N    (same X as BRANCH)

    The side-branch rule makes BRANCH→side_target edges draw as vertical lines
    in IDP Studio (same x-coordinate, different y).
    """
    topo = topological_sort(node_ids, out_edges, in_edges)
    main_target = find_main_branch_targets(node_ids, out_edges, activities)
    branch_nodes = set(main_target.keys())

    cols = {n: 0 for n in node_ids}
    for n in topo:
        for (dst, edge_type) in out_edges.get(n, []):
            if edge_type == 'BRANCH' and n in branch_nodes:
                # Main branch continues right; side branches start at same column
                candidate = cols[n] + 1 if main_target.get(n) == dst else cols[n]
            else:
                candidate = cols[n] + 1
            if candidate > cols.get(dst, 0):
                cols[dst] = candidate

    return cols, topo, main_target


# ── Row assignment ────────────────────────────────────────────────────────────

def assign_rows(node_ids, out_edges, in_edges, activities, main_target, topo_order):
    """
    Topological-order row assignment (guarantees predecessors are resolved first).

    Rules:
    - Root nodes (no predecessors): row 0.
    - BRANCH node → pre-assigns rows to all its branch targets before they are
      processed: main target inherits current row, side targets get a new row below.
    - Pure error handlers (ALL incoming edges are ERROR type) → bumped to a new
      row below the main flow so they never overlap with End or other row-0 nodes.
    - All other nodes: min(predecessor rows) ignoring ERROR-edge predecessors.
      Merge points naturally snap back to the lowest row.
    """
    rows = {}
    max_row = [0]
    pending = {}   # node_id -> row pre-assigned by a BRANCH node above it

    for n in topo_order:
        preds = in_edges.get(n, [])

        if not preds:
            rows[n] = 0
        else:
            # Separate error-edge preds from structural (normal/branch/iter) preds
            non_error_preds = [(p, t) for p, t in preds if t != 'ERROR']

            if not non_error_preds:
                # Pure error handler — give it a dedicated row below main flow
                if n in pending:
                    rows[n] = pending[n]
                else:
                    max_row[0] += 1
                    rows[n] = max_row[0]
            else:
                # Use only structural predecessors to determine row
                non_branch_rows = [rows.get(p, 0) for p, t in non_error_preds if t != 'BRANCH']
                if n in pending:
                    if non_branch_rows:
                        rows[n] = min(pending[n], min(non_branch_rows))
                    else:
                        rows[n] = pending[n]
                else:
                    rows[n] = min(rows.get(p, 0) for p, _ in non_error_preds)

        max_row[0] = max(max_row[0], rows[n])

        # Pre-assign rows to branch targets immediately
        if activities[n].get('activityType') == 'BRANCH':
            branch_targets = [dst for dst, t in out_edges.get(n, []) if t == 'BRANCH']
            main = main_target.get(n)
            for dst in branch_targets:
                if dst == main:
                    pending[dst] = rows[n]      # main continues on same row
                else:
                    max_row[0] += 1
                    pending[dst] = max_row[0]   # side gets next available row

    for n in node_ids:
        if n not in rows:
            rows[n] = 0

    return rows


# ── Flat mode: align branch returns ──────────────────────────────────────────

def align_branch_returns(cols, rows, in_edges):
    """
    For each merge point (node with 2+ predecessors), move any side-branch
    predecessor (row > merge row) to the merge point's column.

    This makes the return edge from the last side-branch node to the merge
    point draw as a vertical line in IDP Studio (same x, different y).
    Combined with branch-entry alignment (vertical entry edges), the result
    is a rectangular bracket shape for each branch:

        BRANCH ──→ D ──→ E ──→ MERGE ──→ ...
          ↓                      ↑
          C ─────────────────────┘   (C is on row below; entry+return are vertical)
    """
    for n in list(cols.keys()):
        preds = in_edges.get(n, [])
        if len(preds) <= 1:
            continue
        merge_row = rows.get(n, 0)
        merge_col = cols[n]
        for pred, _edge_type in preds:
            if rows.get(pred, 0) > merge_row:
                cols[pred] = merge_col  # same X as merge point → vertical return edge


# ── Validation (edge reference integrity) ────────────────────────────────────

def validate(root, activity_ids):
    """Return list of error strings (empty = clean)."""
    errors = []
    edges  = root.findall('.//edge')

    for edge in edges:
        f, t = edge.get('from'), edge.get('to')
        if f not in activity_ids:
            errors.append(f"Edge {edge.get('id')}: from='{f}' references missing node")
        if t not in activity_ids:
            errors.append(f"Edge {edge.get('id')}: to='{t}' references missing node")

    for act in root.findall('.//activity'):
        goto_el = act.find('.//OnActivityError/goto')
        act_el  = act.find('.//OnActivityError/activity')
        if goto_el is not None and goto_el.text == 'true':
            if act_el is not None and act_el.text and act_el.text not in activity_ids:
                errors.append(
                    f"{act.get('id')}: OnActivityError goto='{act_el.text}' references missing node"
                )

    return errors


# ── Backup / Restore ──────────────────────────────────────────────────────────

def make_backup(filepath):
    """Copy file to <name>_layout_backup_YYYYMMDD_HHMMSS.lpd. Returns backup path."""
    base, ext = os.path.splitext(filepath)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{base}_layout_backup_{timestamp}{ext}"
    shutil.copy2(filepath, backup_path)
    return backup_path


def find_latest_backup(filepath):
    """Return path to the most recent layout backup for this file, or None."""
    base, ext = os.path.splitext(filepath)
    pattern = f"{base}_layout_backup_*{ext}"
    candidates = sorted(glob.glob(pattern))
    return candidates[-1] if candidates else None


def restore_backup(filepath):
    backup = find_latest_backup(filepath)
    if not backup:
        print(f"No backup found for {filepath}")
        return False
    shutil.copy2(backup, filepath)
    print(f"Restored from: {backup}")
    return True


# ── XML write helper ──────────────────────────────────────────────────────────

def write_lpd(tree, filepath):
    """
    Write back as single-line XML (IDP expects this format).
    Preserve the XML declaration and encoding.
    """
    # ElementTree.write handles the file; use xml_declaration=True
    tree.write(filepath, encoding='UTF-8', xml_declaration=True)

    # ElementTree writes <?xml version='1.0' encoding='UTF-8'?> — normalise to double quotes
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    content = content.replace("<?xml version='1.0' encoding='UTF-8'?>",
                               '<?xml version="1.0" encoding="UTF-8"?>', 1)
    with open(filepath, 'w', encoding='utf-8', newline='') as f:
        f.write(content)


# ── Band (wrap) helpers ───────────────────────────────────────────────────────

def resolve_target_cols(max_cols_arg, bands_arg, no_wrap_arg, total_cols):
    """
    Return the target columns-per-band.
    Priority: --no-wrap > --bands > --max-cols > auto (~3 bands, min 8 cols).
    """
    if no_wrap_arg:
        return max(total_cols, 1)
    if bands_arg and bands_arg > 0:
        return max(1, math.ceil(total_cols / bands_arg))
    if max_cols_arg and max_cols_arg > 0:
        return max_cols_arg
    return max(8, math.ceil(total_cols / 3))


def find_safe_columns(cols, out_edges, activities, total_cols):
    """
    A column is 'safe' for band wrapping if no open BRANCH section spans it.
    For each BRANCH node we find its merge column (first common descendant of
    all branch targets). Any column strictly between branch_col and merge_col
    is unsafe.
    """
    branch_nodes = [n for n in cols if activities[n].get('activityType') == 'BRANCH']

    unsafe_spans = []   # list of (branch_col, merge_col) half-open intervals [b, m)
    for bn in branch_nodes:
        targets = [dst for dst, t in out_edges.get(bn, []) if t == 'BRANCH']
        if len(targets) < 2:
            continue

        # BFS reachable set from each branch target
        def bfs(start):
            visited, q = set(), deque([start])
            while q:
                n = q.popleft()
                if n in visited:
                    continue
                visited.add(n)
                for (dst, _) in out_edges.get(n, []):
                    q.append(dst)
            return visited

        reachable = [bfs(t) for t in targets]
        common = reachable[0]
        for r in reachable[1:]:
            common = common & r

        if common:
            merge_col = min(cols.get(n, 0) for n in common)
            unsafe_spans.append((cols[bn], merge_col))

    safe = set()
    for c in range(total_cols):
        is_safe = not any(bc <= c < mc for bc, mc in unsafe_spans)
        if is_safe:
            safe.add(c)
    return safe


def assign_bands(cols, rows, out_edges, activities, target_cols):
    """
    Assign each column to a band, wrapping ONLY at columns that are safe
    (not inside any open BRANCH section).
    Returns (col_to_band, band_start_col) dicts.
    """
    total_cols = max(cols.values(), default=0) + 1
    safe = find_safe_columns(cols, out_edges, activities, total_cols)

    col_to_band = {}
    band_start = {0: 0}
    band = 0
    cols_in_band = 0

    for c in range(total_cols):
        col_to_band[c] = band
        cols_in_band += 1
        next_c = c + 1
        if next_c < total_cols and next_c in safe:
            if cols_in_band >= target_cols:
                band += 1
                band_start[band] = next_c
                cols_in_band = 0

    return col_to_band, band_start


def compute_band_offsets(cols, rows, col_to_band, row_height, band_gap):
    """
    Compute cumulative Y offsets for each band.
    Band height = (max_row_in_band + 1) * row_height + band_gap.
    """
    max_row_per_band = defaultdict(int)
    for n in cols:
        b = col_to_band.get(cols[n], 0)
        max_row_per_band[b] = max(max_row_per_band[b], rows.get(n, 0))

    num_bands = max(col_to_band.values(), default=0) + 1
    offsets = {}
    cumulative = 0
    for b in range(num_bands):
        offsets[b] = cumulative
        cumulative += (max_row_per_band[b] + 1) * row_height + band_gap
    return offsets


def node_pixels(node_id, cols, rows, col_width, row_height,
                col_to_band, band_start, band_offsets, start_x, start_y):
    """Return (x, y) pixel position for a node."""
    c = cols[node_id]
    r = rows[node_id]
    b = col_to_band.get(c, 0)
    x = start_x + (c - band_start.get(b, 0)) * col_width
    y = start_y + band_offsets.get(b, 0) + r * row_height
    return x, y


# ── Main layout routine ───────────────────────────────────────────────────────

def layout(filepath, col_width, row_height, band_gap, max_cols_arg, bands_arg, no_wrap,
           start_x, start_y, flat=False, preview=False):
    print(f"Parsing: {filepath}")
    tree, root, activities, edges = parse_lpd(filepath)
    node_ids = list(activities.keys())

    if not node_ids:
        print("No activity nodes found -- nothing to do.")
        return False

    # Validate before touching anything
    pre_errors = validate(root, set(node_ids))
    if pre_errors:
        print("WARNING: File has pre-existing validation errors:")
        for e in pre_errors:
            print(f"  {e}")

    out_edges, in_edges = build_graph(activities, edges)
    cols, topo_order, main_target = assign_columns(node_ids, out_edges, in_edges, activities)
    rows                          = assign_rows(node_ids, out_edges, in_edges, activities, main_target, topo_order)

    # Flat mode: pull each side-branch's last node to the merge column
    # so the return edge draws vertical (rectangular bracket shape)
    if flat:
        align_branch_returns(cols, rows, in_edges)
        no_wrap = True   # flat always uses a single horizontal band

    total_cols  = max(cols.values(), default=0) + 1
    target_cols = resolve_target_cols(max_cols_arg, bands_arg, no_wrap, total_cols)

    col_to_band, band_start = assign_bands(cols, rows, out_edges, activities, target_cols)
    band_offsets            = compute_band_offsets(cols, rows, col_to_band, row_height, band_gap)

    # Canvas size report
    max_row   = max(rows.values(), default=0)
    num_bands = max(col_to_band.values(), default=0) + 1
    max_col_in_band = max(
        (cols[n] - band_start.get(col_to_band.get(cols[n], 0), 0) for n in cols),
        default=0
    )
    canvas_w = start_x + (max_col_in_band + 1) * col_width
    canvas_h = start_y + band_offsets.get(num_bands - 1, 0) + (max_row + 1) * row_height

    band_desc = f"{num_bands} band{'s' if num_bands != 1 else ''} of ~{target_cols} cols"
    print(f"  Nodes: {len(node_ids)}  |  Columns: {total_cols}  |  Parallel rows: {max_row+1}  |  {band_desc}")
    print(f"  Canvas: ~{canvas_w} x {canvas_h} px")

    if preview:
        print(f"\n  Preview (id -> col / band / row -> x, y):")
        print(f"  {'ID':<35} {'COL':>4} {'BND':>4} {'ROW':>4}   {'X':>5} {'Y':>5}")
        print(f"  {'-'*35} {'-'*4} {'-'*4} {'-'*4}   {'-'*5} {'-'*5}")
        for n in topo_order:
            x, y = node_pixels(n, cols, rows, col_width, row_height,
                               col_to_band, band_start, band_offsets, start_x, start_y)
            print(f"  {n:<35} {cols[n]:>4} {col_to_band.get(cols[n],0):>4} {rows[n]:>4}   {x:>5} {y:>5}")
        return True

    # Apply coordinates
    for n, act in activities.items():
        x, y = node_pixels(n, cols, rows, col_width, row_height,
                           col_to_band, band_start, band_offsets, start_x, start_y)
        act.set('x', str(x))
        act.set('y', str(y))

    # Validate after layout (coordinates don't affect edge refs, but sanity-check)
    post_errors = validate(root, set(node_ids))
    if post_errors:
        print("ERROR: Validation failed after layout -- aborting write.")
        for e in post_errors:
            print(f"  {e}")
        return False

    return tree


def main():
    parser = argparse.ArgumentParser(
        description='Auto-layout Infor IDP/IPA .lpd process files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Layout style (mutually exclusive -- pick one or use auto default):
  --flat          Single row; error/branch paths drop below and return vertically
  --no-wrap       Single horizontal row, no wrapping
  --bands N       Target N horizontal bands (e.g. --bands 2)
  --max-cols N    Exact columns per band before wrapping

Spacing:
  --col-width N   Horizontal spacing in pixels  (default: 160)
  --row-height N  Vertical spacing in pixels    (default: 100)
  --band-gap N    Extra gap between bands       (default: 80)
  --start-x N     Left canvas margin in pixels  (default: 40)
  --start-y N     Top canvas margin in pixels   (default: 80)

Examples:
  python lpd_layout.py MyProcess.lpd                   # auto layout + backup
  python lpd_layout.py MyProcess.lpd --preview         # show result without writing
  python lpd_layout.py MyProcess.lpd --restore         # undo last layout
  python lpd_layout.py MyProcess.lpd --flat            # cleanest for branchy flows
  python lpd_layout.py MyProcess.lpd --bands 2         # force 2 horizontal bands
  python lpd_layout.py MyProcess.lpd --no-wrap         # single long row
  python lpd_layout.py MyProcess.lpd --col-width 200 --row-height 120
        """
    )
    parser.add_argument('file', help='.lpd or .idp file to layout')

    # Modes
    parser.add_argument('--preview', action='store_true',
                        help='Print new coordinates without writing the file')
    parser.add_argument('--restore', action='store_true',
                        help='Restore the most recent backup for this file')

    # Wrap control (mutually exclusive intent, last one wins in resolve_max_cols)
    wrap = parser.add_mutually_exclusive_group()
    wrap.add_argument('--bands',    type=int, metavar='N', default=0,
                      help='Target N horizontal bands of flow (auto-computes column wrap point)')
    wrap.add_argument('--max-cols', type=int, metavar='N', default=0,
                      help='Exact columns per band before wrapping (default: auto ~3 bands)')
    wrap.add_argument('--no-wrap',  action='store_true',
                      help='No wrapping -- lay out in one long horizontal row')
    wrap.add_argument('--flat',     action='store_true',
                      help='Single row; side branches drop below and return vertically (rectangular bracket shape)')

    # Spacing
    parser.add_argument('--col-width',  type=int, default=COL_WIDTH,  metavar='N',
                        help=f'Horizontal spacing between columns in px (default: {COL_WIDTH})')
    parser.add_argument('--row-height', type=int, default=ROW_HEIGHT, metavar='N',
                        help=f'Vertical spacing between parallel rows in px (default: {ROW_HEIGHT})')
    parser.add_argument('--band-gap',   type=int, default=BAND_GAP,   metavar='N',
                        help=f'Extra vertical gap between bands in px (default: {BAND_GAP})')

    # Canvas origin
    parser.add_argument('--start-x', type=int, default=START_X, metavar='N',
                        help=f'Left canvas margin in px (default: {START_X})')
    parser.add_argument('--start-y', type=int, default=START_Y, metavar='N',
                        help=f'Top canvas margin in px (default: {START_Y})')

    args = parser.parse_args()

    filepath = args.file
    if not os.path.isfile(filepath):
        print(f"File not found: {filepath}")
        sys.exit(1)

    # ── Restore mode ──────────────────────────────────────────────────────────
    if args.restore:
        sys.exit(0 if restore_backup(filepath) else 1)

    # ── Preview mode ──────────────────────────────────────────────────────────
    if args.preview:
        layout(filepath,
               col_width=args.col_width, row_height=args.row_height, band_gap=args.band_gap,
               max_cols_arg=args.max_cols, bands_arg=args.bands, no_wrap=args.no_wrap,
               start_x=args.start_x, start_y=args.start_y,
               flat=args.flat, preview=True)
        sys.exit(0)

    # ── Layout + write ────────────────────────────────────────────────────────
    backup_path = make_backup(filepath)
    print(f"Backup:  {backup_path}")

    result = layout(filepath,
                    col_width=args.col_width, row_height=args.row_height, band_gap=args.band_gap,
                    max_cols_arg=args.max_cols, bands_arg=args.bands, no_wrap=args.no_wrap,
                    start_x=args.start_x, start_y=args.start_y,
                    flat=args.flat, preview=False)

    if result is False:
        print("Layout failed. Original file unchanged (backup exists at above path).")
        sys.exit(1)
    if result is True:
        sys.exit(0)

    write_lpd(result, filepath)
    print(f"Done.    {filepath}")
    print(f"Tip:     To undo -> python lpd_layout.py \"{filepath}\" --restore")


if __name__ == '__main__':
    main()