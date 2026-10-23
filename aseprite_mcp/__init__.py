"""Aseprite MCP - Model Context Protocol implementation for Aseprite."""

from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP


@asynccontextmanager
async def _lifespan(server):
    from .core.preview import previews

    try:
        yield
    finally:
        previews.close_all()


INSTRUCTIONS = """\
Tools for drawing and animating pixel art in Aseprite. Sprites are small (16-64 px; a scene with a \
character needs 128 px or more): work in passes and check each one.

1. Plan: write down the pixel box of each part (head, torso, legs, weapon) first; leave a 1-2 px margin.
2. Silhouette in one flat color. Use shape tools (draw_line_at, draw_ellipse_at, fill_area_at) for big \
forms and draw_rows for details: rows of characters plus a colour legend, like ASCII art. Hands, faces, \
animals and handles under ~12 px are drawn pixel by pixel with draw_rows, never built from shapes. \
Clean edges: equal steps on straight diagonals (1-1-1, 2-2-2), gradual steps on curves (1,2,3 not 1,3,1), \
no L-shaped doubles on 1 px lines, no lone pixels.
3. Palette before color: one generate_color_ramp (steps=3-4) per material, 8-16 colors in total. \
Neighbouring materials must differ in lightness, not just hue.
4. Flats, then shade from one light (top-left): highlight on the lit side, shadow on the far side and \
under overhangs (chin, arms). Pillow shading (light center, dark ring at the edge) is wrong. \
No dithering or anti-aliasing on sprites under 32 px.
5. Outline in one dark color (darkest ramp step or near-black), never lighter than the fill \
(selective outlines only on larger sprites). outline_cel adds 1 px outside the shape (region= for one object).
6. After every pass: view_sprite (region="x,y,w,h" to zoom into a detail), get_color_stats to catch \
stray colors, and lint_sprite for detached parts and stray pixels by coordinate. Fix before going on. \
If it does not read, change the shape, not the details.
7. Objects sit fully on their surfaces, away from edges, and parts stay attached: a handle touches its \
mug, leaves grow from their pot.

Reach for these before writing Lua: draw_rows / read_rows (pixels as text), draw_text (numbers, labels), \
replace_color / remap_colors_in_cel_range (hit flashes, recolours), export_animation / export_tag (GIF \
previews, scaled, on a background), compare_frames and get_bounds (did a part move? where is it?). \
Raw Lua is disabled unless the operator explicitly enables ASEPRITE_UNSAFE_LUA=1 (unrestricted host access).

Animate pose to pose: redraw each key pose (anticipation, action, follow-through) on its own frame, \
with the whole body involved and the feet on one ground line unless airborne. copy_frame, \
erase_region the part that moves, then redraw it. Tween or offset cels only for rigid objects and \
secondary motion, never as the main motion. Soft bodies (slimes, jelly) squash and stretch 30-50% \
and change shape on every frame in the air. Call animation_workflow_guide before animating \
(use_case "idle" for idle and breathing loops).
"""

mcp = FastMCP("aseprite", instructions=INSTRUCTIONS, lifespan=_lifespan)

__version__ = "0.1.0"
__author__ = "Divyansh Singh"
