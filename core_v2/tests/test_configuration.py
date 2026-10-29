import pytest
from dotenv import dotenv_values
from tools.launcher.configuration import ConfigurationError, save, wizard


def test_preserves_unknown_settings_and_comments(tmp_path):
    path = tmp_path / '.env.v2'
    path.write_text('# custom\nDATABASE_URL=sqlite+aiosqlite:///custom.db\nDISCORD_TOKEN=old\n', encoding='utf-8')
    save(tmp_path, 'fake.token', '123', 'test')
    assert dotenv_values(path)['DATABASE_URL'] == 'sqlite+aiosqlite:///custom.db'
    assert '# custom' in path.read_text()
    assert dotenv_values(path)['DISCORD_TOKEN'] == 'fake.token'
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('token,guild,sync', [('', '1', 'test'), ('bad\nvalue', '1', 'test'), ('fake', '', 'test'), ('fake', '-1', 'test'), ('fake', 'abc', 'test'), ('fake', '1', 'global')])
def test_invalid_input_preserves_original(tmp_path, token, guild, sync):
    path = tmp_path / '.env.v2'
    path.write_text('DISCORD_TOKEN=old\n')
    with pytest.raises(ConfigurationError):
        save(tmp_path, token, guild, sync)
    assert path.read_text() == 'DISCORD_TOKEN=old\n'


def test_wizard_keeps_token_without_printing_it(tmp_path, monkeypatch, capsys):
    path = tmp_path / '.env.v2'
    path.write_text('DISCORD_TOKEN=fake.secret\nTEST_GUILD_ID=123\n')
    monkeypatch.setattr('getpass.getpass', lambda _: '')
    monkeypatch.setattr('builtins.input', lambda _: '')
    wizard(tmp_path)
    assert dotenv_values(path)['DISCORD_TOKEN'] == 'fake.secret'
    assert 'fake.secret' not in capsys.readouterr().out
