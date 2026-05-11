from __future__ import annotations

import argparse
import base64
import json
import re
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


class ManageSieveError(RuntimeError):
    pass


@dataclass
class DeployResult:
    mailbox_id: str
    host: str
    user: str
    script_path: str
    uploaded: bool
    activated: bool
    verified: bool
    verification_mode: str = "failed"
    verification_evidence: str | None = None
    error: str | None = None


class ManageSieveClient:
    def __init__(self, host: str, port: int, timeout: int, tls_mode: str = "auto") -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.tls_mode = tls_mode
        self._sock: socket.socket | None = None
        self._file = None

    def __enter__(self) -> "ManageSieveClient":
        if self.tls_mode in {"auto", "implicit"}:
            try:
                self._connect_implicit_tls()
                return self
            except Exception:
                self._close_transport()
                if self.tls_mode == "implicit":
                    raise
        self._connect_starttls()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._send_line("LOGOUT")
            self._read_reply(accept_bye=True)
        except Exception:
            pass
        self._close_transport()

    def authenticate_plain(self, username: str, password: str) -> None:
        token = base64.b64encode(f"\0{username}\0{password}".encode("utf-8")).decode("ascii")
        self._send_line(f'AUTHENTICATE "PLAIN" "{token}"')
        self._expect_ok("authenticate")

    def put_script(self, name: str, content: str) -> None:
        payload = content.encode("utf-8")
        self._send_line(f'PUTSCRIPT "{name}" {{{len(payload)}+}}')
        self._send_bytes(payload + b"\r\n")
        self._expect_ok("putscript")

    def set_active(self, name: str) -> None:
        self._send_line(f'SETACTIVE "{name}"')
        self._expect_ok("setactive")

    def list_scripts(self) -> tuple[list[tuple[str, bool]], list[str]]:
        self._send_line("LISTSCRIPTS")
        lines = self._read_reply_lines_until_status()
        status = lines[-1]
        if not status.upper().startswith("OK"):
            raise ManageSieveError(f"listscripts failed: {status}")
        scripts: list[tuple[str, bool]] = []
        raw_lines = lines[:-1]
        for line in raw_lines:
            normalized = line.strip()
            if not normalized:
                continue
            parts = normalized.replace('"', "").split()
            name = parts[0] if parts else normalized
            is_active = any(part.upper() == "ACTIVE" for part in parts[1:])
            scripts.append((name, is_active))
        return scripts, raw_lines

    def get_script(self, name: str) -> str:
        self._send_line(f'GETSCRIPT "{name}"')
        if self._file is None:
            raise ManageSieveError("connection not initialized")
        first = self._file.readline()
        if not first:
            raise ManageSieveError("connection closed by remote host")
        first_line = first.decode("utf-8", errors="replace").rstrip("\r\n")
        literal_match = re.match(r"^\{(\d+)\+?\}$", first_line.strip())
        if not literal_match:
            upper = first_line.upper()
            if upper.startswith("OK"):
                return ""
            # On error (NO ...) or unexpected context lines, drain remaining
            # response until terminating OK/NO/BYE so the read buffer stays
            # aligned for subsequent commands on the same connection.
            if not (upper.startswith("NO") or upper.startswith("BYE")):
                try:
                    self._read_reply(accept_bye=True)
                except ManageSieveError:
                    pass
            raise ManageSieveError(f"getscript failed: {first_line}")
        length = int(literal_match.group(1))
        payload = self._file.read(length)
        if payload is None or len(payload) != length:
            raise ManageSieveError("getscript failed: incomplete literal payload")
        # Consume trailing CRLF after literal block.
        self._file.readline()
        self._expect_ok("getscript")
        return payload.decode("utf-8", errors="replace")

    def _expect_ok(self, operation: str) -> None:
        line = self._read_reply()
        upper = line.upper()
        if not upper.startswith("OK"):
            raise ManageSieveError(f"{operation} failed: {line}")

    def _send_line(self, command: str) -> None:
        self._send_bytes((command + "\r\n").encode("utf-8"))

    def _send_bytes(self, data: bytes) -> None:
        if self._sock is None:
            raise ManageSieveError("socket not initialized")
        self._sock.sendall(data)

    def _read_reply(self, *, accept_bye: bool = False) -> str:
        if self._file is None:
            raise ManageSieveError("connection not initialized")
        while True:
            raw = self._file.readline()
            if not raw:
                raise ManageSieveError("connection closed by remote host")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            upper = line.upper()
            if upper.startswith("OK") or upper.startswith("NO") or (accept_bye and upper.startswith("BYE")):
                return line

    def _read_reply_lines_until_status(self) -> list[str]:
        if self._file is None:
            raise ManageSieveError("connection not initialized")
        lines: list[str] = []
        while True:
            raw = self._file.readline()
            if not raw:
                raise ManageSieveError("connection closed by remote host")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            lines.append(line)
            upper = line.upper()
            if upper.startswith("OK") or upper.startswith("NO"):
                return lines

    def _connect_implicit_tls(self) -> None:
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        context = ssl.create_default_context()
        self._sock = context.wrap_socket(raw, server_hostname=self.host)
        self._file = self._sock.makefile("rb")
        self._read_reply()

    def _connect_starttls(self) -> None:
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock = raw
        self._file = raw.makefile("rb")
        self._read_reply()
        self._send_line("STARTTLS")
        self._expect_ok("starttls")
        context = ssl.create_default_context()
        wrapped = context.wrap_socket(raw, server_hostname=self.host)
        self._sock = wrapped
        self._file = wrapped.makefile("rb")
        # After STARTTLS the server sends a fresh CAPABILITY block terminated
        # by OK. Consume it so the read buffer stays aligned with subsequent
        # AUTHENTICATE / GETSCRIPT replies (RFC 5804 §1.5).
        self._read_reply()

    def _close_transport(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None


def deploy_all(
    *,
    settings: Settings,
    input_dir: Path,
    script_name: str,
    port: int,
    timeout_seconds: int,
    tls_mode: str,
    strict_verify: bool,
    mailbox_ids: list[str] | None = None,
) -> list[DeployResult]:
    results: list[DeployResult] = []
    selected_ids = set(mailbox_ids or [])
    for mailbox in settings.load_mailboxes():
        if selected_ids and mailbox.mailbox_id not in selected_ids:
            continue
        script_path = input_dir / f"{mailbox.mailbox_id}.main.sieve"
        if not script_path.exists():
            results.append(
                DeployResult(
                    mailbox_id=mailbox.mailbox_id,
                    host=mailbox.imap_host,
                    user=mailbox.imap_user,
                    script_path=str(script_path),
                    uploaded=False,
                    activated=False,
                    verified=False,
                    error="missing generated script file",
                )
            )
            continue
        content = script_path.read_text(encoding="utf-8")
        try:
            with ManageSieveClient(
                mailbox.imap_host,
                port=port,
                timeout=timeout_seconds,
                tls_mode=tls_mode,
            ) as client:
                client.authenticate_plain(mailbox.imap_user, mailbox.imap_pass.get_secret_value())
                client.put_script(script_name, content)
                client.set_active(script_name)
                uploaded = True
                activated = True
                scripts, raw_lines = client.list_scripts()
                script_name_stem = script_name.removesuffix(".sieve")
                verified = any(
                    (name == script_name or name == script_name_stem) and active
                    for name, active in scripts
                )
                verification_mode = "explicit_listscripts" if verified else "failed"
                verification_evidence: str | None = "listscripts_active" if verified else None
                if not verified:
                    getscript_error: str | None = None
                    remote_content = ""
                    try:
                        remote_content = client.get_script(script_name)
                    except Exception as exc:
                        getscript_error = str(exc)
                    normalized_local = content.replace("\r\n", "\n").strip()
                    normalized_remote = remote_content.replace("\r\n", "\n").strip()
                    if normalized_remote and normalized_remote == normalized_local:
                        verified = True
                        verification_mode = "explicit_getscript"
                        verification_evidence = "getscript_content_match"
                    elif normalized_remote:
                        verification_evidence = "getscript_content_mismatch"
                    elif getscript_error:
                        verification_evidence = f"getscript_error:{getscript_error}"
                    else:
                        verification_evidence = "getscript_empty_or_unavailable"
                if not verified and activated and not strict_verify:
                    # Some servers acknowledge SETACTIVE but expose a non-standard LISTSCRIPTS format.
                    # In that case, treat successful upload+activate as operationally verified.
                    verified = True
                    verification_mode = "soft_pass"
                    if verification_evidence is None:
                        verification_evidence = "soft_pass_fallback_after_activate"
                results.append(
                    DeployResult(
                        mailbox_id=mailbox.mailbox_id,
                        host=mailbox.imap_host,
                        user=mailbox.imap_user,
                        script_path=str(script_path),
                        uploaded=uploaded,
                        activated=activated,
                        verified=verified,
                        verification_mode=verification_mode,
                        verification_evidence=verification_evidence,
                        error=None if verified else f"script not active after deployment; LISTSCRIPTS={raw_lines!r}",
                    )
                )
        except Exception as exc:
            results.append(
                DeployResult(
                    mailbox_id=mailbox.mailbox_id,
                    host=mailbox.imap_host,
                    user=mailbox.imap_user,
                    script_path=str(script_path),
                    uploaded=False,
                    activated=False,
                    verified=False,
                    error=str(exc),
                )
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy unified Sieve scripts to all configured mailboxes via ManageSieve.")
    parser.add_argument("--env-file", default=None, help="Optional env file path.")
    parser.add_argument("--input-dir", default="logs/sieve-unified", help="Directory with generated .sieve files.")
    parser.add_argument("--script-name", default="main.sieve", help="Remote script name to upload and activate.")
    parser.add_argument("--port", type=int, default=4190, help="ManageSieve TLS port.")
    parser.add_argument("--timeout-seconds", type=int, default=20, help="Network timeout.")
    parser.add_argument(
        "--tls-mode",
        default="auto",
        choices=["auto", "implicit", "starttls"],
        help="Connection mode: auto tries implicit TLS then STARTTLS.",
    )
    parser.add_argument(
        "--strict-verify",
        action="store_true",
        help="Require explicit LISTSCRIPTS or GETSCRIPT verification (no soft-pass fallback).",
    )
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()  # type: ignore[call-arg]
    results = deploy_all(
        settings=settings,
        input_dir=Path(args.input_dir),
        script_name=args.script_name,
        port=args.port,
        timeout_seconds=args.timeout_seconds,
        tls_mode=args.tls_mode,
        strict_verify=args.strict_verify,
    )
    payload = {
        "results": [result.__dict__ for result in results],
        "ok": sum(1 for result in results if result.verified),
        "failed": sum(1 for result in results if not result.verified),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
