import threading

# musl gives new threads 128 KiB of stack; Gunicorn's gthread workers and the
# MCP server's sync_to_async threads overflow it (for example, CPython 3.14's
# import-error path keeps two PATH_MAX buffers on the stack), killing the
# worker with SIGSEGV. Match glibc's default before any of those threads start.
threading.stack_size(8 * 1024 * 1024)
