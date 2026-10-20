import json
import sys

import pytest

from sentinel.credentials import main
from sentinel.security import KeyStore


def test_issue_rotate_and_revoke_without_disclosing_token(tmp_path, monkeypatch, capsys):
    keys, token_file = tmp_path/'keys.json', tmp_path/'token'
    def run(*args):
        monkeypatch.setattr(sys, 'argv', ['credentials', '--keys-file', str(keys), *args])
        main()
    run('issue', '--name', 'first', '--tenant', 'test', '--role', 'scorer',
        '--token-file', str(token_file))
    token = token_file.read_text().strip()
    store = KeyStore(keys, require_expiry=True)
    assert store.authenticate(token).role == 'scorer'
    assert token not in capsys.readouterr().out
    assert token not in keys.read_text()
    assert token_file.stat().st_mode & 0o777 == 0o600
    before = keys.read_bytes()
    with pytest.raises(FileExistsError):
        run('issue', '--name', 'second', '--tenant', 'test', '--role', 'scorer',
            '--token-file', str(token_file))
    assert keys.read_bytes() == before
    run('revoke', '--name', 'first', '--tenant', 'test')
    assert store.authenticate(token) is None
    assert json.loads(keys.read_text())[0]['revoked'] is True
