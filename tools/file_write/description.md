# file_write

Write or append text content to a file on the local filesystem.

**Parameters**
- `path` *(required)* — Absolute or relative path to the destination file.
  Parent directories must already exist.
- `content` *(required)* — The text to write.
- `mode` *(optional, default `"w"`)* — Write mode:
  - `"w"` — overwrite the file (create if it does not exist)
  - `"a"` — append to the end of the file (create if it does not exist)
- `encoding` *(optional, default `"utf-8"`)* — Character encoding to use.

**Returns**
A confirmation string showing the path and number of bytes written, or an error
message if the write fails.
