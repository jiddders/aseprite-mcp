from ..core.workspace import workspace
import json
import os
from ..core.commands import AsepriteCommand, lua_escape
from ..core.lua import FIND_LAYER, ISOLATE_LAYER
from ..core.region import parse_region
from .. import mcp

# Render a frame of the (cloned, flattened) sprite into a canvas-sized
# RGB image. Used by the analysis tools so layer offsets and trimmed
# cels do not skew results.
_FLATTEN_FRAME = """
local function flatten_frame(clone, frame_idx, bg)
    local layer = clone.layers[#clone.layers]
    local img = Image(clone.width, clone.height, ColorMode.RGB)
    if bg then
        for py = 0, img.height - 1 do
            for px = 0, img.width - 1 do
                img:putPixel(px, py, bg)
            end
        end
    end
    local cel = layer:cel(clone.frames[frame_idx])
    if cel then
        img:drawImage(cel.image, cel.position)
    end
    return img
end
"""

# Define render_frame(n): frame n as a canvas-sized RGB image, drawn with
# Image:drawSprite, which honours layer visibility, opacity and blend modes
# (Sprite:flatten keeps hidden layers). With layer_name, a clone shows only
# that layer or group, hidden or not; the sprite itself is never changed.
def _render_source(layer_name: str) -> str:
    safe_layer = lua_escape(layer_name)
    return f"""
    local source = spr
    if "{safe_layer}" ~= "" then
        source = Sprite(spr)
        local target = find_layer(source, "{safe_layer}")
        if not target then print("ERROR:Layer not found") return end
        isolate_layer(source.layers, target)
    end
    local function render_frame(frame_idx)
        local img = Image(source.width, source.height, ColorMode.RGB)
        img:drawSprite(source, frame_idx)
        return img
    end
    """


def _parse_frame_list(frames: str) -> list[int] | str:
    """Parse "", "3", "1-4" or "1,3,6-8" into frame numbers ([] means all).

    The upper bound is checked in Lua, where the frame count is known.
    """
    if not frames.strip():
        return []
    picked = []
    for part in frames.split(","):
        part = part.strip()
        try:
            a, b = (int(v) for v in part.split("-", 1)) if "-" in part else (int(part), int(part))
        except ValueError:
            return f"Invalid frames '{frames}': use e.g. \"3\", \"1-4\" or \"1,3,6-8\""
        if a < 1 or a > b:
            return f"Invalid range '{part}': frames start at 1 and the first must not be after the last"
        if len(picked) + (b - a + 1) > 1000:
            return "Too many frames; ask for at most 1000"
        picked.extend(range(a, b + 1))
    return list(dict.fromkeys(picked))


def _read_pixels(
    filename: str,
    frame_index: int = 1,
    layer_name: str = "",
    region: tuple[int, int, int, int] | None = None,
):
    """Read RGBA pixels of a frame, composited, or of one layer when named.

    Returns (x, y, rows) where rows is a list of rows of (r, g, b, a)
    tuples covering the region (the whole sprite when region is None;
    pixels outside the canvas read as transparent), or an error string.
    """
    x, y, w, h = region or (0, 0, 0, 0)
    script = f"""
    {FIND_LAYER}
    {ISOLATE_LAYER}
    local spr = app.activeSprite
    if not spr then print("ERROR:No active sprite") return end
    local idx = {frame_index}
    if idx < 1 or idx > #spr.frames then print("ERROR:Frame index out of range") return end
    {_render_source(layer_name)}
    local img = render_frame(idx)
    local x0, y0, w, h = {x}, {y}, {w}, {h}
    if w == 0 then x0, y0, w, h = 0, 0, img.width, img.height end
    print(string.format("ORIGIN:%d,%d", x0, y0))
    local pc = app.pixelColor
    for py = y0, y0 + h - 1 do
        local cells = {{}}
        for px = x0, x0 + w - 1 do
            local cell = "."
            if px >= 0 and py >= 0 and px < img.width and py < img.height then
                local v = img:getPixel(px, py)
                local a = pc.rgbaA(v)
                if a > 0 then
                    cell = string.format("%02x%02x%02x%02x", pc.rgbaR(v), pc.rgbaG(v), pc.rgbaB(v), a)
                end
            end
            cells[#cells + 1] = cell
        end
        print("ROW:" .. table.concat(cells, " "))
    end
    """
    success, output = AsepriteCommand.execute_lua_script_checked(script, filename)
    if not success:
        return f"Failed to read pixels: {output}"
    origin, rows = (0, 0), []
    for line in output.splitlines():
        if line.startswith("ORIGIN:"):
            origin = tuple(int(v) for v in line[len("ORIGIN:"):].split(","))
        elif line.startswith("ROW:"):
            rows.append([
                (0, 0, 0, 0) if c == "." else tuple(int(c[i:i + 2], 16) for i in (0, 2, 4, 6))
                for c in line[len("ROW:"):].split(" ")
            ])
    if not rows:
        return "No pixel data returned"
    return origin[0], origin[1], rows


@mcp.tool()
async def render_onion_skin(
    filename: str,
    frame_index: int,
    output_filename: str,
    before: int = 1,
    after: int = 1,
    scale: int = 4,
    ghost_opacity: int = 100,
) -> str:
    """Render a frame with neighboring frames as translucent onion-skin ghosts.

    Produces a PNG of the given frame composited over ghosted copies of
    the surrounding frames — the batch-mode equivalent of Aseprite's
    onion skinning. Essential for checking motion continuity while
    animating: export it, open the PNG, and verify the in-between
    positions line up.

    Args:
        filename: Aseprite file to read
        frame_index: Frame to render, starting at 1
        output_filename: Output PNG path
        before: Number of previous frames to ghost (default 1)
        after: Number of following frames to ghost (default 1)
        scale: Integer nearest-neighbor scale factor (default 4)
        ghost_opacity: Opacity of ghost frames, 0-255 (default 100)
    """
    filename = workspace.input_file(filename)
    output_filename = workspace.output_file(output_filename)
    if not os.path.exists(filename):
        return f"File {filename} not found"
    if scale < 1 or scale > 64:
        return "scale must be between 1 and 64"
    if not (0 <= ghost_opacity <= 255):
        return "ghost_opacity must be between 0 and 255"
    if before < 0 or after < 0:
        return "before and after must be >= 0"
    if not output_filename.lower().endswith(".png"):
        output_filename = f"{output_filename}.png"
        output_filename = workspace.output_file(output_filename)

    safe_out = lua_escape(os.path.abspath(output_filename).replace("\\", "/"))
    script = f"""
    {_FLATTEN_FRAME}
    local spr = app.activeSprite
    if not spr then print("ERROR:No active sprite") return end

    local idx = {frame_index}
    if idx < 1 or idx > #spr.frames then print("ERROR:Frame index out of range") return end

    local clone = Sprite(spr)
    clone:flatten()

    local white = app.pixelColor.rgba(255, 255, 255, 255)
    local comp = Image(clone.width, clone.height, ColorMode.RGB)
    for py = 0, comp.height - 1 do
        for px = 0, comp.width - 1 do
            comp:putPixel(px, py, white)
        end
    end

    -- Ghosts: oldest first so closer frames draw on top
    for offset = {before}, 1, -1 do
        local fi = idx - offset
        if fi >= 1 then
            comp:drawImage(flatten_frame(clone, fi, nil), Point(0, 0), {ghost_opacity}, BlendMode.NORMAL)
        end
    end
    for offset = {after}, 1, -1 do
        local fi = idx + offset
        if fi <= #clone.frames then
            comp:drawImage(flatten_frame(clone, fi, nil), Point(0, 0), {ghost_opacity}, BlendMode.NORMAL)
        end
    end
    comp:drawImage(flatten_frame(clone, idx, nil), Point(0, 0), 255, BlendMode.NORMAL)

    local s = {scale}
    local big = Image(comp.width * s, comp.height * s, ColorMode.RGB)
    for py = 0, comp.height - 1 do
        for px = 0, comp.width - 1 do
            local v = comp:getPixel(px, py)
            for oy = 0, s - 1 do
                for ox = 0, s - 1 do
                    big:putPixel(px * s + ox, py * s + oy, v)
                end
            end
        end
    end

    big:saveAs("{safe_out}")
    print("OK")
    """

    success, output = AsepriteCommand.execute_lua_script_checked(script, filename)
    if success:
        return (
            f"Onion-skin render of frame {frame_index} "
            f"(-{before}/+{after} ghosts) saved to {output_filename} at {scale}x"
        )
    return f"Failed to render onion skin: {output}"


@mcp.tool()
async def compare_frames(
    filename: str,
    frame_a: int,
    frame_b: int,
    layer_name: str = "",
    region: str = "",
) -> str:
    """Compare two frames and report how much they differ.

    Renders both frames as you see them (visible layers only) and diffs
    them pixel by pixel. Use while animating to confirm a frame actually
    changed, or that it did not change too much.

    Args:
        filename: Aseprite file to read
        frame_a: First frame index, starting at 1
        frame_b: Second frame index, starting at 1
        layer_name: Compare only this layer (or group), e.g. the character
            without the effects around it; "" compares all visible layers
        region: Compare only "x,y,w,h" (sprite pixels), e.g. the legs, to
            prove that part did or did not move; "" compares the whole frame

    Returns:
        JSON with changed pixel count, total pixels, percent changed,
        and the bounding box of the changed region.
    """
    filename = workspace.input_file(filename)
    if not os.path.exists(filename):
        return f"File {filename} not found"
    rect = parse_region(region)
    if isinstance(rect, str):
        return rect
    rx, ry, rw, rh = rect or (0, 0, 0, 0)

    script = f"""
    {FIND_LAYER}
    {ISOLATE_LAYER}
    local spr = app.activeSprite
    if not spr then print("ERROR:No active sprite") return end

    local a = {frame_a}
    local b = {frame_b}
    if a < 1 or a > #spr.frames or b < 1 or b > #spr.frames then
        print("ERROR:Frame index out of range") return
    end

    {_render_source(layer_name)}

    local img_a = render_frame(a)
    local img_b = render_frame(b)

    -- Region clipped to the canvas (the whole canvas when none was given).
    local x0, y0, x1, y1 = 0, 0, img_a.width - 1, img_a.height - 1
    if {rw} > 0 then
        x0, y0 = math.max(0, {rx}), math.max(0, {ry})
        x1, y1 = math.min(x1, {rx + rw - 1}), math.min(y1, {ry + rh - 1})
        if x1 < x0 or y1 < y0 then print("ERROR:Region is outside the sprite") return end
    end

    local changed = 0
    local min_x, min_y = math.huge, math.huge
    local max_x, max_y = -1, -1
    for py = y0, y1 do
        for px = x0, x1 do
            local va = img_a:getPixel(px, py)
            local vb = img_b:getPixel(px, py)
            local aa = app.pixelColor.rgbaA(va)
            local ab = app.pixelColor.rgbaA(vb)
            local diff
            if aa == 0 and ab == 0 then
                diff = false
            else
                diff = va ~= vb
            end
            if diff then
                changed = changed + 1
                if px < min_x then min_x = px end
                if py < min_y then min_y = py end
                if px > max_x then max_x = px end
                if py > max_y then max_y = py end
            end
        end
    end

    if changed == 0 then
        -- math.huge has no integer representation for %d
        min_x, min_y, max_x, max_y = 0, 0, -1, -1
    end
    print(string.format("DIFF:%d,%d,%d,%d,%d,%d,%d",
        changed, (x1 - x0 + 1) * (y1 - y0 + 1), min_x, min_y, max_x, max_y,
        (changed > 0) and 1 or 0))
    """

    success, output = AsepriteCommand.execute_lua_script_checked(script, filename)
    if not success:
        return f"Failed to compare frames: {output}"

    for line in output.splitlines():
        if line.startswith("DIFF:"):
            parts = [int(p) for p in line[len("DIFF:"):].split(",")]
            changed, total, min_x, min_y, max_x, max_y, any_change = parts
            result = {
                "frame_a": frame_a,
                "frame_b": frame_b,
                "changed_pixels": changed,
                "total_pixels": total,
                "percent_changed": round(changed / total * 100, 2) if total else 0,
            }
            if layer_name:
                result["layer"] = layer_name
            if rect:
                result["region"] = {"x": rx, "y": ry, "width": rw, "height": rh}
            if any_change:
                result["changed_bounds"] = {
                    "x": min_x,
                    "y": min_y,
                    "width": max_x - min_x + 1,
                    "height": max_y - min_y + 1,
                }
            return json.dumps(result)
    return "No diff data returned"


@mcp.tool()
async def get_bounds(filename: str, layer_name: str = "", frames: str = "") -> str:
    """Report where the drawn pixels are in each frame: their bounding box.

    Use while animating to measure instead of eyeballing: that rise and
    fall frames mirror each other, that the feet stay on the ground line,
    that a character never leaves the canvas.

    Args:
        filename: Aseprite file to read
        layer_name: Measure only this layer (or group); "" measures all
            visible layers together
        frames: Frames to measure, 1-based: "3", "1-4" or "1,3,6-8";
            "" measures every frame

    Returns:
        JSON with one entry per frame: x, y, width, height of the
        non-transparent pixels, or "empty": true for a blank frame.
    """
    filename = workspace.input_file(filename)
    if not os.path.exists(filename):
        return f"File {filename} not found"
    picked = _parse_frame_list(frames)
    if isinstance(picked, str):
        return picked
    picked_lua = ", ".join(str(n) for n in picked)

    script = f"""
    {FIND_LAYER}
    {ISOLATE_LAYER}
    local spr = app.activeSprite
    if not spr then print("ERROR:No active sprite") return end
    local picked = {{ {picked_lua} }}
    if #picked == 0 then
        for i = 1, #spr.frames do picked[i] = i end
    end
    for _, f in ipairs(picked) do
        if f > #spr.frames then
            print("ERROR:Frame " .. f .. " out of range (sprite has " .. #spr.frames .. " frames)") return
        end
    end

    {_render_source(layer_name)}

    for _, f in ipairs(picked) do
        local r = render_frame(f):shrinkBounds()
        print(string.format("BOUNDS:%d,%d,%d,%d,%d,%d", f, r.x, r.y, r.width, r.height, r.isEmpty and 1 or 0))
    end
    """

    success, output = AsepriteCommand.execute_lua_script_checked(script, filename)
    if not success:
        return f"Failed to get bounds: {output}"

    entries = []
    for line in output.splitlines():
        if line.startswith("BOUNDS:"):
            f, x, y, w, h, empty = (int(p) for p in line[len("BOUNDS:"):].split(","))
            entries.append({"frame": f, "empty": True} if empty
                           else {"frame": f, "x": x, "y": y, "width": w, "height": h})
    if not entries:
        return "No bounds data returned"
    return json.dumps({"layer": layer_name or None, "frames": entries})


@mcp.tool()
async def get_color_stats(filename: str, frame_index: int = 1, top: int = 16) -> str:
    """Report the colors used in a frame and how often each appears.

    Flattens the sprite (non-destructively) and histograms the frame.
    Use to check palette discipline: too many near-duplicate colors is
    a common pixel-art mistake.

    Args:
        filename: Aseprite file to read
        frame_index: Frame to analyze, starting at 1 (default 1)
        top: How many of the most-used colors to list (default 16)

    Returns:
        JSON with unique color count, opaque pixel count, and the top
        colors with usage counts.
    """
    filename = workspace.input_file(filename)
    if not os.path.exists(filename):
        return f"File {filename} not found"
    if top < 1:
        return "top must be >= 1"

    script = f"""
    {_FLATTEN_FRAME}
    local spr = app.activeSprite
    if not spr then print("ERROR:No active sprite") return end

    local idx = {frame_index}
    if idx < 1 or idx > #spr.frames then print("ERROR:Frame index out of range") return end

    local clone = Sprite(spr)
    clone:flatten()
    local img = flatten_frame(clone, idx, nil)

    local counts = {{}}
    local opaque = 0
    for py = 0, img.height - 1 do
        for px = 0, img.width - 1 do
            local v = img:getPixel(px, py)
            if app.pixelColor.rgbaA(v) > 0 then
                opaque = opaque + 1
                local hex = string.format("#%02X%02X%02X",
                    app.pixelColor.rgbaR(v),
                    app.pixelColor.rgbaG(v),
                    app.pixelColor.rgbaB(v))
                counts[hex] = (counts[hex] or 0) + 1
            end
        end
    end

    local unique = 0
    for hex, count in pairs(counts) do
        unique = unique + 1
        print("COLOR:" .. hex .. "," .. count)
    end
    print("OPAQUE:" .. opaque)
    print("UNIQUE:" .. unique)
    """

    success, output = AsepriteCommand.execute_lua_script_checked(script, filename)
    if not success:
        return f"Failed to get color stats: {output}"

    colors = []
    opaque = 0
    unique = 0
    for line in output.splitlines():
        if line.startswith("COLOR:"):
            hex_color, count = line[len("COLOR:"):].split(",")
            colors.append({"color": hex_color, "count": int(count)})
        elif line.startswith("OPAQUE:"):
            opaque = int(line[len("OPAQUE:"):])
        elif line.startswith("UNIQUE:"):
            unique = int(line[len("UNIQUE:"):])

    colors.sort(key=lambda c: c["count"], reverse=True)
    return json.dumps({
        "frame": frame_index,
        "unique_colors": unique,
        "opaque_pixels": opaque,
        "top_colors": colors[:top],
    })


LINT_MAX_LISTED = 50

_NEIGHBOURS_8 = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


def _pieces(rows: list[list[tuple]]) -> list[list[tuple[int, int]]]:
    """Connected groups of opaque pixels (diagonals connect), largest first."""
    h, w = len(rows), len(rows[0])
    seen = [[False] * w for _ in range(h)]
    pieces = []
    for sy in range(h):
        for sx in range(w):
            if seen[sy][sx] or rows[sy][sx][3] == 0:
                continue
            seen[sy][sx] = True
            stack, piece = [(sx, sy)], []
            while stack:
                x, y = stack.pop()
                piece.append((x, y))
                for dx, dy in _NEIGHBOURS_8:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and rows[ny][nx][3] > 0:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            pieces.append(piece)
    pieces.sort(key=len, reverse=True)
    return pieces


@mcp.tool()
async def lint_sprite(filename: str, frame_index: int = 1, layer_name: str = "") -> str:
    """Find mistakes that are easy to miss by eye, with exact coordinates.

    Reports:
    - detached: groups of pixels not connected to the main shape (diagonals
      count as connected), e.g. a mug handle or leaf floating off its object
    - lone_pixels: single opaque pixels with no opaque neighbour at all
    - orphan_pixels: pixels whose colour matches none of their 8 neighbours,
      the usual stray pixel in a fill or shading band (a deliberate 1 px
      detail such as an eye glint also shows up here: check, don't blindly fix)

    Run it on one layer for objects drawn on their own layer; on a full
    scene everything touches the background, so only orphans are useful.

    Args:
        filename: Aseprite file to check
        frame_index: Frame to check, starting at 1 (default 1)
        layer_name: Check only this layer (or group); "" checks all visible layers

    Returns:
        JSON with counts and up to 50 entries per list, smallest pieces first.
    """
    filename = workspace.input_file(filename)
    if not os.path.exists(filename):
        return f"File {filename} not found"

    result = _read_pixels(filename, frame_index, layer_name)
    if isinstance(result, str):
        return result.replace("Failed to read pixels", "Failed to lint sprite")
    _, _, rows = result
    h, w = len(rows), len(rows[0])

    pieces = _pieces(rows)
    detached, lone = [], []
    for piece in reversed(pieces[1:]):  # smallest first
        if len(piece) == 1:
            lone.append({"x": piece[0][0], "y": piece[0][1]})
            continue
        xs = [p[0] for p in piece]
        ys = [p[1] for p in piece]
        detached.append({"x": min(xs), "y": min(ys), "width": max(xs) - min(xs) + 1,
                         "height": max(ys) - min(ys) + 1, "pixels": len(piece)})

    orphans = []
    for y in range(h):
        for x in range(w):
            c = rows[y][x]
            if c[3] == 0:
                continue
            around = [rows[y + dy][x + dx] for dx, dy in _NEIGHBOURS_8
                      if 0 <= x + dx < w and 0 <= y + dy < h]
            opaque = [n for n in around if n[3] > 0]
            if opaque and c not in opaque:
                orphans.append({"x": x, "y": y, "color": "#%02X%02X%02X" % c[:3]})

    return json.dumps({
        "frame": frame_index,
        "layer": layer_name or None,
        "opaque_pixels": sum(len(p) for p in pieces),
        "pieces": len(pieces),
        "main_piece_pixels": len(pieces[0]) if pieces else 0,
        "detached_count": len(detached),
        "detached": detached[:LINT_MAX_LISTED],
        "lone_pixel_count": len(lone),
        "lone_pixels": lone[:LINT_MAX_LISTED],
        "orphan_pixel_count": len(orphans),
        "orphan_pixels": orphans[:LINT_MAX_LISTED],
    })
