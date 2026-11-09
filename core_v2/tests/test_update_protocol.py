"""Target compatibility must be checked before replacing installed code."""
import json
import zipfile

import pytest

from tools import update_worker as worker


def test_current_checkout_declares_supported_protocol():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    worker.validate_protocol((root / worker.PROTOCOL_PATH).read_bytes())


@pytest.mark.parametrize('content', [
    b'', b'{', b'null', b'[]', b'{}', b'\xff', b' ' * 4097,
    json.dumps({'protocol': 'yeboy-update-session', 'version': True}),
    json.dumps({'protocol': 'yeboy-update-session', 'version': 2}),
    json.dumps({'protocol': 'other', 'version': 1}),
])
def test_rejects_unknown_or_malformed_protocol(content):
    with pytest.raises(worker.UpdateFailure, match='протокол'):
        worker.validate_protocol(content)


@pytest.mark.parametrize('protocol', [None, '{}', json.dumps(worker.PROTOCOL)])
def test_zip_target_requires_explicit_startup_guard_contract(tmp_path, protocol):
    archive = tmp_path / 'source.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        for name in worker.REQUIRED:
            output.writestr('root/' + name, '\n'.join(worker.COMPATIBILITY_MARKERS.get(name, ('code',))))
        if protocol is not None:
            output.writestr('root/' + worker.PROTOCOL_PATH, protocol)
    if protocol == json.dumps(worker.PROTOCOL):
        assert worker.PROTOCOL_PATH in worker.extract_archive(archive, tmp_path / 'stage')
    else:
        with pytest.raises(worker.UpdateFailure, match='протокол'):
            worker.extract_archive(archive, tmp_path / 'stage')


@pytest.mark.parametrize('protocol', [None, '{}'])
def test_git_refuses_incompatible_target_before_transaction_or_switch(tmp_path, monkeypatch, protocol):
    calls = []
    monkeypatch.setattr(worker, 'check_git', lambda root: None)
    monkeypatch.setattr(worker, 'git_tree_files', lambda *a: worker.REQUIRED | ({worker.PROTOCOL_PATH} if protocol is not None else set()))
    def git(root, *args):
        calls.append(args)
        if args == ('rev-parse', 'HEAD'):
            return 'a' * 40
        if args == ('branch', '--show-current'):
            return 'main'
        if args[:1] == ('fetch',):
            return ''
        if args == ('rev-parse', 'FETCH_HEAD'):
            return 'b' * 40
        if args[:1] == ('ls-tree',):
            return '\n'.join(worker.REQUIRED | ({worker.PROTOCOL_PATH} if protocol is not None else set()))
        if args[:1] == ('show',):
            name = args[1].split(':', 1)[1]
            return protocol if name == worker.PROTOCOL_PATH else '\n'.join(worker.COMPATIBILITY_MARKERS[name])
        pytest.fail(f'Unexpected mutation/check: {args}')
    monkeypatch.setattr(worker, 'git', git)
    transaction = tmp_path / 'transaction.json'
    with pytest.raises(worker.UpdateFailure, match='протокол'):
        worker.git_apply(tmp_path, {'branch': 'main', 'sha': 'b' * 40}, transaction)
    assert not transaction.exists()
    assert not any(args[0] == 'switch' for args in calls)
