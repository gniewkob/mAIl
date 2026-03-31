from __future__ import annotations

from pathlib import Path


def test_launchd_artifacts_use_mailai_standard_names() -> None:
    expected = [
        "com.mailai.plist.template",
        "com.mailai.test.plist",
        "com.mailai.multi.plist.template",
        "com.mailai.multi.test.plist",
        "com.mailai.multi.prod.plist",
        "com.mailai.metrics.prod.plist",
    ]
    for filename in expected:
        assert Path(filename).exists(), filename


def test_legacy_salonbw_launchd_artifacts_are_removed() -> None:
    retired = [
        "com.salonbw.mailai.plist.template",
        "com.salonbw.mailai.test.plist",
        "com.salonbw.mailai.multi.plist.template",
    ]
    for filename in retired:
        assert not Path(filename).exists(), filename
