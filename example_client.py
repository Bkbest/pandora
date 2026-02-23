import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def _request(method: str, url: str, body: dict | None = None, timeout: int = 30):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return resp.status, None
            return resp.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            payload = raw.decode("utf-8", errors="replace")
        return e.code, payload


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    base = base.rstrip("/")

    print(f"Using base URL: {base}")

    # 1) Create sandbox
    status, payload = _request("POST", f"{base}/api/sandboxes")
    if status != 200:
        print("Create sandbox failed:", status, payload)
        return 1

    sandbox_id = payload["id"]
    print("Created sandbox:", sandbox_id)

    try:
        # 2) Install a package (example: requests)
        status, payload = _request(
            "POST",
            f"{base}/api/sandboxes/{sandbox_id}/packages",
            {"packages": ["requests==2.32.3"]},
            timeout=300,
        )
        if status != 200:
            print("Install packages failed:", status, payload)
            return 1
        if not isinstance(payload, dict) or payload.get("exit_code", 1) != 0:
            print("Install packages returned non-zero:", payload)
            return 1
        if payload.get("stdout"):
            print("pip stdout:\n", payload.get("stdout"))
        if payload.get("stderr"):
            print("pip stderr:\n", payload.get("stderr"))
        print("Installed packages")

        # 2) Upload a Python file
        code = """\
import sys
import requests
print('hello from sandbox')
print('argv:', sys.argv[1:])
print('requests version:', requests.__version__)
"""
        status, payload = _request(
            "POST",
            f"{base}/api/sandboxes/{sandbox_id}/files",
            {"path": "main.py", "content": code},
        )
        if status != 200:
            print("Upsert file failed:", status, payload)
            return 1
        print("Uploaded main.py")

        # 3) Execute
        status, payload = _request(
            "POST",
            f"{base}/api/sandboxes/{sandbox_id}/execute",
            {"path": "main.py", "args": ["--foo", "bar"]},
            timeout=60,
        )
        if status != 200:
            print("Execute failed:", status, payload)
            return 1
        print("Execute result:")
        print(json.dumps(payload, indent=2))

        # 4) List files
        status, payload = _request("GET", f"{base}/api/sandboxes/{sandbox_id}/files")
        if status != 200:
            print("List files failed:", status, payload)
            return 1
        print("Files:")
        for f in payload.get("files", []):
            print("-", f)

    finally:
        # 5) Delete sandbox
        status, payload = _request("DELETE", f"{base}/api/sandboxes/{sandbox_id}")
        if status != 200:
            print("Delete sandbox failed:", status, payload)
            return 1
        print("Deleted sandbox:", sandbox_id)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
