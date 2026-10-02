from pathlib import Path
import os,tempfile,ipaddress
from urllib.parse import urlsplit

def write_file(path: Path, contents: str | bytes, *, mode: int = 0o600) -> None:
    """Atomically write an app-owned file with its intended permissions."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(contents.encode() if isinstance(contents, str) else contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass

def write_private(path: Path, text: str | bytes) -> None:
    """Atomically write private configuration, never briefly exposing it."""
    write_file(path, text)

def canonical_host(value: str) -> str:
    """Return the single canonical representation accepted for host names."""
    if not isinstance(value, str) or not value:
        raise ValueError("Host must be a valid IP address or IDNA hostname.")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        host = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Host must be a valid IDNA hostname.") from exc
    if not host or len(host) > 253:
        raise ValueError("Host must be a valid IDNA hostname.")
    labels = host.split(".")
    if any(not label or len(label) > 63 or label[0] == "-" or label[-1] == "-"
           or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in label)
           for label in labels):
        raise ValueError("Host must be a valid IDNA hostname.")
    return host

def validate_origin(value: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("Public origins must be exact http:// or https:// origins.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Public origins must be exact http:// or https:// origins.")
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Public origins must not contain credentials, paths, queries, or fragments.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Public origin has an invalid port.") from exc
    if port == 0:
        raise ValueError("Public origin has an invalid port.")
    host = canonical_host(parsed.hostname)
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{host}{f':{port}' if port is not None and port != default_port else ''}"
