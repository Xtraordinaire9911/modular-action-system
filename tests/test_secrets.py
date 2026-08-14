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


# --- the diagnostic ------------------------------------------------------------------


def test_a_bare_key_with_no_name_is_named_as_the_problem(tmp_path):
    """The failure this was written for: silently ignored, looks like a missing file."""
    from src.config.secrets import describe_local_env

    path = tmp_path / ".env.local"
    path.write_text("sk-" + "x" * 100 + "\n", encoding="utf-8")

    notes = describe_local_env(path)

    assert len(notes) == 1
    assert "no '=' in it" in notes[0] and "DASHSCOPE_API_KEY=" in notes[0]
    assert "sk-" not in notes[0], "the diagnostic must not echo the key"


def test_a_missing_file_says_what_to_create(tmp_path):
    from src.config.secrets import describe_local_env

    notes = describe_local_env(tmp_path / "absent")

    assert "does not exist" in notes[0] and "DASHSCOPE_API_KEY=" in notes[0]


def test_an_unknown_variable_name_is_reported_with_the_known_ones(tmp_path):
    from src.config.secrets import describe_local_env

    path = tmp_path / ".env.local"
    path.write_text("QWEN_KEY=sk-abc\n", encoding="utf-8")

    notes = describe_local_env(path)

    assert "does not read" in notes[0] and "DASHSCOPE_API_KEY" in notes[0]
    assert "sk-abc" not in notes[0]


def test_a_correct_file_is_confirmed_without_revealing_the_value(tmp_path, monkeypatch):
    from src.config.secrets import describe_local_env

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    path = tmp_path / ".env.local"
    path.write_text("DASHSCOPE_API_KEY=sk-secret-value\n", encoding="utf-8")

    notes = describe_local_env(path)

    assert "looks correct" in notes[0] and "DASHSCOPE_API_KEY" in notes[0]
    assert "sk-secret-value" not in notes[0]


def test_configured_key_names_loads_the_file_first(tmp_path, monkeypatch):
    """The earlier version only read the environment and reported a correct file as empty."""
    from src.config.secrets import configured_key_names

    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    path = tmp_path / ".env.local"
    path.write_text("ZHIPU_API_KEY=sk-abc\n", encoding="utf-8")

    assert "ZHIPU_API_KEY" in configured_key_names(path)


def test_a_byte_order_mark_is_reported_because_editors_add_them(tmp_path, monkeypatch):
    from src.config.secrets import describe_local_env

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    path = tmp_path / ".env.local"
    path.write_bytes(b"\xef\xbb\xbfDASHSCOPE_API_KEY=sk-abc\n")

    notes = describe_local_env(path)

    assert any("byte order mark" in note for note in notes)
