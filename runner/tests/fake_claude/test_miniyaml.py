from __future__ import annotations

import pytest

from fake_claude.miniyaml import YamlError, loads


def test_nested_mappings_sequences_and_scalars() -> None:
    text = """
# a comment
name: worker      # trailing comment
count: 3
ratio: 0.25
on: true
off: false
nothing: null
tilde: ~
empty_list: []
empty_map: {}
quoted: "approve Bash: rm README.md && ls"
single: 'it''s # not a comment'
plain: usage limit reached — check plan
steps:
  - wait: 0.1
    job:
      needs: null
      detail: Reading calc.py
  - gate: permission
  -
    state: done
  -
items:
- a
- 2
bare:
"""
    assert loads(text) == {
        'name': 'worker',
        'count': 3,
        'ratio': 0.25,
        'on': True,
        'off': False,
        'nothing': None,
        'tilde': None,
        'empty_list': [],
        'empty_map': {},
        'quoted': 'approve Bash: rm README.md && ls',
        'single': "it's # not a comment",
        'plain': 'usage limit reached — check plan',
        'steps': [
            {'wait': 0.1, 'job': {'needs': None, 'detail': 'Reading calc.py'}},
            {'gate': 'permission'},
            {'state': 'done'},
            None,
        ],
        'items': ['a', 2],
        'bare': None,
    }


def test_empty_document_is_none() -> None:
    assert loads('# nothing here\n\n') is None


def test_top_level_sequence() -> None:
    assert loads('- 1\n- two\n') == [1, 'two']


@pytest.mark.parametrize(
    ('text', 'message'),
    [
        ('a:\n\tb: 1\n', 'tabs'),
        ('a: 1\na: 2\n', 'duplicate key'),
        ('needs: approve Bash: rm\n', 'quote this value'),
        ('a: [1, 2]\n', 'quote this value'),
        ('a: 1\n  b: 2\n', 'unexpected indentation'),
        ('a:\n  - 1\n    - 2\n', 'unexpected indentation'),
        ('- 1\n  - 2\n', 'unexpected indentation'),
        ('a: "unterminated\n', 'bad double-quoted string'),
        ("a: 'unterminated\n", 'unterminated single-quoted string'),
        ('just words\n', 'expected `key: value`'),
    ],
)
def test_outside_the_subset_is_a_readable_error(text: str, message: str) -> None:
    with pytest.raises(YamlError, match=message):
        loads(text)
