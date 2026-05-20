"""Tests for the case-sdk CLI.

Run-mode tests use a fake harness via dependency injection at the Runtime
boundary; we don't actually move the mouse. ``init`` and ``doctor`` use a
tmp HOME via monkeypatch so the real ~/.case-sdk is untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from case_sdk import cli


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    """Redirect ~/.case-sdk to a tmp dir for the duration of the test."""
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / ".case-sdk")
    monkeypatch.setattr(
        cli, "CONFIG_FILE", tmp_path / ".case-sdk" / "config.toml"
    )
    yield tmp_path


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "case-sdk" in out


def test_init_writes_config(isolated_home, capsys):
    code = cli.main(["init", "--key", "sk_test", "--api", "wss://example.com"])
    assert code == 0
    assert cli.CONFIG_FILE.exists()
    content = cli.CONFIG_FILE.read_text()
    assert "sk_test" in content
    assert "wss://example.com" in content


def test_init_without_force_refuses_to_overwrite(isolated_home, capsys):
    cli.CONFIG_DIR.mkdir(parents=True)
    cli.CONFIG_FILE.write_text("[default]\napi_key = \"old\"\n")
    code = cli.main(["init", "--key", "new"])
    assert code == 1
    assert "use --force" in capsys.readouterr().err


def test_init_force_overwrites(isolated_home):
    cli.CONFIG_DIR.mkdir(parents=True)
    cli.CONFIG_FILE.write_text("[default]\napi_key = \"old\"\n")
    code = cli.main(["init", "--key", "new", "--force"])
    assert code == 0
    assert "new" in cli.CONFIG_FILE.read_text()


def test_doctor_runs_and_prints_status(isolated_home, capsys):
    with patch("case_sdk.cli.check_perms") as p, patch(
        "case_sdk.cli.missing", return_value=[]
    ):
        p.return_value.accessibility = True
        p.return_value.screen_recording = True
        p.return_value.input_monitoring = None
        code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "case-sdk" in out
    assert "permissions" in out
    assert "config" in out
    assert code == 0


def test_doctor_returns_nonzero_when_perms_missing(isolated_home, capsys):
    with patch("case_sdk.cli.check_perms") as p, patch(
        "case_sdk.cli.missing", return_value=["Accessibility"]
    ), patch("case_sdk.cli.print_onboarding_banner"):
        p.return_value.accessibility = False
        p.return_value.screen_recording = True
        p.return_value.input_monitoring = None
        code = cli.main(["doctor"])
    assert code == 1


def test_run_local_file_uses_local_driver(isolated_home, capsys, tmp_path):
    proc = tmp_path / "p.py"
    proc.write_text(
        "from case_sdk import Action\n"
        "def procedure(params):\n"
        "    yield Action('noop', echo=params.get('greeting', ''))\n"
        "    return {'success': True, 'received': params.get('greeting', '')}\n"
    )

    # Replace the harness so we don't need pyautogui / TextEdit.
    from case_sdk.harness.executor import Harness

    fake = Harness()

    @fake.register_action("noop")
    def _noop(**kwargs):
        return {}

    with patch(
        "case_sdk.cli.Runtime",
        side_effect=lambda *a, **kw: __import__(
            "case_sdk.runtime", fromlist=["Runtime"]
        ).Runtime(harness=fake),
    ), patch("case_sdk.cli.ensure_perms"):
        code = cli.main(
            [
                "run",
                str(proc),
                "--param",
                "greeting=hi",
                "--skip-perm-check",
            ]
        )
    assert code == 0
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result == {"success": True, "received": "hi"}


def test_run_remote_requires_api_key(isolated_home, capsys):
    code = cli.main(["run", "some_procedure"])
    assert code == 2
    err = capsys.readouterr().err
    assert "no API key" in err or "requires --key" in err


def test_procedures_lists_builtin(capsys):
    code = cli.main(["procedures", "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    names = {p["name"] for p in out["procedures"]}
    assert "davinci/append_to_timeline" in names


def test_run_by_procedure_name(isolated_home, capsys, tmp_path):
    from case_sdk.harness.executor import Harness

    fake = Harness()

    @fake.register_action("activate_app")
    def activate_app(**_):
        return {}

    with patch("case_sdk.cli.Runtime") as Rt, patch("case_sdk.cli.ensure_perms"):
        inst = Rt.return_value
        inst.execute_file.return_value = {"success": True, "via": "name"}
        code = cli.main(
            ["run", "davinci/focus_resolve", "--skip-perm-check"],
        )
    assert code == 0
    inst.execute_file.assert_called_once()
    assert inst.execute_file.call_args[0][0] == "davinci/focus_resolve"


def test_verify_list_prints_json(capsys):
    code = cli.main(["verify-list"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert "davinci.db_snapshot" in out["verifiers"]


def test_ax_tree_prints_json(isolated_home, capsys):
    with patch("case_sdk.cli.app_tree", return_value={"app": "X", "tree": {}}), patch(
        "case_sdk.cli.ensure_perms"
    ):
        code = cli.main(["ax-tree", "--frontmost", "--skip-perm-check"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["app"] == "X"


def test_run_invalid_param_format(isolated_home, capsys, tmp_path):
    proc = tmp_path / "p.py"
    proc.write_text("def procedure(params):\n    return {}\n    yield\n")
    code = cli.main(["run", str(proc), "--param", "no_equals", "--skip-perm-check"])
    assert code == 2
    assert "k=v" in capsys.readouterr().err


def _user_action_proc(tmp_path):
    proc = tmp_path / "needs.json"
    proc.write_text(
        json.dumps(
            {
                "requires_user_action": True,
                "user_action_prompt": "Do the thing in the app",
                "steps": [{"action": "noop"}],
                "return": {"ok": True},
            }
        )
    )
    return proc


def _fake_runtime():
    from case_sdk.harness.executor import Harness
    from case_sdk.runtime import Runtime

    h = Harness()

    @h.register_action("noop")
    def _noop(**kwargs):
        return {}

    return lambda *a, **kw: Runtime(harness=h)


def test_run_prompts_when_user_action_required(
    isolated_home, capsys, tmp_path, monkeypatch
):
    proc = _user_action_proc(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **kw: calls.append("entered") or "")

    with patch("case_sdk.cli.Runtime", side_effect=_fake_runtime()), patch(
        "case_sdk.cli.ensure_perms"
    ):
        code = cli.main(["run", str(proc), "--skip-perm-check"])
    err = capsys.readouterr().err
    assert code == 0
    assert "Do the thing in the app" in err
    assert calls == ["entered"]


def test_run_skips_prompt_with_yes_flag(
    isolated_home, capsys, tmp_path, monkeypatch
):
    proc = _user_action_proc(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr("builtins.input", lambda *a, **kw: calls.append("entered") or "")

    with patch("case_sdk.cli.Runtime", side_effect=_fake_runtime()), patch(
        "case_sdk.cli.ensure_perms"
    ):
        code = cli.main(["run", str(proc), "--skip-perm-check", "--yes"])
    assert code == 0
    assert calls == []  # prompt never invoked


def test_run_aborts_when_stdin_not_tty(
    isolated_home, capsys, tmp_path, monkeypatch
):
    proc = _user_action_proc(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "")

    with patch("case_sdk.cli.ensure_perms"):
        code = cli.main(["run", str(proc), "--skip-perm-check"])
    err = capsys.readouterr().err
    assert code == 130
    assert "TTY" in err
