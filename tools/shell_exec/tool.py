"""shell_exec — run a shell command and return its output."""

import subprocess

TOOL_SCHEMA = {
    "name": "shell_exec",
    "description": (
        "Execute a shell command and return its stdout and stderr. "
        "Non-zero exit codes are reported. Use timeout to avoid hanging."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "timeout": {
                "type": "number",
                "description": "Maximum seconds to wait (default: 30).",
                "default": 30,
            },
            "cwd": {
                "type": "string",
                "description": "Working directory. Defaults to current directory.",
            },
        },
        "required": ["command"],
    },
}


def run(command: str, timeout: float = 30, cwd: str | None = None) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=float(timeout),
            cwd=cwd or None,
        )
    except subprocess.TimeoutExpired:
        return f"[Timeout: command exceeded {timeout}s and was killed]"

    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout.rstrip())
    if result.stderr:
        parts.append(f"[stderr]\n{result.stderr.rstrip()}")
    if result.returncode != 0:
        parts.append(f"[exit code: {result.returncode}]")

    return "\n".join(parts) if parts else "(no output)"
