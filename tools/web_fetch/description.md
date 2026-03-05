# web_fetch

Fetch the content of a URL and return it as plain text.

**Parameters**
- `url` *(required)* — The full URL to fetch (must include `http://` or `https://`).
- `max_chars` *(optional, default `8000`)* — Maximum characters to return from the
  response body. Responses longer than this are truncated.
- `timeout` *(optional, default `20`)* — Request timeout in seconds.

**Returns**
The raw response body (HTML, JSON, plain text, etc.) up to `max_chars` characters,
or an error message if the request fails.

**Notes**
- Follows HTTP redirects automatically.
- Does not execute JavaScript — returns the raw HTML source for web pages.
- Set `max_chars` higher if you need more content (be mindful of context length).
