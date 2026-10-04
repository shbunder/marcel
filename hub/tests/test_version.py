import marcel_hub


def test_version_is_semver() -> None:
    parts = marcel_hub.__version__.split('.')
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
