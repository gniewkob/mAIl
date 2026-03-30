from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def test_manifest_secrets_cli_env_mode(tmp_path: Path) -> None:
    import sys

    input_m = tmp_path / "m.json"
    output_m = tmp_path / "m.out.json"
    input_m.write_text(
        json.dumps({"mailboxes": [{"imap_user": "u@e.com", "imap_pass": "s", "imap_host": "h"}]}),
        encoding="utf-8",
    )
    with patch.object(sys, "argv", ["x", "--input", str(input_m), "--output", str(output_m), "--mode", "env"]):
        from mail_ai_agent.manifest_secrets_cli import main

        main()

    result = json.loads(output_m.read_text(encoding="utf-8"))
    mailbox = result["mailboxes"][0]
    assert "imap_pass" not in mailbox
    assert mailbox["imap_pass_ref"].startswith("env:")


def test_sidecar_file_has_restricted_permissions(tmp_path):
    import json
    import os
    import stat
    import sys
    from pathlib import Path
    from unittest.mock import patch

    input_m = tmp_path / "m.json"
    output_m = tmp_path / "m.out.json"
    sidecar = tmp_path / "s.sh"
    input_m.write_text(json.dumps({"mailboxes": [{"imap_user": "u@e.com", "imap_pass": "s", "imap_host": "h"}]}), encoding="utf-8")
    with patch.object(sys, "argv", ["x", "--input", str(input_m), "--output", str(output_m), "--mode", "env", "--sidecar-output", str(sidecar)]):
        from mail_ai_agent.manifest_secrets_cli import main
        main()
    assert stat.S_IMODE(os.stat(sidecar).st_mode) == 0o600
