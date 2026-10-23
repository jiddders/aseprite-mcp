import os
from ..core.commands import AsepriteCommand
from .. import mcp
from ..core.workspace import workspace, UNSAFE_LUA


async def run_lua_script(script: str, filename: str = "") -> str:
    """Execute arbitrary Aseprite Lua code in batch mode (last resort).

    Reach for a dedicated tool first: draw_rows / read_rows to place and
    read pixels as text, draw_text for numbers and labels, replace_color or
    remap_colors_in_cel_range to recolour, export_animation or export_tag
    for GIFs, compare_frames and get_bounds to measure motion, view_sprite
    to look. Use Lua only for what none of them covers, such as a custom
    procedural effect. The full API is documented at
    https://www.aseprite.org/api/.

    Essentials for batch-mode scripts:
    - When filename is given, it is opened first and available as
      app.activeSprite.
    - Changes are NOT saved automatically; call spr:saveAs(spr.filename)
      at the end if you modified the sprite.
    - print() is the only way to return data; a clean exit does not mean
      your logic succeeded, so print what you need to verify.
    - Coordinates on cel images are cel-local: offset sprite-global
      coordinates by cel.position before getPixel/putPixel.

    WARNING: this executes unrestricted code (including io/os access)
    on the host running Aseprite. Only pass scripts you trust.

    Args:
        script: Lua source code to execute
        filename: Optional Aseprite file to open before running
    """
    if not UNSAFE_LUA:
        return "Raw Lua is disabled; operator must explicitly set ASEPRITE_UNSAFE_LUA=1"
    if filename:
        filename = workspace.input_file(filename)
    if not script.strip():
        return "Script cannot be empty"
    if filename and not os.path.exists(filename):
        return f"File {filename} not found"

    success, output = AsepriteCommand.execute_lua_script(script, filename or None)
    if success:
        return output if output.strip() else "Script executed (no output printed)"
    return f"Script failed: {output}"


if UNSAFE_LUA:
    mcp.tool()(run_lua_script)
