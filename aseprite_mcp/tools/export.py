from ..core.workspace import workspace
import glob
import json
from pathlib import Path
import os
from ..core.commands import AsepriteCommand, lua_escape
from ..core.lua import FIND_LAYER, NORMALIZE_CEL
from ..core.colors import parse_hex_color
from .. import mcp

def _save_export(args, produced=None):
    index = args.index('--save-as') + 1
    destination = workspace.output_file(args[index])
    with workspace.scratch() as directory:
        base, extension = os.path.splitext(os.path.basename(destination))
        staged = os.path.join(directory, 'render' + extension)
        command = list(args)
        command[index] = staged
        success, output = AsepriteCommand.run_command(command)
        if not success:
            return success, output
        moves = []
        for entry in Path(directory).iterdir():
            stem, suffix = os.path.splitext(entry.name)
            if suffix != extension or not (stem == 'render' or (stem.startswith('render') and stem[6:].isdigit())):
                return False, 'Unexpected native export filename'
            source = workspace.input_file(str(entry))
            target = workspace.output_file(str(Path(destination).parent / (base + stem[6:] + extension)))
            moves.append((int(stem[6:] or '0'), entry.name, source, target))
        if not moves:
            return False, 'Aseprite exited 0 but wrote no file'
        os.makedirs(workspace.output_dir(str(Path(destination).parent)), exist_ok=True)
        for _, _, source, target in sorted(moves):
            os.replace(source, target)
            if produced is not None:
                produced.append(target)
        return True, output


@mcp.tool()
async def export_sprite(filename: str, output_filename: str, format: str = "png") -> str:
    """Export the Aseprite file to another format.

    Args:
        filename: Name of the Aseprite file to export
        output_filename: Name of the output file
        format: Output format (default: "png", can be "png", "gif", "jpg", etc.)
    """
    filename = workspace.input_file(filename)
    output_filename = workspace.output_file(output_filename)
    if not os.path.exists(filename):
        return f"File {filename} not found"
    
    # Make sure format is lowercase
    format = format.lower()
    
    # Ensure output filename has the correct extension
    if not output_filename.lower().endswith(f".{format}"):
        output_filename = f"{output_filename}.{format}"
        output_filename = workspace.output_file(output_filename)
    
    # For animated exports
    if format == "gif":
        args = ["--batch", filename, "--save-as", output_filename]
        success, output = _save_export(args) if "--save-as" in args else AsepriteCommand.run_command(args)
    else:
        # For still image exports
        args = ["--batch", filename, "--save-as", output_filename]
        success, output = _save_export(args) if "--save-as" in args else AsepriteCommand.run_command(args)

    # Aseprite exits 0 even when it cannot write the requested format
    # (e.g. format="json"). Confirm a file actually appeared. A multi-frame
    # sprite saved to a still format produces frame-numbered siblings
    # (out1.png, out2.png, ...) instead of the exact name, so accept those
    # too — same convention as export_frame.
    if success:
        base, ext = os.path.splitext(output_filename)
        if not os.path.exists(output_filename) and not [workspace.input_file(p) for p in glob.glob(f"{glob.escape(base)}*{glob.escape(ext)}")]:
            success = False
            output = "Aseprite exited 0 but wrote no file (the format may not be writable via --save-as)"

    if success:
        return f"Sprite exported successfully to {output_filename}"
    else:
        return f"Failed to export sprite: {output}"

@mcp.tool()
async def copy_sprite(filename: str, output_filename: str, overwrite: bool = False) -> str:
    """Copy a sprite to a new Aseprite file.

    Args:
        filename: Name of the Aseprite file to copy
        output_filename: Name of the output .aseprite file
        overwrite: Whether to overwrite if output exists
    """
    filename = workspace.input_file(filename)
    output_filename = workspace.output_file(output_filename)
    if not os.path.exists(filename):
        return f"File {filename} not found"

    if not output_filename.lower().endswith(".aseprite"):
        output_filename = f"{output_filename}.aseprite"
        output_filename = workspace.output_file(output_filename)


    if os.path.exists(output_filename) and not overwrite:
        return f"Output file {output_filename} already exists"

    safe_path = lua_escape(output_filename.replace("\\", "/"))
    script = f"""
    local spr = app.activeSprite
    if not spr then print("ERROR:No active sprite") return end

    spr:saveAs("{safe_path}")
    print("OK")
    """

    success, output = AsepriteCommand.execute_lua_script_checked(script, filename)
    if success and not os.path.exists(output_filename):
        success = False
        output = "Aseprite exited 0 but wrote no file"
    if success:
        return f"Sprite copied to {output_filename}"
    return f"Failed to copy sprite: {output}"


@mcp.tool()
async def export_frame(
    filename: str,
    frame_index: int,
    output_filename: str,
    scale: int = 1,
) -> str:
    """Export a single frame as a PNG, optionally scaled up.

    To look at your work while drawing, use view_sprite instead: it returns
    the image directly, with a pixel grid and coordinate rulers.

    Args:
        filename: Aseprite file to export
        frame_index: Frame index starting at 1
        output_filename: Output PNG path
        scale: Integer nearest-neighbor scale factor (default 1)
    """
    filename = workspace.input_file(filename)
    output_filename = workspace.output_file(output_filename)
    if not os.path.exists(filename):
        return f"File {filename} not found"
    if scale < 1 or scale > 64:
        return "scale must be between 1 and 64"
    if not output_filename.lower().endswith(".png"):
        output_filename = f"{output_filename}.png"
        output_filename = workspace.output_file(output_filename)

    f0 = frame_index - 1  # CLI --frame-range is 0-based
    args = [
        "--batch", filename,
        "--frame-range", f"{f0},{f0}",
        "--scale", str(scale),
        "--save-as", output_filename,
    ]
    success, output = _save_export(args) if "--save-as" in args else AsepriteCommand.run_command(args)
    if not success:
        return f"Failed to export frame: {output}"

    # With multi-frame sprites Aseprite may append the frame number to
    # the filename; rename the produced file when that happens.
    if not os.path.exists(output_filename):
        base, ext = os.path.splitext(output_filename)
        candidates = sorted([workspace.input_file(p) for p in glob.glob(f"{glob.escape(base)}*{glob.escape(ext)}")])
        if candidates:
            os.replace(workspace.input_file(candidates[0]), workspace.output_file(output_filename))
        else:
            return f"Export reported success but {output_filename} was not created"
    return f"Frame {frame_index} exported to {output_filename} at {scale}x"


@mcp.tool()
async def export_spritesheet(
    filename: str,
    output_filename: str,
    sheet_type: str = "horizontal",
    data_filename: str = "",
    scale: int = 1,
    padding: int = 0,
    tag_name: str = "",
    data_format: str = "json-array",
    list_tags: bool = False,
) -> str:
    """Export frames as a sprite sheet, optionally with a JSON data file.

    Args:
        filename: Aseprite file to export
        output_filename: Output sheet image path (PNG)
        sheet_type: Layout: "horizontal", "vertical", "rows", "columns", or "packed"
        data_filename: Optional path for a JSON metadata file
        scale: Integer scale factor applied before packing (default 1)
        padding: Padding in pixels between frames (default 0)
        tag_name: Only include frames of this animation tag (default: all frames)
        data_format: JSON format for the data file: "json-array" (default) or "json-hash"
        list_tags: Include animation tag metadata in the JSON data file (default: False)
    """
    filename = workspace.input_file(filename)
    output_filename = workspace.output_file(output_filename)
    data_filename = workspace.output_file(data_filename) if data_filename else ""
    if not os.path.exists(filename):
        return f"File {filename} not found"
    if sheet_type not in ("horizontal", "vertical", "rows", "columns", "packed"):
        return "sheet_type must be one of: horizontal, vertical, rows, columns, packed"
    if scale < 1 or scale > 64:
        return "scale must be between 1 and 64"
    if padding < 0:
        return "padding must be >= 0"
    if data_format not in ("json-array", "json-hash"):
        return "data_format must be 'json-array' or 'json-hash'"
    if not output_filename.lower().endswith(".png"):
        output_filename = f"{output_filename}.png"
        output_filename = workspace.output_file(output_filename)

    args = ["--batch"]
    if tag_name:
        # Frame filters only apply to --sheet when they appear before
        # the input file; resolve the tag to a 0-based --frame-range so
        # missing tags produce a clear error.
        safe_tag = lua_escape(tag_name)
        script = f"""
        local spr = app.activeSprite
        if not spr then print("ERROR:No active sprite") return end
        for _, tag in ipairs(spr.tags) do
            if tag.name == "{safe_tag}" then
                print("RANGE:" .. (tag.fromFrame.frameNumber - 1) .. "," .. (tag.toFrame.frameNumber - 1))
                return
            end
        end
        print("ERROR:Tag not found")
        """
        ok, out = AsepriteCommand.execute_lua_script_checked(script, filename)
        if not ok:
            return f"Failed to resolve tag: {out}"
        frame_range = next(
            (line[len("RANGE:"):] for line in out.splitlines() if line.startswith("RANGE:")),
            None,
        )
        if frame_range is None:
            return "Failed to resolve tag: no range returned"
        args += ["--frame-range", frame_range]
    args.append(filename)
    if scale > 1:
        args += ["--scale", str(scale)]
    args += ["--sheet-type", sheet_type]
    if padding > 0:
        args += ["--shape-padding", str(padding)]
    if data_filename:
        args += ["--data", data_filename, "--format", data_format]
        if list_tags:
            args.append("--list-tags")
    args += ["--sheet", output_filename]

    success, output = _save_export(args) if "--save-as" in args else AsepriteCommand.run_command(args)
    if success and not os.path.exists(output_filename):
        success = False
        output = "Aseprite exited 0 but wrote no sheet file"
    if success and data_filename and not os.path.exists(data_filename):
        success = False
        output = "Aseprite exited 0 but wrote no data file"
    if success:
        msg = f"Sprite sheet exported to {output_filename} ({sheet_type})"
        if data_filename:
            msg += f" with data file {data_filename}"
        return msg
    return f"Failed to export sprite sheet: {output}"


@mcp.tool()
async def export_layers(
    filename: str,
    output_directory: str,
    include_hidden: bool = False,
) -> str:
    """Export top-level uniquely named layers to numeric PNG names and return a name map.

    Nested groups and duplicate names fail explicitly because CLI selection is ambiguous.
    """
    filename = workspace.input_file(filename)
    output_directory = workspace.output_dir(output_directory)
    script = """
    local spr = app.activeSprite
    if not spr then print("ERROR:No active sprite") return end
    local layers = {}
    for _, layer in ipairs(spr.layers) do
        if layer.isGroup then print("ERROR:Nested/group layers are unsupported for layer export") return end
        table.insert(layers, {name=layer.name, visible=layer.isVisible})
    end
    print(json.encode(layers))
    """
    success, output = AsepriteCommand.execute_lua_script_checked(script, filename)
    if not success:
        return f"Failed to export layers: {output}"
    try:
        layers = json.loads(output.strip())
        names = [layer['name'] for layer in layers]
        if len(set(names)) != len(names):
            return "Failed to export layers: duplicate layer names are unsupported"
        if any(not isinstance(name, str) or any(c in name for c in '/*?') for name in names):
            return "Failed to export layers: ambiguous CLI layer selectors are unsupported"
    except (ValueError, TypeError, KeyError):
        return "Failed to export layers: invalid layer metadata"
    os.makedirs(output_directory, exist_ok=True)
    mapping = []
    for ordinal, layer in enumerate(layers, 1):
        if not include_hidden and not layer['visible']:
            continue
        target = workspace.output_file(os.path.join(output_directory, f'layer-{ordinal:04d}.png'))
        args = ['--batch']
        if include_hidden:
            args.append('--all-layers')
        args += ['--layer', layer['name'], filename, '--save-as', target]
        produced = []
        success, output = _save_export(args, produced)
        if not success:
            return f"Failed to export layers: {output}"
        files = [os.path.basename(path) for path in produced]
        mapping.append({'files': files, 'layer': layer['name']})
    return json.dumps({'directory': output_directory, 'layers': mapping})


@mcp.tool()
async def export_tag(
    filename: str,
    tag_name: str,
    output_filename: str,
    scale: int = 1,
) -> str:
    """Export the frames of an animation tag as a GIF or PNG sequence.

    Args:
        filename: Aseprite file to export
        tag_name: Animation tag to export
        output_filename: Output path; .gif gives an animation, .png a sequence
        scale: Integer scale factor (default 1)
    """
    filename = workspace.input_file(filename)
    output_filename = workspace.output_file(output_filename)
    if not os.path.exists(filename):
        return f"File {filename} not found"
    if scale < 1 or scale > 64:
        return "scale must be between 1 and 64"

    # --tag silently exports *all* frames (exit 0) when the tag does not
    # exist, so a produced file is not proof the tag was honoured. Validate
    # the tag up front — same approach as export_spritesheet.
    safe_tag = lua_escape(tag_name)
    check = f"""
    local spr = app.activeSprite
    if not spr then print("ERROR:No active sprite") return end
    for _, tag in ipairs(spr.tags) do
        if tag.name == "{safe_tag}" then print("OK") return end
    end
    print("ERROR:Tag not found")
    """
    ok, out = AsepriteCommand.execute_lua_script_checked(check, filename)
    if not ok:
        return f"Failed to export tag: {out}"

    args = ["--batch", filename, "--tag", tag_name]
    if scale > 1:
        args += ["--scale", str(scale)]
    args += ["--save-as", output_filename]
    success, output = _save_export(args) if "--save-as" in args else AsepriteCommand.run_command(args)
    if success:
        # A multi-frame tag saved to a still format produces frame-numbered
        # siblings instead of the exact name — accept those, same convention
        # as export_sprite/export_frame.
        base, ext = os.path.splitext(output_filename)
        if not os.path.exists(output_filename) and not [workspace.input_file(p) for p in glob.glob(f"{glob.escape(base)}*{glob.escape(ext)}")]:
            success = False
            output = "Aseprite exited 0 but wrote no file"
    if success:
        return f"Tag '{tag_name}' exported to {output_filename}"
    return f"Failed to export tag: {output}"



@mcp.tool()
async def export_animation(
    filename: str,
    output_filename: str,
    tag: str = "",
    scale: int = 1,
    background: str = "",
) -> str:
    """Export the animation for sharing: a GIF (or PNG sequence), scaled, on a background.

    The sprite itself is never changed: the background is added to a
    temporary copy. Use this instead of a Lua saveCopyAs for previews.

    Args:
        filename: Aseprite file to export
        output_filename: Output path; .gif gives an animation, .png a numbered sequence
        tag: Export only this animation tag; "" exports every frame
        scale: Integer nearest-neighbour scale factor, 1-64 (default 1)
        background: Optional hex colour filled behind every frame, e.g.
            "#E9EEF2" so a dark outline reads in any viewer; "" keeps transparency
    """
    filename = workspace.input_file(filename)
    output_filename = workspace.output_file(output_filename)
    if not os.path.exists(filename):
        return f"File {filename} not found"
    if scale < 1 or scale > 64:
        return "scale must be between 1 and 64"
    if os.path.splitext(output_filename)[1].lower() not in (".gif", ".png"):
        return "output_filename must end in .gif or .png"
    bg = None
    if background:
        bg = parse_hex_color(background)
        if bg is None:
            return f"Invalid color value: {background}"

    with workspace.scratch() as tmp:
        source = filename
        if tag or bg:
            # --tag silently exports every frame when the tag is missing, so
            # check it first; build the background copy in the same run.
            copy_path = os.path.join(tmp, "export.aseprite")
            safe_copy = lua_escape(copy_path.replace("\\", "/"))
            safe_tag = lua_escape(tag)
            bg_flag = "true" if bg else "false"
            r, g, b, a = bg or (0, 0, 0, 0)
            script = f"""
            local spr = app.activeSprite
            if not spr then print("ERROR:No active sprite") return end
            local tag = "{safe_tag}"
            if tag ~= "" then
                local found = false
                for _, t in ipairs(spr.tags) do
                    if t.name == tag then found = true end
                end
                if not found then print("ERROR:Tag not found") return end
            end
            if {bg_flag} then
                local copy = Sprite(spr)
                app.activeSprite = copy
                if copy.colorMode ~= ColorMode.RGB then
                    app.command.ChangePixelFormat{{ ui=false, format="rgb" }}
                end
                local layer = copy:newLayer()
                layer.name = "export background"
                layer.stackIndex = 1
                local color = Color({r}, {g}, {b}, {a})
                for _, frame in ipairs(copy.frames) do
                    local img = Image(copy.width, copy.height, ColorMode.RGB)
                    img:clear(color)
                    copy:newCel(layer, frame, img, Point(0, 0))
                end
                copy:saveAs("{safe_copy}")
            end
            print("OK")
            """
            ok, out = AsepriteCommand.execute_lua_script_checked(script, filename)
            if not ok:
                return f"Failed to export animation: {out}"
            if bg:
                source = copy_path

        args = ["--batch", source]
        if tag:
            args += ["--tag", tag]
        if scale > 1:
            args += ["--scale", str(scale)]
        args += ["--save-as", output_filename]
        success, output = _save_export(args) if "--save-as" in args else AsepriteCommand.run_command(args)

    if success:
        # A PNG sequence is written as frame-numbered siblings of the name.
        base, ext = os.path.splitext(output_filename)
        if not os.path.exists(output_filename) and not [workspace.input_file(p) for p in glob.glob(f"{glob.escape(base)}*{glob.escape(ext)}")]:
            success = False
            output = "Aseprite exited 0 but wrote no file"
    if not success:
        return f"Failed to export animation: {output}"
    what = f"Tag '{tag}'" if tag else "Animation"
    on = f" on {background}" if bg else ""
    return f"{what} exported to {output_filename} at {scale}x{on}"

@mcp.tool()
async def import_image_as_layer(
    filename: str,
    image_path: str,
    layer_name: str,
    frame_index: int = 1,
    x: int = 0,
    y: int = 0,
) -> str:
    """Import an image file (PNG, etc.) into a layer of the sprite.

    Useful for bringing in reference images or composing pre-made parts.
    The layer is created if it does not exist. Works best when the sprite
    is in RGB color mode.

    Args:
        filename: Aseprite file to modify
        image_path: Image file to import
        layer_name: Layer to place the image on
        frame_index: Frame index starting at 1 (default 1)
        x: X position for the image's top-left corner (default 0)
        y: Y position for the image's top-left corner (default 0)
    """
    filename = workspace.input_file(filename)
    image_path = workspace.input_file(image_path)
    if not os.path.exists(filename):
        return f"File {filename} not found"
    if not os.path.exists(image_path):
        return f"Image {image_path} not found"

    safe_layer = lua_escape(layer_name)
    safe_image = lua_escape(os.path.abspath(image_path).replace("\\", "/"))
    script = f"""
    {FIND_LAYER}
    {NORMALIZE_CEL}
    local spr = app.activeSprite
    if not spr then print("ERROR:No active sprite") return end

    local idx = {frame_index}
    if idx < 1 or idx > #spr.frames then print("ERROR:Frame index out of range") return end

    local src = Image{{ fromFile = "{safe_image}" }}
    if not src then print("ERROR:Could not load image") return end

    local target = find_layer(spr, "{safe_layer}")
    app.transaction(function()
        if not target then
            target = spr:newLayer()
            target.name = "{safe_layer}"
        end
        local cel = normalize_cel(spr, target, spr.frames[idx], true)
        cel.image:drawImage(src, Point({x}, {y}))
    end)

    spr:saveAs(spr.filename)
    print("OK")
    """

    success, output = AsepriteCommand.execute_lua_script_checked(script, filename)
    if success:
        return f"Image {image_path} imported onto '{layer_name}' frame {frame_index} in {filename}"
    return f"Failed to import image: {output}"
