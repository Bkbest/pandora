# Code Sandbox Service (Docker-backed)

A minimal HTTP service that provides per-sandbox file storage + **Python-only** code execution inside Docker containers.

This is intended to be used as an execution backend for AI agents that generate code.

## Requirements

- Docker installed and running on the host (e.g. Raspberry Pi)
- Python 3.10+

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Run as an MCP server

This repo also provides an MCP server exposing the same sandbox operations as MCP tools.

Start it (Streamable HTTP transport):

```bash
python -m app.mcp_server
```

By default it listens on `0.0.0.0:3000` and serves MCP at:

- `http://<host>:3000/mcp`

Configuration:

- `MCP_HOST` (default `0.0.0.0`)
- `MCP_PORT` (default `3000`)

Tools available:

- `create_sandbox`
- `delete_sandbox`
- `list_files`
- `upsert_file`
- `delete_file`
- `execute`
- `install_packages`

### LangGraph integration (example)

You can load MCP tools into LangGraph/LangChain using `langchain-mcp-adapters`.

Install in your LangGraph app:

```bash
pip install langchain-mcp-adapters
```

Example (async) using `MultiServerMCPClient`:

```python
import asyncio

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langchain.chat_models import init_chat_model


async def main():
    client = MultiServerMCPClient(
        {
            "pandora_sandbox": {
                "transport": "http",
                "url": "http://127.0.0.1:3000/mcp",
            }
        }
    )

    tools = await client.get_tools()
    model = init_chat_model("openai:gpt-4o-mini")

    agent = create_react_agent(model, tools)
    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "Create a sandbox, write main.py that prints hello, execute it, then delete the sandbox.",
                )
            ]
        }
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
```

## Configuration

Environment variables:

- `SANDBOX_ROOT`
  - Host directory where sandbox workspaces are stored
  - Default: `./sandboxes`
- `SANDBOX_PYTHON_IMAGE`
  - Docker image used for sandbox containers
  - Default: `python:3.11-slim`
- `SANDBOX_CONTAINER_PREFIX`
  - Prefix for Docker container names
  - Default: `code-sandbox`

## API

Base URL: `http://<host>:8000`

### Create sandbox

`POST /api/sandboxes`

Response:

```json
{ "id": "<sandbox_id>" }
```

### Delete sandbox

`DELETE /api/sandboxes/{id}`

Response:

```json
{ "ok": true }
```

### List files

`GET /api/sandboxes/{id}/files`

Response:

```json
{ "files": ["main.py", "src/util.py"] }
```

### Create/update file

`POST /api/sandboxes/{id}/files`

Body:

```json
{ "path": "main.py", "content": "print('hello')\n" }
```

Response:

```json
{ "ok": true }
```

### Delete file

`DELETE /api/sandboxes/{id}/files/{path}`

Response:

```json
{ "ok": true }
```

### Execute code (Python only)

`POST /api/sandboxes/{id}/execute`

Body:

```json
{ "path": "main.py", "args": ["--foo", "bar"] }
```

Response:

```json
{
  "exit_code": 0,
  "stdout": "hello\n",
  "stderr": ""
}
```

Notes:

- Only `.py` files are executable.
- Paths are validated to prevent escaping the sandbox directory.

## How it works

- Each sandbox gets:
  - A host workspace directory: `SANDBOX_ROOT/<id>`
  - A dedicated Docker container named: `<prefix>-<id>`
- The workspace is bind-mounted into the container at `/workspace`.
- Execution is done via Docker `exec` using `python /workspace/<path>`.

## Troubleshooting

### Docker permission errors

If you encounter this error during startup:

```
docker.errors.DockerException: Error while fetching server API version: ('Connection aborted.', PermissionError(13, 'Permission denied'))
```

Run these commands to allow Docker commands without sudo:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

Verify by running `docker ps` - there should be no permission errors.

## Security notes (minimal hardening)

Containers are started with:

- Disabled networking
- Read-only container root filesystem
- `no-new-privileges`
- Non-root user (`1000:1000`)

This is intentionally simple; for stronger isolation consider adding:

- CPU/memory/pids limits
- Execution timeouts
- Allowlist of filenames/entrypoints
- Authentication/authorization in front of the service
