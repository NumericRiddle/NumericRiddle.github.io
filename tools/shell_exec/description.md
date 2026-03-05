# shell_exec

Execute a shell command and return its combined stdout and stderr output.

**Parameters**
- `command` *(required)* — The shell command to run (passed to `/bin/sh -c`).
- `timeout` *(optional, default `30`)* — Maximum seconds to wait before killing
  the process and returning a timeout error.
- `cwd` *(optional)* — Working directory for the command. Defaults to the
  current working directory of the chat process.

**Returns**
A string containing:
- Standard output (if any)
- Standard error prefixed with `[stderr]` (if any)
- Exit code prefixed with `[exit code: N]` if non-zero
- `(no output)` if the command produced nothing

**Security note**
Commands run with the same privileges as the chat process. Use with care.
