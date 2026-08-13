"""A key must be loadable without ever being printed, logged, or committed."""

from __future__ import annotations

from src.config.secrets import KNOWN_KEYS, configured_key_names, load_local_env


def test_a_known_key_is_loaded_from_the_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    path = tmp_path / ".env.local"
    path.write_text("DASHSCOPE_API_KEY=sk-secret\n", encoding="utf-8")

    applied = load_local_env(path)

    assert applied == ["DASHSCOPE_API_KEY"], "the name is reported"
    import os

    assert os.environ["DASHSCOPE_API_KEY"] == "sk-secret"


def test_only_names_are_ever_returned_never_values(tmp_path, monkeypatch):
    """Nothing in this module hands a caller something it might log."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-secret")

    for value in load_local_env(tmp_path / "missing") + configured_key_names():
        assert "sk-secret" not in value


def test_an_existing_environment_variable_outranks_the_file(tmp_path, monkeypatch):
    """CI secrets and a one-off prefix must win over a stale local file."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "from-the-shell")
    path = tmp_path / ".env.local"
    path.write_text("DASHSCOPE_API_KEY=from-the-file\n", encoding="utf-8")

    applied = load_local_env(path)

    import os

    assert applied == [] and os.environ["DASHSCOPE_API_KEY"] == "from-the-shell"


def test_quotes_are_stripped_because_every_guide_tells_people_to_add_them(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    path = tmp_path / ".env.local"
    path.write_text('ZHIPU_API_KEY="quoted-key"\n', encoding="utf-8")

    load_local_env(path)

    import os

    assert os.environ["ZHIPU_API_KEY"] == "quoted-key"


def test_an_unknown_name_is_ignored_rather_than_set(tmp_path, monkeypatch):
    """A stray line must not be able to set PATH or PYTHONPATH."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    path = tmp_path / ".env.local"
    path.write_text("PYTHONPATH=/evil\nDASHSCOPE_API_KEY=ok\n", encoding="utf-8")

    applied = load_local_env(path)

    import os

    assert "PYTHONPATH" not in applied and os.environ.get("PYTHONPATH") is None


def test_comments_and_blank_lines_are_skipped(tmp_path, monkeypatch):
    monkeypatch.delenv("VLM_MODEL", raising=False)
    path = tmp_path / ".env.local"
    path.write_text("# a comment\n\nVLM_MODEL=qwen-vl-plus\n", encoding="utf-8")

    assert load_local_env(path) == ["VLM_MODEL"]


def test_a_missing_file_is_not_an_error():
    assert load_local_env("no-such-file-here") == []


def test_the_allowlist_covers_every_provider_the_code_can_use():
    for expected in ("DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        assert expected in KNOWN_KEYS
