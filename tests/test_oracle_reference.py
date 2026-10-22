"""Keep measurements from the executed pinned oracles as offline regressions."""

import json
from pathlib import Path

import pytest

from user.daviscloud.decode import Decoder


@pytest.mark.parametrize('oracle,fixture,index,historic', [
    ('console', 'console', 0, False),
    ('wlip', 'wlip_current', 0, False),
    ('wlip', 'wlip_archive', 1, True),
    ('wll', 'wll_archive', 0, True),
    ('wldc', 'wll_archive', 0, True),
])
def test_executed_oracle_reference(oracle, fixture, index, historic):
    fixtures = Path(__file__).parent / 'fixtures'
    response = json.loads((fixtures / 'oracles.json').read_text())[fixture]
    expected = json.loads((fixtures / 'oracle-results.json').read_text())[oracle]['comparisons'][index]
    decoder = Decoder({})
    if historic:
        actual = next(decoder.archive(response, 1700000000, 1700000200))
    else:
        actual = decoder.current(response, 1700000100)
    for field, value in expected.items():
        assert actual[field] == pytest.approx(value), field
