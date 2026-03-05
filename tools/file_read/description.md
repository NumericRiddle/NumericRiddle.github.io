# file_read

Read the contents of a file from the local filesystem and return them as a string.

**Parameters**
- `path` *(required)* — Absolute or relative path to the file.
- `encoding` *(optional, default `"utf-8"`)* — Character encoding to use when reading.
- `max_chars` *(optional, default `32000`)* — Maximum number of characters to return.
  If the file is longer it will be truncated and a notice appended.

**Returns**
The raw text content of the file, or an error message if the file cannot be read.

**Notes**
- Binary files will fail gracefully with an informative error.
- Use `max_chars` to avoid flooding the context with very large files.
