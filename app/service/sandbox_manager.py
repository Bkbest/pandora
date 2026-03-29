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

    def create_sandbox(self, sandbox_name: str) -> str:
        self.ensure_root()

        # Generate unique ID and append sandbox_name if provided
        unique_id = uuid.uuid4().hex
        if sandbox_name:
            # Sanitize sandbox_name to be filesystem and container-name friendly
            sanitized_name = "".join(c for c in sandbox_name if c.isalnum() or c in ('-', '_')).rstrip('-_')
            sandbox_id = f"{unique_id}-{sanitized_name}"
        else:
            sandbox_id = unique_id
        
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
                security_opt=["no-new-privileges"],
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

    def execute(self, sandbox_id: str, command: str, args: List[str] | None = None) -> Tuple[int, str, str]:
        """Execute a bash command inside the sandbox container.
        
        Args:
            sandbox_id: sandbox identifier
            command: the bash command to execute (e.g., 'python', 'ls', 'cat')
            args: command-line arguments passed to the command
            
        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        base = self.sandbox_dir(sandbox_id)
        if not base.exists():
            self.get_container(sandbox_id)
            raise HTTPException(status_code=404, detail="Sandbox directory missing")

        container = self.get_container(sandbox_id)

        # Build the full command string
        full_cmd = command
        if args:
            full_cmd += " " + " ".join(args)
        
        # Use /bin/sh to properly parse and execute the command with PATH set
        # Include common Python paths and exclude /tmp to avoid stray virtualenvs
        exec_result = container.exec_run(
            ["/bin/sh", "-c", full_cmd],
            stdout=True,
            stderr=True,
            demux=True,
            environment={
                "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
            },
        )

        exit_code = int(getattr(exec_result, "exit_code", 1))
        stdout_b, stderr_b = getattr(exec_result, "output", (b"", b"")) or (b"", b"")

        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")

        return exit_code, stdout, stderr

    def list_running_sandboxes(self) -> List[dict]:
        """List all running sandboxes with their status and metadata."""
        try:
            containers = self.client.containers.list(
                all=True,
                filters={"name": f"{self.container_prefix}-"}
            )
            
            sandboxes = []
            for container in containers:
                # Extract sandbox ID from container name
                container_name = container.name
                if container_name.startswith(f"{self.container_prefix}-"):
                    sandbox_id = container_name[len(self.container_prefix) + 1:]
                    
                    # Get sandbox directory info
                    sandbox_dir = self.sandbox_dir(sandbox_id)
                    dir_exists = sandbox_dir.exists()
                    
                    # Get container status
                    status = container.status
                    
                    # Get container creation time
                    created = container.attrs.get("Created", "")
                    
                    # Get container stats
                    container_info = {
                        "id": sandbox_id,
                        "container_name": container_name,
                        "status": status,
                        "created": created,
                        "directory_exists": dir_exists,
                        "image": container.image.tags[0] if container.image.tags else self.docker_image,
                    }
                    
                    sandboxes.append(container_info)
            
            # Sort by creation time (newest first)
            sandboxes.sort(key=lambda x: x.get("created", ""), reverse=True)
            return sandboxes
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list sandboxes: {str(e)}")
