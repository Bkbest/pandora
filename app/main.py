import os
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

import docker
from docker.models.containers import Container
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


APP_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_ROOT = Path(os.environ.get("SANDBOX_ROOT", str(APP_ROOT / "sandboxes"))).resolve()
DOCKER_IMAGE = os.environ.get("SANDBOX_PYTHON_IMAGE", "python:3.11-slim")
CONTAINER_PREFIX = os.environ.get("SANDBOX_CONTAINER_PREFIX", "code-sandbox")
WORKDIR_IN_CONTAINER = "/workspace"

client = docker.from_env()
app = FastAPI()


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


def _container_name(sandbox_id: str) -> str:
    return f"{CONTAINER_PREFIX}-{sandbox_id}"


def _sandbox_dir(sandbox_id: str) -> Path:
    return (SANDBOX_ROOT / sandbox_id).resolve()


def _safe_path(base_dir: Path, rel_path: str) -> Path:
    rel = rel_path.lstrip("/\\")
    p = (base_dir / rel).resolve()
    if p == base_dir:
        raise HTTPException(status_code=400, detail="Invalid path")
    if base_dir in p.parents:
        return p
    raise HTTPException(status_code=400, detail="Invalid path")


def _get_container(sandbox_id: str) -> Container:
    name = _container_name(sandbox_id)
    try:
        return client.containers.get(name)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Sandbox not found")


def _ensure_root() -> None:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)


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
    _ensure_root()

    sandbox_id = uuid.uuid4().hex
    host_dir = _sandbox_dir(sandbox_id)
    host_dir.mkdir(parents=True, exist_ok=False)

    name = _container_name(sandbox_id)

    try:
        container = client.containers.run(
            DOCKER_IMAGE,
            command=["sleep", "infinity"],
            name=name,
            detach=True,
            tty=False,
            network_disabled=True,
            read_only=True,
            security_opt=["no-new-privileges"],
            user="1000:1000",
            working_dir=WORKDIR_IN_CONTAINER,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=256m"},
            volumes={
                str(host_dir): {"bind": WORKDIR_IN_CONTAINER, "mode": "rw"}
            },
        )
    except Exception:
        shutil.rmtree(host_dir, ignore_errors=True)
        raise

    return CreateSandboxResponse(id=sandbox_id)


@app.delete("/api/sandboxes/{sandbox_id}")
def delete_sandbox(sandbox_id: str) -> dict:
    container = _get_container(sandbox_id)
    try:
        container.remove(force=True)
    finally:
        shutil.rmtree(_sandbox_dir(sandbox_id), ignore_errors=True)
    return {"ok": True}


@app.get("/api/sandboxes/{sandbox_id}/files", response_model=ListFilesResponse)
def list_files(sandbox_id: str) -> ListFilesResponse:
    base = _sandbox_dir(sandbox_id)
    if not base.exists():
        _get_container(sandbox_id)
        raise HTTPException(status_code=404, detail="Sandbox directory missing")

    files: List[str] = []
    for p in base.rglob("*"):
        if p.is_file():
            files.append(str(p.relative_to(base)).replace("\\", "/"))

    files.sort()
    return ListFilesResponse(files=files)


@app.post("/api/sandboxes/{sandbox_id}/files")
def upsert_file(sandbox_id: str, req: UpsertFileRequest) -> dict:
    base = _sandbox_dir(sandbox_id)
    if not base.exists():
        _get_container(sandbox_id)
        raise HTTPException(status_code=404, detail="Sandbox directory missing")

    target = _safe_path(base, req.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    return {"ok": True}


@app.delete("/api/sandboxes/{sandbox_id}/files/{file_path:path}")
def delete_file(sandbox_id: str, file_path: str) -> dict:
    base = _sandbox_dir(sandbox_id)
    if not base.exists():
        _get_container(sandbox_id)
        raise HTTPException(status_code=404, detail="Sandbox directory missing")

    target = _safe_path(base, file_path)
    if target.exists() and target.is_file():
        target.unlink()
    return {"ok": True}


@app.post("/api/sandboxes/{sandbox_id}/execute", response_model=ExecuteResponse)
def execute_code(sandbox_id: str, req: ExecuteRequest) -> ExecuteResponse:
    if not req.path.endswith(".py"):
        raise HTTPException(status_code=400, detail="Only .py files can be executed")

    base = _sandbox_dir(sandbox_id)
    if not base.exists():
        _get_container(sandbox_id)
        raise HTTPException(status_code=404, detail="Sandbox directory missing")

    target = _safe_path(base, req.path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    container = _get_container(sandbox_id)

    cmd = ["python", str(Path(WORKDIR_IN_CONTAINER) / req.path).replace("\\", "/")] + req.args

    pkg_dir = str(Path(WORKDIR_IN_CONTAINER) / ".python_packages").replace("\\", "/")
    exec_result = container.exec_run(
        cmd,
        stdout=True,
        stderr=True,
        demux=True,
        environment={"PYTHONPATH": pkg_dir},
    )

    exit_code = int(getattr(exec_result, "exit_code", 1))
    stdout_b, stderr_b = getattr(exec_result, "output", (b"", b"")) or (b"", b"")

    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")

    return ExecuteResponse(exit_code=exit_code, stdout=stdout, stderr=stderr)


@app.post("/api/sandboxes/{sandbox_id}/packages", response_model=InstallPackagesResponse)
def install_packages(sandbox_id: str, req: InstallPackagesRequest) -> InstallPackagesResponse:
    if not req.packages:
        raise HTTPException(status_code=400, detail="No packages provided")

    base = _sandbox_dir(sandbox_id)
    if not base.exists():
        _get_container(sandbox_id)
        raise HTTPException(status_code=404, detail="Sandbox directory missing")

    container = _get_container(sandbox_id)

    target_dir = str(Path(WORKDIR_IN_CONTAINER) / ".python_packages").replace("\\", "/")
    cmd = [
        "python",
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--target",
        target_dir,
        *req.packages,
    ]

    exec_result = container.exec_run(cmd, stdout=True, stderr=True, demux=True)

    exit_code = int(getattr(exec_result, "exit_code", 1))
    stdout_b, stderr_b = getattr(exec_result, "output", (b"", b"")) or (b"", b"")

    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")

    return InstallPackagesResponse(exit_code=exit_code, stdout=stdout, stderr=stderr)
