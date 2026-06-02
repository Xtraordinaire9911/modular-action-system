"""Smoke test so pytest has at least one passing test during scaffold phase."""


def test_import_src():
    import src  # noqa: F401
