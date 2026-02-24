import os
import shutil
import uuid
from pathlib import Path
from typing import List, Tuple

import docker
from docker.models.containers import Container
from fastapi import HTTPException


class SandboxManager:
    def __init__(
        self,
        *,
        sandbox_root: Path,
        docker_image: str,
        container_prefix: str,
        workdir_in_container: str,
        network_disabled: bool,
    ) -> None:
        self.sandbox_root = sandbox_root.resolve()
        self.docker_image = docker_image
        self.container_prefix = container_prefix
        self.workdir_in_container = workdir_in_container
        self.network_disabled = network_disabled
        self.client = docker.from_env()

    def ensure_root(self) -> None:
        self.sandbox_root.mkdir(parents=True, exist_ok=True)

    def container_name(self, sandbox_id: str) -> str:
        return f"{self.container_prefix}-{sandbox_id}"

    def sandbox_dir(self, sandbox_id: str) -> Path:
        return (self.sandbox_root / sandbox_id).resolve()

    def safe_path(self, base_dir: Path, rel_path: str, *, allow_base: bool = False) -> Path:
        rel = rel_path.lstrip("/\\")
        p = (base_dir / rel).resolve()
        if p == base_dir:
            if allow_base:
                return p
            raise HTTPException(status_code=400, detail="Invalid path")
        if base_dir in p.parents:
            return p
        raise HTTPException(status_code=400, detail="Invalid path")

    def get_container(self, sandbox_id: str) -> Container:
        name = self.container_name(sandbox_id)
        try:
            return self.client.containers.get(name)
        except docker.errors.NotFound:
            raise HTTPException(status_code=404, detail="Sandbox not found")

    def create_sandbox(self) -> str:
        self.ensure_root()

        sandbox_id = uuid.uuid4().hex
        host_dir = self.sandbox_dir(sandbox_id)
        host_dir.mkdir(parents=True, exist_ok=False)

        name = self.container_name(sandbox_id)

        try:
            self.client.containers.run(
                self.docker_image,
                command=["sleep", "infinity"],
                name=name,
                detach=True,
                tty=False,
                network_disabled=self.network_disabled,
                read_only=True,
                security_opt=["no-new-privileges"],
                user="1000:1000",
                working_dir=self.workdir_in_container,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=256m"},
                volumes={
                    str(host_dir): {"bind": self.workdir_in_container, "mode": "rw"}
                },
            )
        except Exception:
            shutil.rmtree(host_dir, ignore_errors=True)
            raise

        return sandbox_id

    def delete_sandbox(self, sandbox_id: str) -> None:
        container = self.get_container(sandbox_id)
        try:
            container.remove(force=True)
        finally:
            shutil.rmtree(self.sandbox_dir(sandbox_id), ignore_errors=True)

    def list_files(self, sandbox_id: str, dir: str) -> List[str]:
        base = self.sandbox_dir(sandbox_id)
        if not base.exists():
            self.get_container(sandbox_id)
            raise HTTPException(status_code=404, detail="Sandbox directory missing")

        list_dir = self.safe_path(base, dir, allow_base=True)
        if not list_dir.exists() or not list_dir.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")

        files: List[str] = []
        for p in list_dir.rglob("*"):
            if p.is_file():
                files.append(str(p.relative_to(base)).replace("\\", "/"))

        files.sort()
        return files

    def upsert_file(self, sandbox_id: str, path: str, content: str) -> None:
        base = self.sandbox_dir(sandbox_id)
        if not base.exists():
            self.get_container(sandbox_id)
            raise HTTPException(status_code=404, detail="Sandbox directory missing")

        target = self.safe_path(base, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def delete_file(self, sandbox_id: str, file_path: str) -> None:
        base = self.sandbox_dir(sandbox_id)
        if not base.exists():
            self.get_container(sandbox_id)
            raise HTTPException(status_code=404, detail="Sandbox directory missing")

        target = self.safe_path(base, file_path)
        if target.exists() and target.is_file():
            target.unlink()

    def read_file(self, sandbox_id: str, file_path: str) -> str:
        base = self.sandbox_dir(sandbox_id)
        if not base.exists():
            self.get_container(sandbox_id)
            raise HTTPException(status_code=404, detail="Sandbox directory missing")

        target = self.safe_path(base, file_path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="File not found")
        if not target.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")
        
        return target.read_text(encoding="utf-8")

    def execute(self, sandbox_id: str, path: str, args: List[str]) -> Tuple[int, str, str]:
        if not path.endswith(".py"):
            raise HTTPException(status_code=400, detail="Only .py files can be executed")

        base = self.sandbox_dir(sandbox_id)
        if not base.exists():
            self.get_container(sandbox_id)
            raise HTTPException(status_code=404, detail="Sandbox directory missing")

        target = self.safe_path(base, path)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        container = self.get_container(sandbox_id)

        cmd = [
            "python",
            str(Path(self.workdir_in_container) / path).replace("\\", "/"),
            *args,
        ]

        pkg_dir = str(Path(self.workdir_in_container) / ".python_packages").replace("\\", "/")
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

        return exit_code, stdout, stderr

    def install_packages(self, sandbox_id: str, packages: List[str]) -> Tuple[int, str, str]:
        if not packages:
            raise HTTPException(status_code=400, detail="No packages provided")

        base = self.sandbox_dir(sandbox_id)
        if not base.exists():
            self.get_container(sandbox_id)
            raise HTTPException(status_code=404, detail="Sandbox directory missing")

        container = self.get_container(sandbox_id)

        target_dir = str(Path(self.workdir_in_container) / ".python_packages").replace("\\", "/")
        cmd = [
            "python",
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--target",
            target_dir,
            *packages,
        ]

        exec_result = container.exec_run(cmd, stdout=True, stderr=True, demux=True)

        exit_code = int(getattr(exec_result, "exit_code", 1))
        stdout_b, stderr_b = getattr(exec_result, "output", (b"", b"")) or (b"", b"")

        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")

        if exit_code != 0:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Package installation failed",
                    "exit_code": exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "note": (
                        "If this is a network error and you want to allow pip downloads, run the service with "
                        "SANDBOX_NETWORK_DISABLED=0 (default)."
                    ),
                },
            )

        return exit_code, stdout, stderr
