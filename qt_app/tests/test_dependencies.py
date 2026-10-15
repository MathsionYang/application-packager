import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import threading

import pytest

from packager.models import BundleConfig, BuildResult


def api():
    try:
        return importlib.import_module('packager.dependencies')
    except ModuleNotFoundError:
        pytest.fail('Dependency hashing and persistent cache API is not implemented')


@pytest.fixture
def distribution(tmp_path):
    root = tmp_path / 'release'
    (root / 'plugins').mkdir(parents=True)
    (root / 'a.dll').write_bytes(b'first dependency')
    (root / 'plugins' / 'a.dll').write_bytes(b'second dependency')
    (root / 'main.exe').write_bytes(b'MZ compile-only fixture')
    return root


def hashed(root, paths=None):
    return api().hash_selected(root, paths or ['a.dll', 'plugins/a.dll'], threading.Event())


def test_dependency_config_roundtrip_and_old_config_defaults():
    config = BundleConfig.from_dict({'dependency_files': {'C': ['plugins/a.dll']},
                                     'dependency_records': {'C': 'portable.json'}, 'hash_workers': 4})
    assert config.dependency_files == {'C': ['plugins/a.dll']}
    assert config.dependency_records == {'C': 'portable.json'}
    assert config.hash_workers == 4
    assert BundleConfig.from_dict(config.to_dict()) == config
    old = BundleConfig.from_dict({})
    assert old.dependency_files == {} and old.dependency_records == {} and old.hash_workers == 2
    assert BuildResult(Path('bundle.exe'), Path('records'), Path('manifest.json'), 'id', ['C'], 0).dependency_records == {}


@pytest.mark.parametrize('data', [
    {'dependency_files': {'C': '../escape.dll'}}, {'dependency_files': {'C': ['../escape.dll']}},
    {'dependency_files': {'X': ['a.dll']}}, {'dependency_files': {'C': ['a.txt']}},
    {'dependency_files': {'C': ['a.dll', 'A.dll']}}, {'dependency_records': {'C': 12}},
    {'dependency_records': {'X': 'record.json'}}, {'hash_workers': True},
    {'hash_workers': 0}, {'hash_workers': 9},
])
def test_dependency_config_rejects_invalid_selection_and_worker_values(data):
    with pytest.raises(ValueError):
        BundleConfig.from_dict(data)


def test_scan_files_reports_all_relative_files_in_batches_without_opening_contents(distribution, monkeypatch):
    module = api()
    original_open = Path.open
    def forbid_source_reads(path, *args, **kwargs):
        assert not path.is_relative_to(distribution), 'Scanning must only read metadata'
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', forbid_source_reads)
    batches = []
    files = module.scan_files(distribution, threading.Event(), batches.append)
    assert {item['path'] for item in files} == {'a.dll', 'plugins/a.dll', 'main.exe'}
    assert [item for batch in batches for item in batch] == files
    assert all(set(item) == {'path', 'size', 'mtime_ns'} for item in files)


def test_hash_selected_distinguishes_same_basename_and_reports_exact_progress(distribution):
    progress = []
    callback_threads = []
    caller_thread = threading.get_ident()
    def report(*value):
        progress.append(value)
        callback_threads.append(threading.get_ident())
    files = api().hash_selected(distribution, ['plugins\\a.dll', 'a.dll'], threading.Event(), workers=2,
                                progress=report)
    by_path = {item['path']: item for item in files}
    assert set(by_path) == {'a.dll', 'plugins/a.dll'}
    assert by_path['a.dll']['sha256'] == hashlib.sha256(b'first dependency').hexdigest()
    assert by_path['plugins/a.dll']['sha256'] == hashlib.sha256(b'second dependency').hexdigest()
    total = len(b'first dependency') + len(b'second dependency')
    assert progress[-1] == (total, total, 2, 2)
    assert all(len(value) == 4 for value in progress)
    assert set(callback_threads) == {caller_thread}


def test_hash_selected_rehashes_even_when_size_and_mtime_are_unchanged(distribution):
    before = hashed(distribution)
    path = distribution / 'a.dll'
    old_stat = path.stat()
    path.write_bytes(b'other dependency')
    os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    after = hashed(distribution)
    assert before[0]['size'] == after[0]['size']
    assert before[0]['mtime_ns'] == after[0]['mtime_ns']
    assert before[0]['sha256'] != after[0]['sha256']


@pytest.mark.parametrize('paths', [['../outside.dll'], ['D:/outside.dll'], ['/outside.dll'],
                                   ['main.exe'], ['a.dll', 'A.dll'], ['plugins//a.dll'], ['plugins/./a.dll']])
def test_hash_selected_rejects_unsafe_or_ambiguous_paths(distribution, paths):
    with pytest.raises(ValueError):
        api().hash_selected(distribution, paths, threading.Event())


def test_hash_selected_detects_changes_during_streaming(distribution):
    path = distribution / 'a.dll'
    path.write_bytes(b'x' * (3 * 1024 * 1024))
    changed = False
    def mutate(done_bytes, total_bytes, done_files, total_files):
        nonlocal changed
        if done_bytes and not changed:
            changed = True
            with path.open('ab') as stream:
                stream.write(b'changed')
    with pytest.raises(RuntimeError, match='变化|变更'):
        api().hash_selected(distribution, ['a.dll'], threading.Event(), workers=1, progress=mutate)


def test_hash_selected_cancellation_stops_streaming(distribution):
    from packager.builder import BuildCancelled
    cancel = threading.Event()
    def stop(done_bytes, total_bytes, done_files, total_files):
        if done_bytes:
            cancel.set()
    with pytest.raises(BuildCancelled):
        api().hash_selected(distribution, ['a.dll'], cancel, progress=stop)


@pytest.mark.parametrize('broken', [False, True])
def test_dependency_operations_reject_symlinks(distribution, tmp_path, broken):
    target = tmp_path / 'outside.dll'
    if not broken:
        target.write_bytes(b'outside')
    try:
        (distribution / 'link.dll').symlink_to(target)
    except OSError as error:
        pytest.skip(f'Symlinks unavailable: {error}')
    with pytest.raises(ValueError):
        api().scan_files(distribution, threading.Event())
    with pytest.raises(ValueError):
        api().hash_selected(distribution, ['link.dll'], threading.Event())


def test_cache_key_uses_contents_paths_and_recipe_but_not_mtime(distribution):
    module = api()
    files = hashed(distribution)
    recipe = {'extractor_schema': 1, 'architecture': 'x64', 'compression': {'method': 'lzma2', 'level': 'fast'}}
    key = module.cache_key(files, recipe)
    moved_metadata = [{**file, 'mtime_ns': file['mtime_ns'] + 10} for file in reversed(files)]
    assert module.cache_key(moved_metadata, recipe) == key
    assert module.cache_key(files[:1], recipe) != key
    assert module.cache_key([{**files[0], 'path': 'renamed.dll'}, files[1]], recipe) != key
    assert module.cache_key(files, {**recipe, 'architecture': 'x86'}) != key


def test_cache_publish_find_and_corrupt_repair_preserve_old_generations(distribution, tmp_path):
    module = api()
    files, recipe = hashed(distribution), {'extractor_schema': 1, 'productAppId': 'stable-product'}
    key = module.cache_key(files, recipe)
    exe = tmp_path / 'new.exe'
    exe.write_bytes(b'MZ dependency extractor fixture')
    cache = module.DependencyCache(tmp_path / 'cache')
    first = cache.publish(key, files, recipe, 'C', str(distribution), exe, threading.Event())
    found = cache.find(key, files, recipe, threading.Event())
    assert found is not None and found['package']['sha256'] == hashlib.sha256(exe.read_bytes()).hexdigest()
    assert Path(first['package']['path']).is_absolute()
    old_package = Path(first['package']['path'])
    old_package.write_bytes(b'MZ corrupt old generation')
    assert cache.find(key, files, recipe, threading.Event()) is None
    second = cache.publish(key, files, recipe, 'C', str(distribution), exe, threading.Event())
    assert second['package']['path'] != first['package']['path']
    assert old_package.read_bytes() == b'MZ corrupt old generation'
    assert cache.find(key, files, recipe, threading.Event())['package'] == second['package']


def test_imported_record_cannot_reuse_old_package_for_changed_dll_or_recipe(distribution, tmp_path):
    module = api()
    files, recipe = hashed(distribution), {'extractor_schema': 1}
    exe = tmp_path / 'extractor.exe'
    exe.write_bytes(b'MZ fixture')
    cache = module.DependencyCache(tmp_path / 'cache')
    record = cache.publish(module.cache_key(files, recipe), files, recipe, 'C', str(distribution), exe, threading.Event())
    (distribution / 'a.dll').write_bytes(b'changed dependency')
    current = hashed(distribution)
    assert cache.find(module.cache_key(current, recipe), current, recipe, threading.Event(), imported_record=record) is None
    new_recipe = {'extractor_schema': 2}
    assert cache.find(module.cache_key(files, new_recipe), files, new_recipe, threading.Event(), imported_record=record) is None
    Path(record['package']['path']).unlink()
    assert cache.find(record['cache_key'], files, recipe, threading.Event(), imported_record=record) is None


def test_portable_record_moves_with_its_package_and_checks_role(distribution, tmp_path):
    module = api()
    files, recipe = hashed(distribution), {'extractor_schema': 1}
    exe = tmp_path / 'extractor.exe'
    exe.write_bytes(b'MZ fixture')
    cache = module.DependencyCache(tmp_path / 'cache')
    record = cache.publish(module.cache_key(files, recipe), files, recipe, 'C', str(distribution), exe, threading.Event())
    export = tmp_path / 'export'
    export.mkdir()
    path = export / 'dependency.json'
    module.save_record(path, record, portable=True, cancel=threading.Event())
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert not Path(raw['package']['path']).is_absolute()
    assert raw['package']['path'].startswith('dependency.files/')
    moved = tmp_path / 'moved'
    shutil.move(str(export), moved)
    loaded = module.load_record(moved / path.name, expected_role='C')
    assert Path(loaded['package']['path']).read_bytes() == exe.read_bytes()
    assert cache.find(record['cache_key'], files, recipe, threading.Event(), imported_record=loaded) is not None
    with pytest.raises(ValueError):
        module.load_record(moved / path.name, expected_role='A')


def test_cancelled_portable_save_leaves_previous_record_and_package(distribution, tmp_path):
    from packager.builder import BuildCancelled
    module = api()
    files, recipe = hashed(distribution), {'extractor_schema': 1}
    exe = tmp_path / 'extractor.exe'
    exe.write_bytes(b'MZ fixture')
    cache = module.DependencyCache(tmp_path / 'cache')
    record = cache.publish(module.cache_key(files, recipe), files, recipe, 'C', str(distribution), exe, threading.Event())
    path = tmp_path / 'dependency.json'
    module.save_record(path, record, portable=True)
    previous = path.read_bytes()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(BuildCancelled):
        module.save_record(path, record, portable=True, cancel=cancel)
    assert path.read_bytes() == previous
    assert Path(module.load_record(path)['package']['path']).read_bytes() == exe.read_bytes()


def test_cancelled_publish_does_not_replace_valid_cache_record(distribution, tmp_path):
    from packager.builder import BuildCancelled
    module = api()
    files, recipe = hashed(distribution), {'extractor_schema': 1}
    exe = tmp_path / 'extractor.exe'
    exe.write_bytes(b'MZ fixture')
    cache = module.DependencyCache(tmp_path / 'cache')
    key = module.cache_key(files, recipe)
    record = cache.publish(key, files, recipe, 'C', str(distribution), exe, threading.Event())
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(BuildCancelled):
        cache.publish(key, files, recipe, 'C', str(distribution), exe, cancel)
    assert cache.find(key, files, recipe, threading.Event())['package'] == record['package']


def test_selection_record_roundtrips_without_a_package(distribution, tmp_path):
    module = api()
    files = [item for item in module.scan_files(distribution, threading.Event()) if item['path'].endswith('.dll')]
    record = {'schema_version': 1, 'kind': 'selection', 'role': 'C', 'source_hint': str(distribution),
              'files': files, 'recipe': None, 'cache_key': None, 'package': None}
    path = tmp_path / 'selection.json'
    module.save_record(path, record)
    loaded = module.load_record(path, expected_role='C')
    assert loaded['kind'] == 'selection' and loaded['package'] is None
    assert {item['path'] for item in loaded['files']} == {'a.dll', 'plugins/a.dll'}


@pytest.mark.parametrize('corruption', ['traversal', 'duplicate', 'sha', 'key', 'schema'])
def test_record_validation_rejects_corrupt_content(distribution, tmp_path, corruption):
    module = api()
    files, recipe = hashed(distribution), {'extractor_schema': 1}
    record = {'schema_version': 1, 'kind': 'dependency_package', 'role': 'C', 'source_hint': '',
              'files': files, 'recipe': recipe, 'cache_key': module.cache_key(files, recipe),
              'package': {'path': 'dependencies.exe', 'size': 10, 'sha256': 'a' * 64}}
    if corruption == 'traversal':
        record['files'][0]['path'] = '../outside.dll'
    elif corruption == 'duplicate':
        record['files'].append(dict(record['files'][0]))
    elif corruption == 'sha':
        record['files'][0]['sha256'] = 'not-a-sha'
    elif corruption == 'key':
        record['cache_key'] = '0' * 64
    else:
        record['schema_version'] = True
    path = tmp_path / 'invalid.json'
    path.write_text(json.dumps(record), encoding='utf-8')
    with pytest.raises(ValueError):
        module.load_record(path)


def test_portable_save_rejects_changed_package_without_replacing_previous_export(distribution, tmp_path):
    module = api()
    files, recipe = hashed(distribution), {'extractor_schema': 1}
    exe = tmp_path / 'extractor.exe'
    exe.write_bytes(b'MZ fixture')
    cache = module.DependencyCache(tmp_path / 'cache')
    record = cache.publish(module.cache_key(files, recipe), files, recipe, 'C', str(distribution), exe, threading.Event())
    path = tmp_path / 'dependency.json'
    module.save_record(path, record, portable=True)
    previous_json = path.read_bytes()
    previous_package = Path(module.load_record(path)['package']['path'])
    Path(record['package']['path']).write_bytes(b'MZ changed fixture')
    with pytest.raises(ValueError, match='SHA-256'):
        module.save_record(path, record, portable=True)
    assert path.read_bytes() == previous_json
    assert previous_package.read_bytes() == b'MZ fixture'
    assert not list((tmp_path / 'dependency.files').rglob('.tmp-*'))


def test_cancellation_after_copy_preserves_existing_cache_pointer(distribution, tmp_path, monkeypatch):
    from packager.builder import BuildCancelled
    module = api()
    files, recipe = hashed(distribution), {'extractor_schema': 1}
    exe = tmp_path / 'extractor.exe'
    exe.write_bytes(b'MZ fixture')
    cache = module.DependencyCache(tmp_path / 'cache')
    key = module.cache_key(files, recipe)
    first = cache.publish(key, files, recipe, 'C', str(distribution), exe, threading.Event())
    cancel = threading.Event()
    digest = module._package_digest
    def cancel_after_copy(*args, **kwargs):
        result = digest(*args, **kwargs)
        cancel.set()
        return result
    monkeypatch.setattr(module, '_package_digest', cancel_after_copy)
    with pytest.raises(BuildCancelled):
        cache.publish(key, files, recipe, 'C', str(distribution), exe, cancel)
    assert module.load_record(cache.root / key / 'latest.json')['package'] == first['package']
    assert not list(cache.root.rglob('.tmp-*'))


def test_deferred_cache_publish_is_not_visible_until_activation(distribution, tmp_path):
    module = api()
    files, recipe = hashed(distribution), {'extractor_schema': 1}
    exe = tmp_path / 'extractor.exe'
    exe.write_bytes(b'MZ fixture')
    cache = module.DependencyCache(tmp_path / 'cache')
    key = module.cache_key(files, recipe)
    record = cache.publish(key, files, recipe, 'C', str(distribution), exe, threading.Event(), activate=False)
    assert Path(record['package']['path']).is_file()
    assert cache.find(key, files, recipe, threading.Event()) is None
    cache.activate(record)
    assert cache.find(key, files, recipe, threading.Event())['package'] == record['package']


def test_cancelled_activation_preserves_previous_generation(distribution, tmp_path):
    from packager.builder import BuildCancelled
    module = api()
    files, recipe = hashed(distribution), {'extractor_schema': 1}
    exe = tmp_path / 'extractor.exe'
    exe.write_bytes(b'MZ fixture')
    cache = module.DependencyCache(tmp_path / 'cache')
    key = module.cache_key(files, recipe)
    first = cache.publish(key, files, recipe, 'C', str(distribution), exe, threading.Event())
    candidate = cache.publish(key, files, recipe, 'C', str(distribution), exe, threading.Event(), activate=False)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(BuildCancelled):
        cache.activate(candidate, cancel)
    assert cache.find(key, files, recipe, threading.Event())['package'] == first['package']


def test_activation_rejects_packages_from_a_different_cache(distribution, tmp_path):
    module = api()
    files, recipe = hashed(distribution), {'extractor_schema': 1}
    exe = tmp_path / 'extractor.exe'
    exe.write_bytes(b'MZ fixture')
    cache = module.DependencyCache(tmp_path / 'cache')
    key = module.cache_key(files, recipe)
    record = cache.publish(key, files, recipe, 'C', str(distribution), exe, threading.Event())
    other = module.DependencyCache(tmp_path / 'other-cache')
    with pytest.raises(ValueError):
        other.activate(record)
    assert not other.root.exists()
