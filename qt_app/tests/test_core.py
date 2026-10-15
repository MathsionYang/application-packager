import json
from pathlib import Path

import pytest

from packager.models import BundleConfig, RuntimeInstaller
from packager.store import AppStore
from packager.validation import EntrySelectionRequired, ValidationError, default_runtime_params, validate_config


@pytest.fixture
def config(tmp_path):
    source = tmp_path / '功能软件'
    source.mkdir()
    (source / 'main.exe').write_bytes(b'fixture, never execute')
    (source / 'data').mkdir()
    (source / 'data' / 'settings.json').write_text('{}')
    compiler = tmp_path / 'ISCC.exe'
    compiler.write_bytes(b'fixture, never execute')
    return BundleConfig(paths={'C': str(source)}, output=str(tmp_path / 'output'), compiler=str(compiler))


def test_prototype_config_roundtrip_keeps_paths_runtime_order_and_entries():
    data = {'paths': {'C': 'D:/产品'}, 'output': 'D:/输出', 'name': '套装', 'version': '2.0',
            'runtimes': [{'path': 'D:/vc_redist.x64.exe', 'params': '/quiet'}, {'path': 'D:/other.msi', 'params': ''}],
            'entries': {'C': 'bin/main.exe'}}
    config = BundleConfig.from_dict(data)
    assert config.runtimes[0].path == Path('D:/vc_redist.x64.exe')
    assert config.to_dict()['entries'] == data['entries']
    assert BundleConfig.from_dict(config.to_dict()) == config
    assert config.runtimes[0].success_codes == (0, 3010)


def test_valid_distribution_resolves_entry_without_creating_output(config):
    result = validate_config(config)
    assert [software.role for software in result] == ['C']
    assert result[0].entry_relative == Path('main.exe')
    assert result[0].entry_path.is_file()
    assert not Path(config.output).exists()


def test_lightweight_validation_does_not_open_distribution_dependencies(config, monkeypatch):
    source = Path(config.paths['C'])
    dependency = source / 'data' / 'settings.json'
    original_open = Path.open
    def unreadable_dependency(path, *args, **kwargs):
        if path == dependency:
            raise PermissionError('dependency deliberately unavailable during preflight')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', unreadable_dependency)
    resolved = validate_config(config, check_files=False)
    assert resolved[0].entry_relative == Path('main.exe')
    assert not Path(config.output).exists()
    with pytest.raises(ValidationError, match='无法读取'):
        validate_config(config)


@pytest.mark.parametrize('issue', ['missing_entry', 'escaped_entry', 'output_inside_source'])
def test_lightweight_validation_still_checks_entry_and_path_boundaries(config, issue):
    if issue == 'missing_entry':
        config.entries['C'] = 'missing.exe'
    elif issue == 'escaped_entry':
        config.entries['C'] = '../ISCC.exe'
    else:
        config.output = str(Path(config.paths['C']) / 'out')
    with pytest.raises(ValidationError):
        validate_config(config, check_files=False)


def test_main_exe_input_includes_full_distribution_directory(config):
    config.paths['C'] = str(Path(config.paths['C']) / 'main.exe')
    assert validate_config(config)[0].source_dir == Path(config.paths['C']).parent


def test_ambiguous_entry_requires_explicit_selection(config):
    source = Path(config.paths['C'])
    (source / 'helper.exe').write_bytes(b'fixture')
    with pytest.raises(EntrySelectionRequired) as error:
        validate_config(config)
    assert error.value.role == 'C'
    assert len(error.value.candidates) == 2
    config.entries['C'] = 'main.exe'
    assert validate_config(config)[0].entry_relative == Path('main.exe')


def test_nested_entry_is_supported_but_cannot_escape_distribution(config, tmp_path):
    source = Path(config.paths['C'])
    (source / 'bin').mkdir()
    (source / 'bin' / 'nested.exe').write_bytes(b'fixture')
    config.entries['C'] = 'bin/nested.exe'
    assert validate_config(config)[0].entry_relative == Path('bin/nested.exe')
    config.entries['C'] = '../ISCC.exe'
    with pytest.raises(ValidationError, match='入口'):
        validate_config(config)


@pytest.mark.parametrize('issue', ['missing_c', 'bad_optional', 'output_inside_source', 'source_inside_output', 'same_sources', 'bad_compiler', 'bad_version'])
def test_invalid_config_is_rejected_before_build(config, issue):
    if issue == 'missing_c':
        config.paths['C'] = ''
    elif issue == 'bad_optional':
        config.paths['A'] = 'Z:/does-not-exist'
    elif issue == 'output_inside_source':
        config.output = str(Path(config.paths['C']) / 'out')
    elif issue == 'source_inside_output':
        config.output = str(Path(config.paths['C']).parent)
    elif issue == 'same_sources':
        config.paths['A'] = config.paths['C']
    elif issue == 'bad_compiler':
        config.compiler = 'Z:/missing.exe'
    elif issue == 'bad_version':
        config.auto_version = False
        config.version = '1.0\n[Run]'
    with pytest.raises(ValidationError):
        validate_config(config)


def test_runtime_validation_rejects_missing_noninstaller_and_duplicates(config, tmp_path):
    runtime = tmp_path / 'vc_redist.x64.exe'
    runtime.write_bytes(b'fixture')
    config.runtimes = [RuntimeInstaller(runtime)]
    assert validate_config(config)
    config.runtimes.append(RuntimeInstaller(runtime))
    with pytest.raises(ValidationError, match='重复'):
        validate_config(config)
    config.runtimes = [RuntimeInstaller(tmp_path / 'missing.exe')]
    with pytest.raises(ValidationError):
        validate_config(config)
    text_file = tmp_path / 'installer.txt'
    text_file.write_text('x')
    config.runtimes = [RuntimeInstaller(text_file)]
    with pytest.raises(ValidationError):
        validate_config(config)


def test_unknown_runtime_does_not_get_silent_parameters():
    assert default_runtime_params('custom.exe') == ''
    assert default_runtime_params('vc_redist.x64.exe') == '/install /quiet /norestart'
    assert default_runtime_params('runtime.msi') == '/qn /norestart'


def test_store_persists_unicode_config_and_bounded_latest_first_history(tmp_path):
    store = AppStore(tmp_path / 'state')
    config = BundleConfig(name='产品套装', paths={'C': 'D:/发布目录'})
    store.save_config(config)
    assert store.load_config() == config
    for i in range(35):
        store.add_history({'id': str(i), 'name': '任务'})
    history = store.load_history()
    assert len(history) == 30
    assert history[0]['id'] == '34'
    assert history[-1]['id'] == '5'


def test_corrupt_store_falls_back_and_preserves_original(tmp_path):
    store = AppStore(tmp_path)
    invalid = '{broken'
    (tmp_path / 'config.json').write_text(invalid, encoding='utf-8')
    assert store.load_config() == BundleConfig()
    store.save_config(BundleConfig(name='恢复'))
    backups = list(tmp_path.glob('config.json.corrupt-*'))
    assert backups and backups[0].read_text(encoding='utf-8') == invalid
    assert json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))['name'] == '恢复'


def test_history_with_corrupt_nested_schema_is_quarantined(tmp_path):
    (tmp_path / 'history.json').write_text('[{"id":"x","config":null,"logs":[]}]', encoding='utf-8')
    store = AppStore(tmp_path)
    assert store.load_history() == []
    assert list(tmp_path.glob('history.json.corrupt-*'))
