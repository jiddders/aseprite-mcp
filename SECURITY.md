# Security boundaries

This fork repairs findings in upstream commit `a89b205bed31930ee141edf319e78c71ef16bba6`.

| Finding | Change |
|---|---|
| Lua injection through nested drawing coordinates and quality tool layer names | Require integer coordinates, escape interpolated strings, and encode control characters safely. |
| Broad tool file access | Validate ordinary tool paths against a fixed workspace, reject traversal and symlink descendants, validate bitmap descriptor children, and keep temporary files inside the workspace. Exports use controlled filenames instead of layer names. |
| Unowned preview processes and broad listener exposure | Run an owned in-process listener on numeric loopback, serve regular files using descriptor-relative no-follow access, and close owned listeners on shutdown. No PID files or process signals are used. |
| Outdated dependencies | Update and lock compatible dependencies. The 2026-10-24 OSV query returned no known matches for the 43 locked package versions, including development dependencies. This is a point-in-time result. |

## Configuration

Set `ASEPRITE_WORKSPACE` to a dedicated asset folder and `ASEPRITE_PATH` to the Aseprite executable. The root is fixed at process startup. Without an explicit workspace, the initial working directory is used. The server does not read `.env` automatically.

Paths inside the workspace must not contain traversal components, symlink descendants, control characters, or filename template characters. Fonts and their bitmap sheets must also be inside it. Put discoverable fonts in `fonts/` under that root. Some formerly accepted paths and exports intentionally fail validation.

Raw Lua is disabled by default, including direct calls. `ASEPRITE_UNSAFE_LUA=1` enables it at startup and grants unrestricted Lua execution. Only enable it for trusted scripts in an externally sandboxed process.

## Limits

The workspace checks are an application policy, not an operating-system sandbox. Ordinary tool validation does not protect against a hostile process concurrently replacing filesystem entries, native Aseprite or image/font parser vulnerabilities, or unrestricted Lua. Use an OS sandbox as an additional boundary when needed.

Preview is unauthenticated local HTTP. Serve only assets intended for preview, never secrets. Loopback clients can read the served files. HTML files are active browser content. The listener does not sanitize them. The server rejects unexpected Host authorities and cross-origin Origin headers; this does not authenticate local clients.

Aseprite commands have a 120-second timeout. Output capture and file sizes have no enforced quota. Avoid untrusted or excessively large assets.

Verification was performed on macOS with Python 3.13 and native Aseprite. Preview requires POSIX descriptor-relative file access and fails closed if unavailable. Windows compatibility and the inherited Docker setup have not been verified.

## Verification

Run the full suite with the native executable available:

```sh
ASEPRITE_PATH=/path/to/aseprite .venv/bin/python -m pytest -q
```

Tests allocate a unique workspace before package imports and remove only that owned directory. Security coverage includes nested Lua inputs, tool path inventory, traversal, symlinks, exports, font descriptor children, raw Lua registration, preview ownership, and HTTP file access. Native integration tests exercise Aseprite behavior. A skipped font test means the workspace had no suitable TrueType font.
