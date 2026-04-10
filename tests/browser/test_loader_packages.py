"""Tests for the packages requirement type in the skill loader."""

from __future__ import annotations

from marcel_core.skills.loader import _check_requirements


class TestPackagesRequirement:
    def test_installed_package_passes(self):
        """A package that is installed (like pytest) should pass."""
        assert _check_requirements({'packages': ['pytest']}, 'test-user') is True

    def test_missing_package_fails(self):
        """A package that is not installed should fail."""
        assert _check_requirements({'packages': ['nonexistent_package_xyz_123']}, 'test-user') is False

    def test_multiple_packages_all_present(self):
        """Multiple installed packages should pass."""
        assert _check_requirements({'packages': ['pytest', 'yaml']}, 'test-user') is True

    def test_multiple_packages_one_missing(self):
        """If any package is missing, the check fails."""
        assert _check_requirements({'packages': ['pytest', 'nonexistent_xyz']}, 'test-user') is False

    def test_empty_packages_passes(self):
        """Empty packages list should pass."""
        assert _check_requirements({'packages': []}, 'test-user') is True
