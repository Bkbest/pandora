from pathlib import Path
import os
from typing import List

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from .sandbox_manager import SandboxManager


APP_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_ROOT = Path(os.environ.get("SANDBOX_ROOT", str(APP_ROOT / "sandboxes"))).resolve()
DOCKER_IMAGE = os.environ.get("SANDBOX_PYTHON_IMAGE", "python:3.11-slim")
CONTAINER_PREFIX = os.environ.get("SANDBOX_CONTAINER_PREFIX", "code-sandbox")
WORKDIR_IN_CONTAINER = "/workspace"
NETWORK_DISABLED = os.environ.get("SANDBOX_NETWORK_DISABLED", "0").lower() in ("1", "true", "yes")
app = FastAPI()

manager = SandboxManager(
    sandbox_root=SANDBOX_ROOT,
    docker_image=DOCKER_IMAGE,
    container_prefix=CONTAINER_PREFIX,
    workdir_in_container=WORKDIR_IN_CONTAINER,
    network_disabled=NETWORK_DISABLED,
)


class CreateSandboxResponse(BaseModel):
    id: str


class ExecuteRequest(BaseModel):
    path: str = Field(default="main.py", description="Path to a .py file inside the sandbox")
    args: List[str] = Field(default_factory=list)


class ExecuteResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str


class ListFilesResponse(BaseModel):
    files: List[str]


class UpsertFileRequest(BaseModel):
    path: str
    content: str


class InstallPackagesRequest(BaseModel):
    packages: List[str]


class InstallPackagesResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str


@app.get("/doc")
def doc() -> dict:
    return {
        "service": "code-sandbox",
        "language": "python",
        "endpoints": [
            {
                "method": "POST",
                "path": "/api/sandboxes",
                "description": "Create a new sandbox (workspace directory + docker container)",
                "response": {"id": "<sandbox_id>"},
            },
            {
                "method": "DELETE",
                "path": "/api/sandboxes/{id}",
                "description": "Remove a sandbox (docker container + workspace directory)",
                "response": {"ok": True},
            },
            {
                "method": "GET",
                "path": "/api/sandboxes/{id}/files",
                "description": "List files in the sandbox workspace directory",
                "query": {"dir": "/"},
                "response": {"files": ["main.py", "src/util.py"]},
            },
            {
                "method": "POST",
                "path": "/api/sandboxes/{id}/files",
                "description": "Create or update a file in the sandbox workspace",
                "request": {"path": "main.py", "content": "print('hello')\n"},
                "response": {"ok": True},
            },
            {
                "method": "DELETE",
                "path": "/api/sandboxes/{id}/files/{path}",
                "description": "Delete a file from the sandbox workspace",
                "response": {"ok": True},
            },
            {
                "method": "POST",
                "path": "/api/sandboxes/{id}/execute",
                "description": "Execute a .py file inside the sandbox container",
                "request": {"path": "main.py", "args": ["--foo", "bar"]},
                "response": {"exit_code": 0, "stdout": "...", "stderr": "..."},
                "notes": ["Only .py files can be executed"],
            },
            {
                "method": "POST",
                "path": "/api/sandboxes/{id}/packages",
                "description": "Install Python packages into the sandbox (persisted in the workspace)",
                "request": {"packages": ["requests==2.32.3"]},
                "response": {"exit_code": 0, "stdout": "...", "stderr": "..."},
                "notes": [
                    "Packages are installed into /workspace/.python_packages using pip --target",
                    "Execution includes that directory via PYTHONPATH",
                ],
            },
        ],
    }


@app.post("/api/sandboxes", response_model=CreateSandboxResponse)
def create_sandbox() -> CreateSandboxResponse:
    sandbox_id = manager.create_sandbox()
    return CreateSandboxResponse(id=sandbox_id)


@app.delete("/api/sandboxes/{sandbox_id}")
def delete_sandbox(sandbox_id: str) -> dict:
    manager.delete_sandbox(sandbox_id)
    return {"ok": True}


@app.get("/api/sandboxes/{sandbox_id}/files", response_model=ListFilesResponse)
def list_files(sandbox_id: str, dir: str = Query(default="/", description="Directory within the sandbox to list")) -> ListFilesResponse:
    files = manager.list_files(sandbox_id, dir)
    return ListFilesResponse(files=files)


@app.post("/api/sandboxes/{sandbox_id}/files")
def upsert_file(sandbox_id: str, req: UpsertFileRequest) -> dict:
    manager.upsert_file(sandbox_id, req.path, req.content)
    return {"ok": True}


@app.delete("/api/sandboxes/{sandbox_id}/files/{file_path:path}")
def delete_file(sandbox_id: str, file_path: str) -> dict:
    manager.delete_file(sandbox_id, file_path)
    return {"ok": True}


@app.post("/api/sandboxes/{sandbox_id}/execute", response_model=ExecuteResponse)
def execute_code(sandbox_id: str, req: ExecuteRequest) -> ExecuteResponse:
    exit_code, stdout, stderr = manager.execute(sandbox_id, req.path, req.args)
    return ExecuteResponse(exit_code=exit_code, stdout=stdout, stderr=stderr)


@app.post("/api/sandboxes/{sandbox_id}/packages", response_model=InstallPackagesResponse)
def install_packages(sandbox_id: str, req: InstallPackagesRequest) -> InstallPackagesResponse:
    exit_code, stdout, stderr = manager.install_packages(sandbox_id, req.packages)
    return InstallPackagesResponse(exit_code=exit_code, stdout=stdout, stderr=stderr)
