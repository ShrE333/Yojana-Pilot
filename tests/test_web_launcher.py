"""The launcher must not open another process's website when binding fails."""
import sys
from unittest.mock import Mock

import pytest

from web import __main__ as launcher


def test_occupied_port_does_not_open_browser_or_start_a_second_server(monkeypatch, capsys):
    listener = Mock()
    listener.bind.side_effect = OSError('address already in use')
    context = Mock()
    context.__enter__ = Mock(return_value=listener)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(launcher.socket, 'socket', Mock(return_value=context))
    open_browser = Mock()
    start_server = Mock()
    monkeypatch.setattr(launcher.webbrowser, 'open', open_browser)
    monkeypatch.setattr(launcher.uvicorn, 'Server', start_server)
    monkeypatch.setattr(sys, 'argv', ['python -m web'])
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 1
    assert 'netstat -ano' in capsys.readouterr().err
    open_browser.assert_not_called()
    start_server.assert_not_called()


@pytest.mark.parametrize('port', ['0', '65536'])
def test_invalid_port_is_rejected(monkeypatch, port):
    monkeypatch.setattr(sys, 'argv', ['python -m web', '--port', port])
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 2
