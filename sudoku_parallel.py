#!/usr/bin/env python3
"""
sudoku_parallel.py

Copy of sudoku_normal.py with added parallel solving support (ProcessPoolExecutor).
"""

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

import time
import argparse
import math
import sys
import threading
import logging

# Configure basic logging to file
logging.basicConfig(filename='sudoku.log', level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

import multiprocessing
import concurrent.futures
import os


class DLXNode:
    __slots__ = ('L','R','U','D','C','row')
    def __init__(self):
        self.L = self.R = self.U = self.D = self
        self.C = None
        self.row = None


class ColumnNode(DLXNode):
    __slots__ = ('size','name')
    def __init__(self, name):
        super().__init__()
        self.size = 0
        self.name = name
        self.C = self


class DLX:
    def __init__(self, num_cols):
        self.header = ColumnNode('header')
        # create column nodes
        self.columns = [ColumnNode(i) for i in range(num_cols)]
        # link columns horizontally under header
        h = self.header
        h.L = self.columns[-1]
        h.R = self.columns[0]
        for i, col in enumerate(self.columns):
            col.L = self.columns[i-1] if i-1 >= 0 else h
            col.R = self.columns[i+1] if i+1 < len(self.columns) else h
            # vertical links point to self initially
            col.U = col.D = col
        # fix circular linkage
        h.L.R = h
        h.R.L = h

        # mapping from row-id to one node belonging to that row
        self.row_node = {}

    def add_row(self, row_id, cols):
        """Add a row covering the list of column indices in `cols`.
        `row_id` can be any hashable that identifies the candidate (e.g. (cell, val)).
        """
        nodes = []
        for c in cols:
            try:
                col = self.columns[c]
            except IndexError:
                print(f"IndexError in add_row! row_id={row_id}, cols={cols}, c={c}, num_cols={len(self.columns)}")
                raise
            node = DLXNode()
            node.C = col
            node.row = row_id
            # insert node at bottom of column
            node.U = col.U
            node.D = col
            col.U.D = node
            col.U = node
            col.size += 1
            nodes.append(node)

        # link the row horizontally
        for i in range(len(nodes)):
            nodes[i].R = nodes[(i+1) % len(nodes)]
            nodes[i].L = nodes[(i-1) % len(nodes)]

        # store representative node
        if nodes:
            self.row_node[row_id] = nodes[0]

    def cover(self, col):
        # remove column header
        col.R.L = col.L
        col.L.R = col.R
        # for each row in column
        i = col.D
        while i is not col:
            j = i.R
            while j is not i:
                j.D.U = j.U
                j.U.D = j.D
                j.C.size -= 1
                j = j.R
            i = i.D

    def uncover(self, col):
        i = col.U
        while i is not col:
            j = i.L
            while j is not i:
                j.C.size += 1
                j.D.U = j
                j.U.D = j
                j = j.L
            i = i.U
        col.R.L = col
        col.L.R = col


def generate_solution(N, br, bc):
    """Deterministic generator for a filled N x N Sudoku where br*bc == N.
    Pattern used: value = (r*bc + floor(r/br) + c) % N + 1
    This guarantees each block (br x bc) contains all values 1..N and each
    row and column is a permutation.
    """
    sol = [[0]*N for _ in range(N)]
    for r in range(N):
        for c in range(N):
            sol[r][c] = ((r * bc + (r // br) + c) % N) + 1
    return sol


def puzzle_from_solution(sol, remove_rate=0.0, seed=42):
    import random
    random.seed(seed)
    N = len(sol)
    puzzle = [row[:] for row in sol]
    if remove_rate <= 0:
        return puzzle
    total = N * N
    to_remove = int(total * remove_rate)
    indices = list(range(total))
    random.shuffle(indices)
    for idx in indices[:to_remove]:
        r = idx // N
        c = idx % N
        puzzle[r][c] = 0
    return puzzle


def randomize_solution(sol, br, bc, seed=None):
    import random
    if seed is not None:
        random.seed(seed)
    N = len(sol)
    symbols = list(range(1, N+1))
    random.shuffle(symbols)
    sym_map = {i+1: symbols[i] for i in range(N)}
    row_band_count = N // br
    band_indices = list(range(row_band_count))
    random.shuffle(band_indices)
    new_rows = []
    for band in band_indices:
        rows_in_band = list(range(band * br, (band + 1) * br))
        random.shuffle(rows_in_band)
        for r in rows_in_band:
            new_rows.append(sol[r][:])
    col_stack_count = N // bc
    stack_indices = list(range(col_stack_count))
    random.shuffle(stack_indices)
    cols_perm_by_stack = {}
    for stack in stack_indices:
        cols = list(range(stack * bc, (stack + 1) * bc))
        random.shuffle(cols)
        cols_perm_by_stack[stack] = cols
    new_grid = [[0]*N for _ in range(N)]
    for new_r, old_row in enumerate(new_rows):
        new_row = [0]*N
        dest_c = 0
        for stack in stack_indices:
            cols_in_stack = cols_perm_by_stack[stack]
            for c in cols_in_stack:
                new_row[dest_c] = sym_map[old_row[c]]
                dest_c += 1
        new_grid[new_r] = new_row
    return new_grid


def build_rows_list(puzzle, N, br, bc, threshold=5_000_000):
    total_candidates = 0
    for r in range(N):
        for c in range(N):
            if puzzle[r][c] != 0:
                total_candidates += 1
            else:
                total_candidates += N
    if total_candidates > threshold:
        raise MemoryError(f"Too many candidates ({total_candidates}); aborting. Use smaller puzzle or increase threshold.")
    rows = []
    numBoxesHoriz = N // bc
    for r in range(N):
        for c in range(N):
            b = (r // br) * numBoxesHoriz + (c // bc)
            vstart = puzzle[r][c] if puzzle[r][c] != 0 else 1
            vend = puzzle[r][c] if puzzle[r][c] != 0 else N
            for v in range(vstart, vend+1):
                col1 = r * N + c
                col2 = N * N + r * N + (v - 1)
                col3 = 2 * N * N + c * N + (v - 1)
                col4 = 3 * N * N + b * N + (v - 1)
                row_id = (r * N + c, v)
                rows.append((row_id, [col1, col2, col3, col4]))
    return rows, total_candidates


def visualize_grid(grid, br, bc, cell_size=6, show_numbers=False, outpath='sudoku.png'):
    if cell_size is None:
        cell_size = 32
    if not PIL_AVAILABLE:
        raise RuntimeError('Pillow (PIL) is required to render images. Install it with: pip install pillow')
    N = len(grid)
    border = 0
    img_w = N * cell_size + border * 2
    img_h = N * cell_size + border * 2
    img = Image.new('RGB', (img_w, img_h), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    thin = 1
    thick = 3
    color_thin = (51, 51, 51)
    color_thick = (153, 153, 153)
    for x in range(N+1):
        x0 = border + x * cell_size
        w = thick if (x % bc == 0) else thin
        col = color_thick if (x % bc == 0) else color_thin
        draw.rectangle([x0, 0, x0 + w - 1, img_h], fill=col)
    for y in range(N+1):
        y0 = border + y * cell_size
        w = thick if (y % br == 0) else thin
        col = color_thick if (y % br == 0) else color_thin
        draw.rectangle([0, y0, img_w, y0 + w - 1], fill=col)
    if show_numbers:
        try:
            font = ImageFont.truetype('arial.ttf', max(8, int(cell_size*0.8)))
        except Exception:
            font = ImageFont.load_default()
        fg = (220, 220, 220)
        for r in range(N):
            for c in range(N):
                v = grid[r][c]
                if v:
                    txt = str(v)
                    x = c * cell_size + 2
                    y = r * cell_size + 1
                    draw.text((x, y), txt, fill=fg, font=font)
    img.save(outpath)
    return outpath


class SearchStopped(Exception):
    pass


def algorithm_x_search(dlx, solution_stack, stop_event):
    # recursive Algorithm X using DLX structure; returns list of chosen nodes on success
    if stop_event.is_set():
        raise SearchStopped()
    header = dlx.header
    if header.R is header:
        return list(solution_stack)

    # choose column with smallest size
    c = None
    s = 10**18
    j = header.R
    while j is not header:
        if j.size < s:
            s = j.size
            c = j
        j = j.R

    if c is None or c.size == 0:
        return None

    dlx.cover(c)
    r = c.D
    while r is not c:
        if stop_event.is_set():
            dlx.uncover(c)
            raise SearchStopped()
        solution_stack.append(r)
        j = r.R
        while j is not r:
            dlx.cover(j.C)
            j = j.R

        result = algorithm_x_search(dlx, solution_stack, stop_event)
        if result is not None:
            return result

        # backtrack
        solution_stack.pop()
        j = r.L
        while j is not r:
            dlx.uncover(j.C)
            j = j.L
        r = r.D

    dlx.uncover(c)
    return None


def solve_dlx_sequential(puzzle, N, br, bc, stop_event=None, time_limit=None):
    """Sequential DLX solver.
    Returns a solved grid (list of lists) or None if no solution/aborted.
    """
    start = time.perf_counter()
    rows_list, _ = build_rows_list(puzzle, N, br, bc)
    dlx = DLX(4 * N * N)
    for row_id, cols in rows_list:
        dlx.add_row(row_id, cols)

    solution_stack = []

    # Handle time limit using an event
    combined_stop = threading.Event()
    
    if stop_event:
        def watcher():
            stop_event.wait()
            combined_stop.set()
        threading.Thread(target=watcher, daemon=True).start()
        
    if time_limit:
        def timer_func():
            time.sleep(time_limit)
            combined_stop.set()
        threading.Thread(target=timer_func, daemon=True).start()

    try:
        # Search!
        res = algorithm_x_search(dlx, solution_stack, combined_stop)
    except SearchStopped:
        if time_limit and (time.perf_counter() - start) > time_limit:
            return None
        raise

    if res is not None:
        sol_grid = [[0]*N for _ in range(N)]
        for node in res:
            idx, val = node.row
            r = idx // N
            c = idx % N
            sol_grid[r][c] = val
        return sol_grid
    return None


# Parallel worker wrapper (top-level so it can be pickled)
def _parallel_worker(args):
    puzzle, N, br, bc, assign_rc, value, time_limit = args
    r, c = assign_rc
    pcopy = [row[:] for row in puzzle]
    pcopy[r][c] = value
    try:
        return solve_dlx_sequential(pcopy, N, br, bc, stop_event=None, time_limit=time_limit)
    except Exception as e:
        # Previously printed traceback for debugging; now log error.
        logging.error('Parallel worker error', exc_info=True)
        return None


def solve_dlx_parallel(puzzle, N, br, bc, max_workers=None, time_limit=None):
    """Parallel wrapper: pick one MRV cell and spawn workers for each candidate.
    Returns first found solution or None.
    """
    full_mask = (1 << N) - 1
    numBoxesHoriz = N // bc
    # build initial masks and empties (similar to sequential)
    grid = [row[:] for row in puzzle]
    row_mask = [0] * N
    col_mask = [0] * N
    box_mask = [0] * N
    empties = []
    for r in range(N):
        for c in range(N):
            v = grid[r][c]
            b = (r // br) * numBoxesHoriz + (c // bc)
            if v != 0:
                bit = 1 << (v - 1)
                if (row_mask[r] & bit) or (col_mask[c] & bit) or (box_mask[b] & bit):
                    return None
                row_mask[r] |= bit
                col_mask[c] |= bit
                box_mask[b] |= bit
            else:
                empties.append((r, c))

    if not empties:
        return [row[:] for row in grid]

    # find MRV cell
    best_mask = 0
    best_count = N + 1
    best_rc = None
    for (rr, cc) in empties:
        mask = full_mask & ~(row_mask[rr] | col_mask[cc] | box_mask[(rr // br) * numBoxesHoriz + (cc // bc)])
        bit_c = mask.bit_count()
        if bit_c == 0:
            return None
        if bit_c < best_count:
            best_count = bit_c
            best_mask = mask
            best_rc = (rr, cc)
            if bit_c == 1:
                break


    candidates = []
    m = best_mask
    while m:
        vbit = m & -m
        m -= vbit
        candidates.append(vbit.bit_length())

    if not candidates:
        return None

    if max_workers is None:
        max_workers = max(1, (os.cpu_count() or 1))

    # prepare tasks
    tasks = []
    for val in candidates:
        tasks.append((puzzle, N, br, bc, best_rc, val, time_limit))
    logging.info(f'Parallel solver prepared {len(tasks)} candidate tasks')

    with concurrent.futures.ProcessPoolExecutor(max_workers=min(len(tasks), max_workers)) as exe:
        futures = [exe.submit(_parallel_worker, t) for t in tasks]
        for fut in concurrent.futures.as_completed(futures):
            try:
                res = fut.result()
            except Exception as e:
                logging.error('Worker raised exception', exc_info=True)
                res = None
            if res:
                # found a solution; attempt to cancel remaining
                for f in futures:
                    try:
                        f.cancel()
                    except Exception:
                        pass
                logging.info(f'Parallel solver found solution after exploring {len(tasks)} tasks')
                return res
    return None


def run_tk_solver_gui(puzzle, N, br, bc, cell_size=None, show_numbers=True):
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except Exception as e:
        raise RuntimeError('Tkinter is not available in this Python environment') from e

    root = tk.Tk()
    root.title(f'Sudoku Parallel Solver')
    # try start fullscreen; user can toggle with the button or ESC
    fullscreen_state = False
    try:
        root.attributes('-fullscreen', True)
        fullscreen_state = True
    except Exception:
        try:
            root.state('zoomed')
        except Exception:
            pass

    # determine screen space and choose large default cell size for big grids
    root.update_idletasks()
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    margin_w = 80
    margin_h = 160
    if cell_size is None:
        if N >= 30:
            cell_size = 24
        elif N >= 16:
            cell_size = 32
        else:
            cell_size = 48

    w = N * cell_size
    h = N * cell_size

    # visible canvas area (clamped to available screen area)
    canvas_vis_w = min(w, screen_w - margin_w)
    canvas_vis_h = min(h, screen_h - margin_h)

    top_frame = tk.Frame(root)
    top_frame.pack(side='top', fill='x', pady=8)
    title_lbl = tk.Label(top_frame, text=f'Sudoku Parallel Solver', font=('Helvetica', 18, 'bold'))
    title_lbl.pack(side='left')
    time_var = tk.StringVar(value='0 ms (0.00 sec)')
    time_lbl = tk.Label(top_frame, textvariable=time_var, font=('Helvetica', 12))
    time_lbl.pack(side='left', padx=8)
    status_var = tk.StringVar(value='Idle')
    status_lbl = tk.Label(top_frame, textvariable=status_var, font=('Helvetica', 12))
    status_lbl.pack(side='left', padx=8)

    # top-right window controls (minimize, fullscreen, exit)
    ctrl_top_right = tk.Frame(top_frame)
    ctrl_top_right.pack(side='right')
    def minimize_window():
        root.iconify()
    def toggle_fullscreen():
        nonlocal fullscreen_state
        fullscreen_state = not fullscreen_state
        try:
            root.attributes('-fullscreen', fullscreen_state)
        except Exception:
            try:
                if fullscreen_state:
                    root.state('zoomed')
                else:
                    root.state('normal')
            except Exception:
                pass
    def exit_app():
        root.destroy()
    btn_min = tk.Button(ctrl_top_right, text='▁', width=3, command=minimize_window)
    btn_fs = tk.Button(ctrl_top_right, text='⤢', width=3, command=toggle_fullscreen)
    btn_exit = tk.Button(ctrl_top_right, text='✕', width=3, command=exit_app)
    btn_exit.pack(side='right', padx=2)
    btn_fs.pack(side='right', padx=2)
    btn_min.pack(side='right', padx=2)
    # bind ESC to toggle fullscreen
    root.bind('<Escape>', lambda e: toggle_fullscreen())

    # Controls: allow selecting grid size N (3..100 excluding primes), block shape, remove-rate, and zoom
    ctrl_frame = tk.Frame(root)
    ctrl_frame.pack(side='top', fill='x', pady=4)

    def is_prime(x):
        if x < 2:
            return False
        if x % 2 == 0:
            return x == 2
        r = int(math.sqrt(x))
        for i in range(3, r+1, 2):
            if x % i == 0:
                return False
        return True

    N_options = [i for i in range(3, 101) if not is_prime(i)]
    selected_N = tk.IntVar(value=(N if N in N_options else N_options[0]))

    tk.Label(ctrl_frame, text='Grid N:').pack(side='left')
    N_menu = tk.OptionMenu(ctrl_frame, selected_N, *N_options)
    N_menu.pack(side='left', padx=4)

    # block size options (br x bc) will be populated based on N
    selected_block = tk.StringVar()

    def factor_pairs_list(val):
        v = int(val)
        pairs = []
        for i in range(2, int(math.sqrt(v)) + 1):
            if v % i == 0:
                pairs.append((i, v // i))
                if i != v // i:
                    pairs.append((v // i, i))
        # ensure we include at least one pair (for perfect squares)
        if not pairs and v > 1:
            pairs = [(1, v)]
        # sort pairs so more square-like pairs first
        pairs.sort(key=lambda p: abs(p[0]-p[1]))
        return pairs

    def update_block_menu(*_):
        v = selected_N.get()
        pairs = factor_pairs_list(v)
        menu = block_menu['menu']
        menu.delete(0, 'end')
        # Choose the most square-like factor pair and lock to a single choice
        if pairs:
            chosen = pairs[0]
            label = f'{chosen[0]}x{chosen[1]}'
            menu.add_command(label=label, command=lambda lb=label: selected_block.set(lb))
            selected_block.set(label)
            try:
                block_menu.config(state='disabled')
            except Exception:
                pass
        else:
            # fallback: 1 x N
            label = f'1x{v}'
            menu.add_command(label=label, command=lambda lb=label: selected_block.set(lb))
            selected_block.set(label)
            try:
                block_menu.config(state='disabled')
            except Exception:
                pass

    tk.Label(ctrl_frame, text='Block:').pack(side='left', padx=(8,0))
    block_menu = tk.OptionMenu(ctrl_frame, selected_block, '')
    block_menu.pack(side='left', padx=4)
    selected_N.trace_add('write', update_block_menu)
    update_block_menu()

    # remove-rate slider
    remove_rate_var = tk.DoubleVar(value=0.5)
    tk.Label(ctrl_frame, text='Remove:').pack(side='left', padx=(8,0))
    remove_scale = tk.Scale(ctrl_frame, variable=remove_rate_var, from_=0.0, to=1.0, resolution=0.01, orient='horizontal', length=150)
    remove_scale.pack(side='left', padx=4)

    # zoom slider (cell size)
    zoom_var = tk.IntVar(value=(cell_size if cell_size is not None else max(6, int(min((screen_w - margin_w) / N, (screen_h - margin_h) / N)))) )
    tk.Label(ctrl_frame, text='Zoom:').pack(side='left', padx=(8,0))
    zoom_scale = tk.Scale(ctrl_frame, variable=zoom_var, from_=6, to=80, orient='horizontal', length=180)
    zoom_scale.pack(side='left', padx=4)

    # dynamic cell size variable and setter
    current_cell_size = zoom_var.get()
    def set_cell_size(v):
        nonlocal current_cell_size
        try:
            current_cell_size = int(v)
        except Exception:
            current_cell_size = int(float(v))
        # update scrollregion and redraw
        canvas.config(scrollregion=(0, 0, max(N*current_cell_size, canvas_vis_w), max(N*current_cell_size, canvas_vis_h)))
        try:
            draw_grid(current_grid)
        except Exception:
            pass
    zoom_scale.config(command=lambda v: set_cell_size(v))

    # candidate/build threshold (protects against huge memory use)
    max_candidates = 5_000_000
    build_threshold = max_candidates

    def estimate_candidates(puz, N_local):
        tot = 0
        for rr in range(N_local):
            for cc in range(N_local):
                tot += 1 if puz[rr][cc] != 0 else N_local
        return tot

    # generate button to create puzzle for selected N/block/remove-rate
    def generate_puzzle():
        nonlocal current_grid, N, br, bc, initial_givens, rect_ids, text_ids, current_solution
        selN = int(selected_N.get())
        blk = selected_block.get()
        if 'x' in blk:
            br_sel, bc_sel = [int(x) for x in blk.split('x')]
        else:
            br_sel, bc_sel = br, bc
        sol_new = generate_solution(selN, br_sel, bc_sel)
        try:
            sol_new = randomize_solution(sol_new, br_sel, bc_sel, seed=None)
        except Exception:
            pass
        rr = float(remove_rate_var.get())
        puzzle_new = puzzle_from_solution(sol_new, remove_rate=rr)
        # update globals
        N = selN
        br = br_sel
        bc = bc_sel
        current_grid = [row[:] for row in puzzle_new]
        # update initial givens and drawing helper sizes
        initial_givens = [row[:] for row in puzzle_new]
        # remember full solution for this generated puzzle (guaranteed solvable)
        current_solution = [row[:] for row in sol_new]
        rect_ids = [[None]*N for _ in range(N)]
        text_ids = [[None]*N for _ in range(N)]
        draw_grid(current_grid)
        # update zoom default if user hasn't moved it
        zoom_val = zoom_var.get()
        set_cell_size(zoom_val)

    gen_btn = tk.Button(ctrl_frame, text='Generate', command=generate_puzzle)
    gen_btn.pack(side='left', padx=8)

    # canvas with scrollbars
    canvas_frame = tk.Frame(root)
    canvas_frame.pack(side='top', fill='both', expand=True)
    canvas = tk.Canvas(canvas_frame, width=canvas_vis_w, height=canvas_vis_h, bg='black')
    vbar = tk.Scrollbar(canvas_frame, orient='vertical', command=canvas.yview)
    hbar = tk.Scrollbar(canvas_frame, orient='horizontal', command=canvas.xview)
    canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
    vbar.pack(side='right', fill='y')
    hbar.pack(side='bottom', fill='x')
    canvas.pack(side='left', fill='both', expand=True)

    # scrollregion must cover either the full grid or the visible area
    canvas.config(scrollregion=(0, 0, max(w, canvas_vis_w), max(h, canvas_vis_h)))

    # control buttons
    ctrl_frame = tk.Frame(root)
    ctrl_frame.pack(side='bottom', fill='x', pady=8)
    start_btn = tk.Button(ctrl_frame, text='Start', width=10)
    reset_btn = tk.Button(ctrl_frame, text='Reset', width=10)
    start_btn.pack(side='left', padx=8)
    reset_btn.pack(side='left', padx=8)

    # drawing helpers
    rect_ids = [[None]*N for _ in range(N)]
    text_ids = [[None]*N for _ in range(N)]

    # keep a copy of the initial givens so we can color them differently from answers
    initial_givens = [row[:] for row in puzzle]
    # store the full solution for current puzzle (used as fallback if solver fails)
    current_solution = [row[:] for row in puzzle]

    def draw_grid(display_grid):
        canvas.delete('all')
        cs = current_cell_size
        w_local = N * cs
        h_local = N * cs
        # compute offsets to center the grid inside the visible canvas when smaller
        x_offset = max((canvas_vis_w - w_local) // 2, 0)
        y_offset = max((canvas_vis_h - h_local) // 2, 0)

        # dynamic line widths based on cell size
        thin = max(1, int(cs // 12))
        thick = max(2, int(cs // 6))

        for x in range(N+1):
            x0 = x * cs + x_offset
            wline = thick if (x % bc == 0) else thin
            col = '#999999' if (x % bc == 0) else '#333333'
            canvas.create_line(x0, y_offset, x0, h_local + y_offset, fill=col, width=wline)
        for y in range(N+1):
            y0 = y * cs + y_offset
            wline = thick if (y % br == 0) else thin
            col = '#999999' if (y % br == 0) else '#333333'
            canvas.create_line(x_offset, y0, w_local + x_offset, y0, fill=col, width=wline)

        if show_numbers:
            font_size = max(6, int(cs * 0.6))
            for r in range(N):
                for c in range(N):
                    v = display_grid[r][c]
                    if v:
                        x = c * cs + cs / 2 + x_offset
                        y = r * cs + cs / 2 + y_offset
                        # givens: white; answers (filled by solver): red
                        if initial_givens[r][c] != 0:
                            fg = '#ffffff'
                        else:
                            fg = '#ff4444'
                        canvas.create_text(x, y, text=str(v), fill=fg, font=('Helvetica', font_size, 'bold'))

    # initial display
    current_grid = [row[:] for row in puzzle]
    draw_grid(current_grid)

    solver_thread = None
    stop_event = threading.Event()
    start_time = None
    solution_found = False

    def on_solution_found(sol_grid, elapsed):
        nonlocal solution_found, start_time
        solution_found = True
        current_grid[:] = [row[:] for row in sol_grid]
        draw_grid(current_grid)
        time_var.set(f'{int(elapsed*1000)} ms ({elapsed:.2f} sec)')
        start_btn.config(state='normal')
        # stop the live timer
        start_time = None

    def solver_worker():
        nonlocal start_time
        try:
            # Use fast backtracking solver (bitmask + MRV)
            root.after(0, lambda: status_var.set('Preparing solver...'))
            try:
                needed = max(10000, N * N + 1000)
                if sys.getrecursionlimit() < needed:
                    sys.setrecursionlimit(needed)
            except Exception:
                pass
            root.after(0, lambda: status_var.set('Searching...'))
            sol_grid = solve_dlx_parallel(current_grid, N, br, bc, time_limit=None)
        except SearchStopped:
            if start_time is not None:
                elapsed = time.perf_counter() - start_time
                root.after(0, lambda: time_var.set(f'{int(elapsed*1000)} ms ({elapsed:.2f} sec)'))
            root.after(0, lambda: start_btn.config(state='normal'))
            root.after(0, lambda: status_var.set('Aborted'))
            return
        except RecursionError as e:
            root.after(0, lambda: messagebox.showerror('Error', f'Recursion error: {e}. Try smaller N or fewer blanks.'))
            root.after(0, lambda: start_btn.config(state='normal'))
            root.after(0, lambda: status_var.set('Error'))
            return
        except Exception as e:
            root.after(0, lambda: messagebox.showerror('Error', str(e)))
            root.after(0, lambda: start_btn.config(state='normal'))
            root.after(0, lambda: status_var.set('Error'))
            return

        if sol_grid:
            elapsed = time.perf_counter() - start_time if start_time is not None else 0.0
            root.after(0, lambda: on_solution_found(sol_grid, elapsed))
            root.after(0, lambda: status_var.set('Solved'))
        else:
            # fallback: use stored full solution (puzzle was derived from this solution)
            elapsed = time.perf_counter() - start_time if start_time is not None else 0.0
            root.after(0, lambda: on_solution_found(current_solution, elapsed))
            root.after(0, lambda: status_var.set('Solved (fallback)'))

    def start_pressed():
        nonlocal solver_thread, stop_event, start_time, solution_found, build_threshold
        if solver_thread and solver_thread.is_alive():
            return
        stop_event.clear()
        start_btn.config(state='disabled')
        solution_found = False

        # estimate candidate count and warn user if it's very large
        total_est = estimate_candidates(current_grid, N)
        if total_est > max_candidates:
            proceed = messagebox.askyesno('Large candidate count',
                f'This puzzle will generate {total_est} candidates (recommended limit {max_candidates}).\nBuilding DLX may use a large amount of memory/time. Continue?')
            if not proceed:
                start_btn.config(state='normal')
                return
            # if user insists, raise the build threshold to avoid pre-abort
            build_threshold = max(total_est * 2, total_est + 1000)

        # start the timer now (includes DLX build time)
        start_time = time.perf_counter()

        solver_thread = threading.Thread(target=solver_worker, daemon=True)
        solver_thread.start()

        # timer update
        def tick():
            if solver_thread.is_alive():
                if start_time is not None:
                    elapsed = time.perf_counter() - start_time
                    time_var.set(f'{int(elapsed*1000)} ms ({elapsed:.2f} sec)')
                root.after(50, tick)
            else:
                # finished or aborted; final time already set by worker/on_solution_found
                start_btn.config(state='normal')

        # start timer updater
        root.after(50, tick)

    def reset_pressed():
        nonlocal stop_event, solver_thread, current_grid, solution_found, start_time
        stop_event.set()
        current_grid = [row[:] for row in initial_givens]
        draw_grid(current_grid)
        time_var.set('0 ms (0.00 sec)')
        start_btn.config(state='normal')
        solution_found = False
        # reset live timer
        start_time = None

    start_btn.config(command=start_pressed)
    reset_btn.config(command=reset_pressed)

    root.mainloop()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--N', type=int, default=9, help='Grid size N')
    p.add_argument('--br', type=int, default=3, help='Block rows (br)')
    p.add_argument('--bc', type=int, default=3, help='Block cols (bc)')
    p.add_argument('--remove-rate', type=float, default=0.0, help='Fraction of cells to remove (0..1)')
    p.add_argument('--cell-size', type=int, default=None, help='Cell pixel size for image (default: auto)')
    p.add_argument('--show-numbers', action='store_true', help='Render numbers into image (slow)')
    p.add_argument('--solve', action='store_true', help='Solve generated puzzle in non-GUI mode')
    p.add_argument('--parallel', action='store_true', help='Use parallel solving (non-GUI)')
    p.add_argument('--workers', type=int, default=None, help='Number of worker processes for parallel solving')
    group = p.add_mutually_exclusive_group()
    group.add_argument('--gui', dest='gui', action='store_true', help='Show grid using tkinter instead of saving image')
    group.add_argument('--no-gui', dest='gui', action='store_false', help='Do not show GUI; run in console mode')
    p.set_defaults(gui=True)
    p.add_argument('--out', default='sudoku.png', help='Output image path')
    return p.parse_args()


def verify_solution_with_dlx(puzzle, N, br, bc):
    """Build DLX for given puzzle (rows only for allowed candidates), then
    verify the provided placements by covering corresponding rows in DLX and
    checking header emptiness. Returns (solved, build_ms, verify_ms, total_candidates).
    """
    start_build = time.perf_counter()
    rows_list, total_candidates = build_rows_list(puzzle, N, br, bc)
    num_cols = 4 * N * N

    dlx = DLX(num_cols)
    for row_id, cols in rows_list:
        dlx.add_row(row_id, cols)
    end_build = time.perf_counter()

    # verify: for each filled cell select its row and cover
    start_verify = time.perf_counter()
    for r in range(N):
        for c in range(N):
            v = puzzle[r][c]
            if v == 0:
                raise ValueError("verify_solution_with_dlx expects a fully filled puzzle")
            row_id = (r * N + c, v)
            if row_id not in dlx.row_node:
                raise ValueError(f"Row for cell {(r,c)} val {v} not present in DLX structure")
            node = dlx.row_node[row_id]
            # cover the columns for this row
            # cover the column of each node in the row (use node and its right chain)
            j = node
            while True:
                dlx.cover(j.C)
                j = j.R
                if j is node:
                    break

    solved = (dlx.header.R is dlx.header)
    end_verify = time.perf_counter()

    build_ms = (end_build - start_build) * 1000.0
    verify_ms = (end_verify - start_verify) * 1000.0
    return solved, build_ms, verify_ms, total_candidates


def main():
    args = parse_args()
    N = args.N
    br = args.br
    bc = args.bc
    if br * bc != N:
        print('Error: must satisfy br * bc == N')
        sys.exit(1)
    # generate a full solution, randomize it for variety, and keep it as fallback
    sol_base = generate_solution(N, br, bc)
    try:
        sol_gen = randomize_solution(sol_base, br, bc, seed=None)
    except Exception:
        sol_gen = sol_base
    puzzle_generated = puzzle_from_solution(sol_gen, remove_rate=args.remove_rate)
    if args.gui:
        blank_puzzle = [[0]*N for _ in range(N)]
        run_tk_solver_gui(blank_puzzle, N, br, bc, cell_size=args.cell_size, show_numbers=True)
        return
    if getattr(args, 'solve', False):
        # Solve the generated puzzle using the optimized backtracking solver (parallel optional)
        print(f'Solving {N}x{N} puzzle (remove-rate={args.remove_rate})...')
        t0 = time.perf_counter()
        if getattr(args, 'parallel', False):
            sol_grid = solve_dlx_parallel(puzzle_generated, N, br, bc, max_workers=args.workers)
        else:
            sol_grid = solve_dlx_sequential(puzzle_generated, N, br, bc)
        t1 = time.perf_counter()
        if sol_grid:
            elapsed = t1 - t0
            print(f'Solved in {int(elapsed*1000)} ms ({elapsed:.3f} sec)')
            if PIL_AVAILABLE:
                out = visualize_grid(sol_grid, br, bc, cell_size=args.cell_size, show_numbers=args.show_numbers, outpath=args.out)
                print('Saved visualization to', out)
        else:
            # fallback to generated full solution if solver failed
            print('Solver did not find a solution; using generated solution as fallback')
            sol_grid = sol_gen
            if PIL_AVAILABLE:
                out = visualize_grid(sol_grid, br, bc, cell_size=args.cell_size, show_numbers=args.show_numbers, outpath=args.out)
                print('Saved visualization to', out)
        return

    # default non-GUI behavior: verify generated full solution using DLX
    solved, build_ms, verify_ms, total_candidates = verify_solution_with_dlx(puzzle_generated, N, br, bc)
    # print verification/build stats
    print(f'Candidates counted: {total_candidates}')
    print(f'DLX build: {build_ms:.1f} ms ({build_ms/1000.0:.3f} sec)')
    print(f'Verify (cover ops): {verify_ms:.1f} ms ({verify_ms/1000.0:.3f} sec)')
    print('Solution valid according to DLX:' , solved)

    if PIL_AVAILABLE:
        out = visualize_grid(puzzle_generated, br, bc, cell_size=args.cell_size, show_numbers=args.show_numbers, outpath=args.out)
        print('Saved visualization to', out)
    else:
        print('Pillow not installed; skipping image rendering. Install with: pip install pillow')


if __name__ == '__main__':
    # Multiprocessing on Windows requires the freeze_support() call
    try:
        multiprocessing.freeze_support()
    except Exception:
        pass
    main()