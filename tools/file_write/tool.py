"""file_write — write or append text content to a local file."""

TOOL_SCHEMA = {
    "name": "file_write",
    "description": (
        "Write or append text content to a local file. "
        "Mode 'w' overwrites, mode 'a' appends."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative path to the destination file.",
            },
            "content": {
                "type": "string",
                "description": "The text content to write.",
            },
            "mode": {
                "type": "string",
                "description": "'w' to overwrite (default), 'a' to append.",
                "enum": ["w", "a"],
                "default": "w",
            },
            "encoding": {
                "type": "string",
                "description": "Character encoding (default: utf-8).",
                "default": "utf-8",
            },
        },
        "required": ["path", "content"],
    },
}


def run(path: str, content: str, mode: str = "w", encoding: str = "utf-8") -> str:
    if mode not in ("w", "a"):
        raise ValueError(f"Invalid mode '{mode}'. Use 'w' or 'a'.")
    with open(path, mode, encoding=encoding) as fh:
        fh.write(content)
    action = "Written" if mode == "w" else "Appended"
    return f"{action} {len(content)} characters to '{path}'."
