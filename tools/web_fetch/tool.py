"""web_fetch — fetch a URL and return its content."""

import httpx

TOOL_SCHEMA = {
    "name": "web_fetch",
    "description": (
        "Fetch the content of a URL and return it as text. "
        "Follows redirects. Does not execute JavaScript."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL to fetch (http:// or https://).",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default: 8000).",
                "default": 8000,
            },
            "timeout": {
                "type": "number",
                "description": "Request timeout in seconds (default: 20).",
                "default": 20,
            },
        },
        "required": ["url"],
    },
}


def run(url: str, max_chars: int = 8000, timeout: float = 20) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
            "Gecko/20100101 Firefox/120.0"
        )
    }
    with httpx.Client(timeout=float(timeout), follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        text = resp.text
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[… response truncated at {max_chars} characters]"
    return text
