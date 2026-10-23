import subprocess
import os
from .workspace import workspace


def lua_escape(s: str) -> str:
    escapes = {'\n': r'\n', '\r': r'\r', '\t': r'\t'}
    return ''.join(escapes[c] if c in escapes else '\\' + c if c in ('\\', '"') else
                   f"\\{ord(c):03d}" if ord(c) < 32 or ord(c) == 127 else c
                   for c in s)


class AsepriteCommand:
    """Helper class for running Aseprite commands."""
    
    @staticmethod
    def run_command(args):
        """Run an Aseprite command with proper error handling.
        
        Args:
            args: List of command arguments
            
        Returns:
            tuple: (success, output) where success is a boolean and output is the command output
        """
        try:
            cmd = [os.getenv('ASEPRITE_PATH', 'aseprite')] + args
            result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
            return True, result.stdout
        except subprocess.CalledProcessError as e:
            return False, e.stderr
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, str(e)
    
    @staticmethod
    def execute_lua_script(script_content, filename=None):
        """Execute a Lua script in Aseprite.
        
        Args:
            script_content: Lua script code to execute
            filename: Optional filename to open before executing script
            
        Returns:
            tuple: (success, output)
        """
        if filename:
            filename = workspace.input_file(filename)
        with workspace.scratch() as directory:
            script_path = workspace.output_file(os.path.join(directory, 'script.lua'))
            with open(script_path, 'w', encoding='utf-8') as script:
                script.write(script_content)
            args = ["--batch"]
            if filename:
                args.append(filename)
            args.extend(["--script", script_path])
            return AsepriteCommand.run_command(args)

    @staticmethod
    def execute_lua_script_checked(script_content, filename=None):
        """Execute a Lua script and surface in-script errors.

        Scripts using this helper signal failure by printing a line
        starting with "ERROR:" (batch-mode scripts cannot affect the
        process exit code from Lua).

        Returns:
            tuple: (success, output) where output is the error message
            when an ERROR: line was printed, or the raw stdout otherwise.
        """
        success, output = AsepriteCommand.execute_lua_script(script_content, filename)
        if not success:
            return False, output
        for line in output.splitlines():
            if line.startswith("ERROR:"):
                return False, line[len("ERROR:"):]
        return True, output
