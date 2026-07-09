"""Tests de fumée : le package s'importe et expose sa version."""

import data_analyst_agent


def test_version_exposee():
    assert isinstance(data_analyst_agent.__version__, str)
    assert data_analyst_agent.__version__
