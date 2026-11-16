# -*- coding: utf-8 -*-
"""
AlphaExtruderCore.py
Core image alpha contour extraction, anti-fringe edge dilation,
and 3ds Max Spline/Extrude/UVW/Material builder.
Compatible with 3ds Max 2024+ (PySide2) and 3ds Max 2025-2027+ (PySide6).
"""

import os
import math
import time
import tempfile
from collections import deque

try:
    from PySide6.QtGui import QImage
    QT_VER = 6
except ImportError:
    from PySide2.QtGui import QImage
    QT_VER = 2


def _rgba_format():
    fmt = getattr(QImage, "Format_RGBA8888", None)
    if fmt is None:
        fmt = QImage.Format.Format_RGBA8888
    return fmt


def load_image_rgba(image_path):
    """Load an image via Qt and return (width, height, rgba_bytearray, has_transparency)."""
    if not image_path or not os.path.isfile(image_path):
        raise FileNotFoundError(f"找不到图片文件: {image_path}")

    img = QImage(image_path)
    if img.isNull():
        raise ValueError(f"无法读取图片 (格式不受支持或文件损坏): {image_path}")

    img = img.convertToFormat(_rgba_format())
    w = img.width()
    h = img.height()
    bpl = img.bytesPerLine()
    raw_all = bytes(img.constBits())

    if bpl == w * 4:
        raw = bytearray(raw_all[:w * h * 4])
    else:
        raw = bytearray(w * h * 4)
        row_bytes = w * 4
        for y in range(h):
            src = y * bpl
            dst = y * row_bytes
            raw[dst:dst + row_bytes] = raw_all[src:src + row_bytes]

    alphas = bytes(raw[3::4])
    min_a = min(alphas) if alphas else 255
    has_trans = min_a < 255
    return w, h, raw, alphas, has_trans


def _find_mask_boundary_pixels(w, h, mask_bytes):
    """Return list of (x, y, is_fg) pixels along the 4-connected boundary of mask."""
    # Fast row scan: find pixels where mask==1 touches mask==0 in 4-neighborhood
    pw = w + 2
    zero_row = b"\x00" * pw
    one_row = b"\x01" * pw
    pad_m = [zero_row] + [b"\x00" + mask_bytes[y * w:(y + 1) * w] + b"\x00" for y in range(h)] + [zero_row]

    fg_boundary = []
    bg_boundary = []
    for y in range(1, h + 1):
        mr_prev = pad_m[y - 1]
        mr_curr = pad_m[y]
        mr_next = pad_m[y + 1]
        if mr_curr == zero_row and mr_prev == zero_row and mr_next == zero_row:
            continue
        if mr_curr == one_row and mr_prev == one_row and mr_next == one_row:
            continue
        ic = int.from_bytes(mr_curr, "big")
        ip = int.from_bytes(mr_prev, "big")
        inxt = int.from_bytes(mr_next, "big")
        # Horizontal neighbors differ or vertical neighbors differ
        diff = (ic ^ (ic >> 8)) | (ic ^ (ic << 8)) | (ic ^ ip) | (ic ^ inxt)
        # Mask out padded border bytes (byte 0 and byte pw-1)
        # Only inspect x in [1..w]
        iy = y - 1
        while diff:
            lsb = diff & -diff
            diff ^= lsb
            px = pw - 1 - ((lsb.bit_length() - 1) >> 3)
            if 1 <= px <= w:
                ix = px - 1
                val = mr_curr[px]
                if val:
                    if not (mr_curr[px - 1] and mr_curr[px + 1] and mr_prev[px] and mr_next[px]):
                        fg_boundary.append((ix, iy))
                else:
                    if mr_curr[px - 1] or mr_curr[px + 1] or mr_prev[px] or mr_next[px]:
                        bg_boundary.append((ix, iy))
    return fg_boundary, bg_boundary


def apply_contour_offset(w, h, alphas, thresh, offset_px):
    """
    Offset the alpha contour by `offset_px` pixels using narrow-band Euclidean BFS.
    offset_px > 0 expands outward; offset_px < 0 shrinks inward.
    Returns a new bytes-like alpha array of length w*h.
    """
    if abs(offset_px) < 1e-3:
        return alphas

    tbl = bytes(1 if a >= thresh else 0 for a in range(256))
    mask = alphas.translate(tbl)
    fg_b, bg_b = _find_mask_boundary_pixels(w, h, mask)

    rad = abs(offset_px)
    max_r = int(math.ceil(rad)) + 2
    max_r2 = max_r * max_r

    if offset_px > 0:
        # Expand into mask == 0
        out = bytearray(255 if m else 0 for m in mask)
        dist2 = {}
        q = deque()
        for sx, sy in fg_b:
            q.append((sx, sy, sx, sy))
        while q:
            x, y, sx, sy = q.popleft()
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    nidx = ny * w + nx
                    if mask[nidx] == 0:
                        d2 = (nx - sx) * (nx - sx) + (ny - sy) * (ny - sy)
                        if d2 <= max_r2:
                            prev = dist2.get(nidx)
                            if prev is None or d2 < prev:
                                dist2[nidx] = d2
                                q.append((nx, ny, sx, sy))
        for nidx, d2 in dist2.items():
            d = math.sqrt(d2)
            # Smooth transition around d == rad (mapped to thresh)
            val = int(round(thresh + (rad - d) * 64.0))
            if val < 0:
                val = 0
            elif val > 255:
                val = 255
            out[nidx] = val
        return bytes(out)
    else:
        # Shrink into mask == 1
        out = bytearray(255 if m else 0 for m in mask)
        dist2 = {}
        q = deque()
        for sx, sy in bg_b:
            q.append((sx, sy, sx, sy))
        while q:
            x, y, sx, sy = q.popleft()
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    nidx = ny * w + nx
                    if mask[nidx] == 1:
                        d2 = (nx - sx) * (nx - sx) + (ny - sy) * (ny - sy)
                        if d2 <= max_r2:
                            prev = dist2.get(nidx)
                            if prev is None or d2 < prev:
                                dist2[nidx] = d2
                                q.append((nx, ny, sx, sy))
        for nidx, d2 in dist2.items():
            d = math.sqrt(d2)
            val = int(round(thresh + (d - rad) * 64.0))
            if val < 0:
                val = 0
            elif val > 255:
                val = 255
            out[nidx] = val
        return bytes(out)


def polygon_signed_area(pts):
    """Signed area of a 2D polygon. In image coords (+x right, +y down):
    outer islands have negative area; internal holes have positive area."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def extract_contours(w, h, alphas, thresh=128, min_island_area=25.0, min_hole_area=25.0, keep_holes=True):
    """
    Fast subpixel Marching Squares over alpha channel.
    Returns (outer_loops, hole_loops, fg_seed_pixels).
    Each point in loops is (px, py) in continuous image pixel space [0..W] x [0..H],
    where pixel (x, y) center is at (x + 0.5, y + 0.5).
    """
    tbl = bytes(1 if a >= thresh else 0 for a in range(256))
    mask = alphas.translate(tbl)

    pw = w + 2
    zero_row = b"\x00" * pw
    one_row = b"\x01" * pw
    pad_a = [zero_row] + [b"\x00" + alphas[y * w:(y + 1) * w] + b"\x00" for y in range(h)] + [zero_row]
    pad_m = [zero_row] + [b"\x00" + mask[y * w:(y + 1) * w] + b"\x00" for y in range(h)] + [zero_row]

    t_val = thresh - 0.5

    def interp(a0, a1):
        d = a1 - a0
        if d == 0:
            return 0.5
        t = (t_val - a0) / d
        if t < 0.02:
            return 0.02
        if t > 0.98:
            return 0.98
        return t

    segments = {}
    seed_set = set()

    for y in range(h + 1):
        mr0 = pad_m[y]
        mr1 = pad_m[y + 1]
        if (mr0 == zero_row and mr1 == zero_row) or (mr0 == one_row and mr1 == one_row):
            continue
        i0 = int.from_bytes(mr0, "big")
        i1 = int.from_bytes(mr1, "big")
        diff = (i0 ^ (i0 >> 8)) | (i1 ^ (i1 >> 8)) | (i0 ^ i1)
        ar0 = pad_a[y]
        ar1 = pad_a[y + 1]

        while diff:
            lsb = diff & -diff
            diff ^= lsb
            x = pw - 2 - ((lsb.bit_length() - 1) >> 3)
            if x < 0 or x >= pw - 1:
                continue
            mTL = mr0[x]
            mTR = mr0[x + 1]
            mBR = mr1[x + 1]
            mBL = mr1[x]
            code = (mTL << 3) | (mTR << 2) | (mBR << 1) | mBL
            if code == 0 or code == 15:
                continue

            # Collect foreground pixel coordinates (in unpadded [0..w-1] x [0..h-1]) for fast edge dilation
            if mTL and 1 <= x <= w and 1 <= y <= h:
                seed_set.add((x - 1, y - 1))
            if mTR and 1 <= x + 1 <= w and 1 <= y <= h:
                seed_set.add((x, y - 1))
            if mBR and 1 <= x + 1 <= w and 1 <= y + 1 <= h:
                seed_set.add((x, y))
            if mBL and 1 <= x <= w and 1 <= y + 1 <= h:
                seed_set.add((x - 1, y))

            aTL = ar0[x]
            aTR = ar0[x + 1]
            aBR = ar1[x + 1]
            aBL = ar1[x]

            e_top = (0, x, y)
            e_right = (1, x + 1, y)
            e_bottom = (0, x, y + 1)
            e_left = (1, x, y)

            if code == 1:
                segments[e_bottom] = ((x + interp(aBL, aBR) - 0.5, y + 0.5), e_left)
            elif code == 2:
                segments[e_right] = ((x + 0.5, y + interp(aTR, aBR) - 0.5), e_bottom)
            elif code == 3:
                segments[e_right] = ((x + 0.5, y + interp(aTR, aBR) - 0.5), e_left)
            elif code == 4:
                segments[e_top] = ((x + interp(aTL, aTR) - 0.5, y - 0.5), e_right)
            elif code == 6:
                segments[e_top] = ((x + interp(aTL, aTR) - 0.5, y - 0.5), e_bottom)
            elif code == 7:
                segments[e_top] = ((x + interp(aTL, aTR) - 0.5, y - 0.5), e_left)
            elif code == 8:
                segments[e_left] = ((x - 0.5, y + interp(aTL, aBL) - 0.5), e_top)
            elif code == 9:
                segments[e_bottom] = ((x + interp(aBL, aBR) - 0.5, y + 0.5), e_top)
            elif code == 11:
                segments[e_right] = ((x + 0.5, y + interp(aTR, aBR) - 0.5), e_top)
            elif code == 12:
                segments[e_left] = ((x - 0.5, y + interp(aTL, aBL) - 0.5), e_right)
            elif code == 13:
                segments[e_bottom] = ((x + interp(aBL, aBR) - 0.5, y + 0.5), e_right)
            elif code == 14:
                segments[e_left] = ((x - 0.5, y + interp(aTL, aBL) - 0.5), e_bottom)
            elif code == 5:
                center_on = (aTL + aTR + aBR + aBL) >= 4 * thresh
                pT = (x + interp(aTL, aTR) - 0.5, y - 0.5)
                pB = (x + interp(aBL, aBR) - 0.5, y + 0.5)
                if center_on:
                    segments[e_top] = (pT, e_left)
                    segments[e_bottom] = (pB, e_right)
                else:
                    segments[e_top] = (pT, e_right)
                    segments[e_bottom] = (pB, e_left)
            elif code == 10:
                center_on = (aTL + aTR + aBR + aBL) >= 4 * thresh
                pR = (x + 0.5, y + interp(aTR, aBR) - 0.5)
                pL = (x - 0.5, y + interp(aTL, aBL) - 0.5)
                if center_on:
                    segments[e_right] = (pR, e_top)
                    segments[e_left] = (pL, e_bottom)
                else:
                    segments[e_right] = (pR, e_bottom)
                    segments[e_left] = (pL, e_top)

    outer_loops = []
    hole_loops = []
    while segments:
        start_e, (pt, nxt) = segments.popitem()
        loop = [pt]
        while nxt != start_e:
            pt, nxt = segments.pop(nxt)
            loop.append(pt)
        if len(loop) < 3:
            continue
        area = polygon_signed_area(loop)
        if area < 0.0:
            if -area >= min_island_area:
                outer_loops.append(loop)
        else:
            if keep_holes and area >= min_hole_area:
                hole_loops.append(loop)

    # Sort largest first
    outer_loops.sort(key=lambda l: abs(polygon_signed_area(l)), reverse=True)
    hole_loops.sort(key=lambda l: abs(polygon_signed_area(l)), reverse=True)
    return outer_loops, hole_loops, seed_set


def _rdp_open(pts, eps):
    if len(pts) <= 2:
        return pts
    eps2 = eps * eps
    keep = [False] * len(pts)
    keep[0] = True
    keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 - i0 <= 1:
            continue
        x0, y0 = pts[i0]
        x1, y1 = pts[i1]
        dx = x1 - x0
        dy = y1 - y0
        len2 = dx * dx + dy * dy
        max_d2 = -1.0
        max_idx = -1
        for i in range(i0 + 1, i1):
            px, py = pts[i]
            if len2 == 0.0:
                d2 = (px - x0) ** 2 + (py - y0) ** 2
            else:
                t = ((px - x0) * dx + (py - y0) * dy) / len2
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                qx = x0 + t * dx
                qy = y0 + t * dy
                d2 = (px - qx) ** 2 + (py - qy) ** 2
            if d2 > max_d2:
                max_d2 = d2
                max_idx = i
        if max_d2 > eps2 and max_idx != -1:
            keep[max_idx] = True
            stack.append((i0, max_idx))
            stack.append((max_idx, i1))
    return [pts[i] for i in range(len(pts)) if keep[i]]


def rdp_closed(pts, eps):
    """Iterative Ramer-Douglas-Peucker simplification for closed polygon loops."""
    n = len(pts)
    if n <= 3 or eps <= 0.0:
        return pts
    x0, y0 = pts[0]
    far_idx = 0
    far_d2 = -1.0
    for i in range(1, n):
        d2 = (pts[i][0] - x0) ** 2 + (pts[i][1] - y0) ** 2
        if d2 > far_d2:
            far_d2 = d2
            far_idx = i
    if far_idx == 0 or far_idx == n - 1:
        far_idx = n // 2
    p1 = _rdp_open(pts[:far_idx + 1], eps)
    p2 = _rdp_open(pts[far_idx:] + [pts[0]], eps)
    return p1[:-1] + p2[:-1]


def chaikin_closed(pts, iterations=1):
    """Chaikin corner-cutting for closed polygons (stays inside convex hull, never overshoots)."""
    cur = pts
    for _ in range(iterations):
        n = len(cur)
        if n < 3:
            break
        nxt = []
        for i in range(n):
            x0, y0 = cur[i]
            x1, y1 = cur[(i + 1) % n]
            nxt.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            nxt.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        cur = nxt
    return cur


def clean_loop(pts, min_seg_len=0.05):
    """Remove near-duplicate consecutive vertices from a closed loop."""
    if len(pts) < 3:
        return []
    min_d2 = min_seg_len * min_seg_len
    out = [pts[0]]
    for i in range(1, len(pts)):
        dx = pts[i][0] - out[-1][0]
        dy = pts[i][1] - out[-1][1]
        if dx * dx + dy * dy >= min_d2:
            out.append(pts[i])
    while len(out) >= 3:
        dx = out[0][0] - out[-1][0]
        dy = out[0][1] - out[-1][1]
        if dx * dx + dy * dy < min_d2:
            out.pop()
        else:
            break
    return out if len(out) >= 3 else []


def simplify_and_smooth_loops(loops, rdp_eps=0.7, smooth_iters=0):
    res = []
    for lp in loops:
        s = rdp_closed(lp, rdp_eps)
        if smooth_iters > 0:
            s = chaikin_closed(s, smooth_iters)
        s = clean_loop(s, min_seg_len=0.05)
        if len(s) >= 3:
            res.append(s)
    return res


def create_dilated_texture(w, h, raw_rgba, alphas, thresh, seed_pixels, pad_px=8, output_path=None):
    """
    Generate an edge-dilated opaque PNG texture so transparent background pixels
    do not cause white/black edge halos under bilinear filtering or on extruded side walls.
    Runs BFS strictly from boundary `seed_pixels` (O(boundary * pad_px)).
    """
    out = bytearray(raw_rgba)
    tbl = bytes(1 if a >= thresh else 0 for a in range(256))
    mask = alphas.translate(tbl)

    # State: 0 = unvisited bg, 1 = queued bg, 2 = colored
    state = bytearray(mask)  # mask==1 starts as state=1? Wait: make foreground state=2!
    for i in range(len(state)):
        if state[i]:
            state[i] = 2
            out[i * 4 + 3] = 255

    q = deque()
    for sx, sy in seed_pixels:
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nx, ny = sx + dx, sy + dy
            if 0 <= nx < w and 0 <= ny < h:
                nidx = ny * w + nx
                if state[nidx] == 0:
                    state[nidx] = 1
                    q.append((nx, ny, nidx, 1))

    neighbors8 = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1))
    neighbors4 = ((-1, 0), (1, 0), (0, -1), (0, 1))

    while q:
        x, y, idx, dist = q.popleft()
        r_sum = g_sum = b_sum = cnt = 0
        for dx, dy in neighbors8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                nidx = ny * w + nx
                if state[nidx] == 2:
                    p = nidx * 4
                    r_sum += out[p]
                    g_sum += out[p + 1]
                    b_sum += out[p + 2]
                    cnt += 1
        if cnt > 0:
            p = idx * 4
            out[p] = r_sum // cnt
            out[p + 1] = g_sum // cnt
            out[p + 2] = b_sum // cnt
            out[p + 3] = 255
            state[idx] = 2
            if dist < pad_px:
                for dx, dy in neighbors4:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        nidx = ny * w + nx
                        if state[nidx] == 0:
                            state[nidx] = 1
                            q.append((nx, ny, nidx, dist + 1))

    if output_path is None:
        tmp_dir = os.path.join(tempfile.gettempdir(), "AlphaExtruder")
        os.makedirs(tmp_dir, exist_ok=True)
        output_path = os.path.join(tmp_dir, "dilated_albedo.png")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    raw_bytes = bytes(out)
    qimg = QImage(raw_bytes, w, h, w * 4, _rgba_format())
    if not qimg.save(output_path, "PNG"):
        raise IOError(f"无法保存扩色贴图: {output_path}")
    return output_path


def process_image_to_contours(
    image_path,
    alpha_threshold=128,
    contour_offset_px=0.0,
    simplify_eps=0.7,
    smooth_iters=0,
    min_island_area=25.0,
    min_hole_area=25.0,
    keep_holes=True,
    dilate_edges=True,
    dilate_pad_px=8,
    dilated_tex_path=None,
):
    """
    Full image processing pipeline: loads PNG/image, optionally offsets contour,
    extracts subpixel contours & holes, simplifies/smooths loops, and optionally
    builds an edge-dilated anti-fringe texture.
    """
    t0 = time.perf_counter()
    w, h, raw_rgba, alphas, has_trans = load_image_rgba(image_path)
    if not has_trans:
        raise ValueError("该图片没有透明区域 (所有像素 Alpha=255)，无法自动抠图！")

    contour_alphas = apply_contour_offset(w, h, alphas, alpha_threshold, contour_offset_px)
    outer_raw, hole_raw, seed_pixels = extract_contours(
        w,
        h,
        contour_alphas,
        thresh=alpha_threshold,
        min_island_area=min_island_area,
        min_hole_area=min_hole_area,
        keep_holes=keep_holes,
    )

    if not outer_raw:
        raise ValueError("在当前 Alpha 阈值下未检测到有效图案轮廓，请尝试调低 Alpha 阈值。")

    outer_loops = simplify_and_smooth_loops(outer_raw, rdp_eps=simplify_eps, smooth_iters=smooth_iters)
    hole_loops = simplify_and_smooth_loops(hole_raw, rdp_eps=simplify_eps, smooth_iters=smooth_iters) if keep_holes else []
    all_loops = outer_loops + hole_loops

    if not all_loops:
        raise ValueError("简化后的轮廓为空，请调低简化容差或最小面积。")

    tex_path = image_path
    if dilate_edges:
        if dilated_tex_path is None:
            stem = os.path.splitext(os.path.basename(image_path))[0]
            cache_dir = os.path.join(os.path.dirname(os.path.abspath(image_path)), "_alpha_extrude_tex")
            try:
                os.makedirs(cache_dir, exist_ok=True)
                dilated_tex_path = os.path.join(cache_dir, f"{stem}_albedo.png")
            except OSError:
                dilated_tex_path = os.path.join(tempfile.gettempdir(), "AlphaExtruder", f"{stem}_albedo.png")
        tex_path = create_dilated_texture(
            w, h, raw_rgba, alphas, alpha_threshold, seed_pixels,
            pad_px=dilate_pad_px, output_path=dilated_tex_path
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    total_knots = sum(len(lp) for lp in all_loops)
    return {
        "width": w,
        "height": h,
        "outer_count": len(outer_loops),
        "hole_count": len(hole_loops),
        "total_knots": total_knots,
        "loops": all_loops,
        "texture_path": tex_path,
        "elapsed_ms": elapsed_ms,
    }


def _unique_material_name(rt, base_name):
    """Return a material name based on `base_name` that does not collide with existing scene/medit materials."""
    existing = set()
    try:
        for m in rt.sceneMaterials:
            existing.add(str(m.name))
    except Exception:
        pass
    try:
        for m in rt.meditMaterials:
            if m is not None:
                existing.add(str(m.name))
    except Exception:
        pass
    if base_name not in existing:
        return base_name
    idx = 1
    while True:
        cand = f"{base_name}_{idx:02d}"
        if cand not in existing:
            return cand
        idx += 1


def _create_bitmap_tex(rt, tex_path, map_name="AlphaExtruder_Map", use_vray_bitmap=False):
    if use_vray_bitmap and hasattr(rt, "VRayBitmap"):
        bmp = rt.VRayBitmap(name=map_name)
        bmp.HDRIMapName = tex_path
        bmp.alphaSource = 2
        bmp.coords.U_Tile = False
        bmp.coords.V_Tile = False
        bmp.coords.U_Mirror = False
        bmp.coords.V_Mirror = False
        bmp.coords.blur = 0.1
        return bmp

    bmp = rt.BitmapTexture(name=map_name)
    bmp.filename = tex_path
    bmp.alphaSource = 2  # None (Opaque) - prevents alpha cutout double-filtering
    bmp.coords.U_Tile = False
    bmp.coords.V_Tile = False
    bmp.coords.U_Mirror = False
    bmp.coords.V_Mirror = False
    bmp.coords.blur = 0.1
    return bmp


def _resolve_mat_type(rt, mat_type):
    if mat_type != "auto":
        return mat_type
    r_cls = str(rt.classOf(rt.renderers.current))
    if "V_Ray" in r_cls and hasattr(rt, "VRayMtl"):
        return "vray"
    return "physical"


def _create_single_material(rt, name, tex_path, mat_type="auto", roughness=0.45):
    chosen = _resolve_mat_type(rt, mat_type)
    mat_name = _unique_material_name(rt, name)
    map_name = f"{mat_name}_Map"
    bmp = _create_bitmap_tex(rt, tex_path, map_name=map_name, use_vray_bitmap=(chosen == "vray"))

    if chosen == "vray" and hasattr(rt, "VRayMtl"):
        mat = rt.VRayMtl(name=mat_name)
        mat.texmap_diffuse = bmp
        mat.Reflection = rt.color(20, 20, 20)
        mat.Reflection_Glossiness = max(0.05, 1.0 - roughness)
    elif chosen == "standard" and hasattr(rt, "Standardmaterial"):
        mat = rt.Standardmaterial(name=mat_name)
        mat.diffuseMap = bmp
        mat.specularLevel = 20
        mat.glossiness = int((1.0 - roughness) * 60)
    else:
        mat = rt.PhysicalMaterial(name=mat_name)
        mat.base_color_map = bmp
        mat.base_weight = 1.0
        mat.roughness = roughness
        mat.reflectivity = 0.35

    rt.showTextureMap(mat, True)
    return mat


def _create_solid_material(rt, name, rgb_tuple, mat_type="auto", roughness=0.2, acrylic=False):
    r, g, b = rgb_tuple
    col = rt.color(r, g, b)
    chosen = _resolve_mat_type(rt, mat_type)
    mat_name = _unique_material_name(rt, name)

    if chosen == "vray" and hasattr(rt, "VRayMtl"):
        mat = rt.VRayMtl(name=mat_name)
        mat.Diffuse = col
        if acrylic:
            mat.Reflection = rt.color(230, 230, 230)
            mat.Refraction = rt.color(235, 235, 235)
            mat.refraction_ior = 1.49
            mat.Reflection_Glossiness = 0.96
    elif chosen == "standard" and hasattr(rt, "Standardmaterial"):
        mat = rt.Standardmaterial(name=mat_name)
        mat.diffuse = col
        if acrylic:
            mat.opacity = 35
            mat.specularLevel = 90
            mat.glossiness = 85
    else:
        mat = rt.PhysicalMaterial(name=mat_name)
        mat.base_color = col
        mat.roughness = 0.05 if acrylic else roughness
        if acrylic:
            mat.transparency = 0.85
            mat.trans_color = rt.color(245, 250, 255)
            mat.trans_ior = 1.49
            mat.reflectivity = 0.85
    return mat


def build_extruded_cutout_in_max(
    image_path,
    node_name=None,
    material_name=None,
    alpha_threshold=128,
    contour_offset_px=0.0,
    simplify_eps=0.7,
    smooth_iters=0,
    min_island_area=25.0,
    min_hole_area=25.0,
    keep_holes=True,
    dilate_edges=True,
    dilate_pad_px=8,
    size_mode="height",     # "height" | "width" | "pixel"
    target_size=200.0,      # mm (or world units)
    extrude_depth=5.0,
    cap_type=0,             # 0=Morph, 1=Grid
    orientation="standing", # "standing" | "flat"
    material_mode="full",   # "full" | "side_solid" | "side_acrylic" | "front_only"
    side_color=(245, 245, 245),
    mat_type="auto",
    roughness=0.45,
    collapse_poly=False,
    select_result=True,
):
    """Execute full pipeline inside 3ds Max using pymxs."""
    import pymxs
    rt = pymxs.runtime

    data = process_image_to_contours(
        image_path=image_path,
        alpha_threshold=alpha_threshold,
        contour_offset_px=contour_offset_px,
        simplify_eps=simplify_eps,
        smooth_iters=smooth_iters,
        min_island_area=min_island_area,
        min_hole_area=min_hole_area,
        keep_holes=keep_holes,
        dilate_edges=dilate_edges,
        dilate_pad_px=dilate_pad_px,
    )

    w = data["width"]
    h = data["height"]
    loops = data["loops"]
    tex_path = data["texture_path"]

    if size_mode == "height":
        scale = float(target_size) / float(h)
    elif size_mode == "width":
        scale = float(target_size) / float(w)
    else:
        scale = float(target_size)

    canvas_w = w * scale
    canvas_h = h * scale
    half_w = w * 0.5
    half_h = h * 0.5

    raw_stem = os.path.splitext(os.path.basename(image_path))[0].strip() or "AlphaCutout"
    if not node_name:
        clean_stem = "".join(c if c.isalnum() or c in "_-" else "_" for c in raw_stem).strip("_") or "AlphaCutout"
        node_name = rt.uniqueName(clean_stem)
    else:
        node_name = rt.uniqueName(node_name)

    mat_base_name = (material_name or "").strip() or raw_stem

    with pymxs.undo(True, "AlphaExtruder Generate"):
        sp = rt.SplineShape(name=node_name)
        knot_corner = rt.name("corner")
        knot_line = rt.name("line")

        for idx, lp in enumerate(loops):
            s_idx = rt.addNewSpline(sp)
            for px, py in lp:
                wx = (px - half_w) * scale
                wy = (half_h - py) * scale
                rt.addKnot(sp, s_idx, knot_corner, knot_line, rt.Point3(wx, wy, 0.0))
            rt.close(sp, s_idx)

        rt.updateShape(sp)

        ext = rt.Extrude()
        ext.amount = float(extrude_depth)
        ext.capStart = True
        ext.capEnd = True
        ext.capType = int(cap_type)
        ext.mapcoords = True
        ext.matIDs = True
        ext.smooth = False
        rt.addModifier(sp, ext)

        uvw = rt.Uvwmap()
        uvw.maptype = 0  # Planar
        uvw.axis = 2     # Z axis
        uvw.realWorldMapSize = False
        uvw.width = canvas_w
        uvw.length = canvas_h
        rt.addModifier(sp, uvw)

        # CRITICAL: 3ds Max auto-centers Uvwmap gizmo on the geometry's bounding box center,
        # which shifts the texture whenever the cutout subject is asymmetric in the PNG canvas.
        # Force modifier evaluation and lock gizmo.pos back to [0, 0, 0] (the full PNG canvas center).
        rt.redrawViews()
        try:
            uvw.gizmo.pos = rt.Point3(0.0, 0.0, 0.0)
        except Exception:
            rt.select(sp)
            rt.maxModifyMode()
            rt.modPanel.setCurrentObject(uvw)
            uvw.gizmo.pos = rt.Point3(0.0, 0.0, 0.0)

        if collapse_poly:
            rt.convertToPoly(sp)

        if orientation == "standing":
            rt.rotate(sp, rt.angleAxis(90.0, rt.Point3(1.0, 0.0, 0.0)))
            # Center thickness on Y=0, put mesh bottom flush at Z=0
            rt.redrawViews()
            bb_min = sp.min
            bb_max = sp.max
            mid_y = (bb_min.y + bb_max.y) * 0.5
            sp.pos = rt.Point3(sp.pos.x, sp.pos.y - mid_y, sp.pos.z - bb_min.z)
            sp.pivot = rt.Point3(0.0, 0.0, 0.0)
        else:
            bb_min = sp.min
            sp.pos = rt.Point3(sp.pos.x, sp.pos.y, sp.pos.z - bb_min.z)
            sp.pivot = rt.Point3(0.0, 0.0, 0.0)

        # Assign material (named directly after image filename, deduplicated against scene/medit)
        if material_mode == "full":
            mat = _create_single_material(rt, mat_base_name, tex_path, mat_type=mat_type, roughness=roughness)
        else:
            mm_name = _unique_material_name(rt, mat_base_name)
            mm = rt.Multimaterial(numsubs=3, name=mm_name)
            front_mat = _create_single_material(rt, f"{mat_base_name}_正面", tex_path, mat_type=mat_type, roughness=roughness)
            is_acrylic = (material_mode == "side_acrylic")
            side_mat = _create_solid_material(rt, f"{mat_base_name}_侧面", side_color, mat_type=mat_type, acrylic=is_acrylic)
            if material_mode == "front_only":
                back_mat = side_mat
            else:
                back_mat = front_mat
            mm.materialList[0] = front_mat  # MatID 1: Top/Front Cap
            mm.materialList[1] = back_mat   # MatID 2: Bottom/Back Cap
            mm.materialList[2] = side_mat   # MatID 3: Side Walls
            mm.names[0] = "Front_Image"
            mm.names[1] = "Back_Image"
            mm.names[2] = "Extrude_Side"
            mat = mm

        sp.material = mat
        if select_result:
            rt.select(sp)
            rt.execute("max zoomext sel all")

    data["node_name"] = str(sp.name)
    data["material_name"] = str(mat.name)
    data["canvas_width"] = canvas_w
    data["canvas_height"] = canvas_h
    return data
