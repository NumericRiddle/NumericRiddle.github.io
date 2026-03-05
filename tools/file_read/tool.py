"""file_read — read a local file and return its contents."""

TOOL_SCHEMA = {
    "name": "file_read",
    "description": (
        "Read the contents of a local file and return them as a string. "
        "Truncates at max_chars to avoid flooding the context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the file.",
            },
            "encoding": {
                "type": "string",
                "description": "Character encoding (default: utf-8).",
                "default": "utf-8",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default: 32000).",
                "default": 32000,
            },
        },
        "required": ["path"],
    },
}


def run(path: str, encoding: str = "utf-8", max_chars: int = 32000) -> str:
    with open(path, encoding=encoding) as fh:
        content = fh.read(max_chars + 1)
    if len(content) > max_chars:
        content = content[:max_chars]
        content += f"\n\n[… file truncated at {max_chars} characters]"
    return content
