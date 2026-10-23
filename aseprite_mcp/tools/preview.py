from .. import mcp
from ..core.preview import previews
from ..core.workspace import workspace


@mcp.tool()
async def start_preview_server(directory: str, port: int = 8000) -> str:
    """Serve regular files in a workspace directory on loopback, without listings."""
    try:
        return previews.start(workspace.input_dir(directory), port)
    except (OSError, ValueError) as exc:
        return f'Error starting preview server: {exc}'


@mcp.tool()
async def stop_preview_server(port: int = 8000) -> str:
    """Stop this process's preview on the port; repeated stops are harmless."""
    return previews.stop(port)
