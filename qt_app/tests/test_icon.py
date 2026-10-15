from pathlib import Path

import pytest

from packager import inno
from packager.models import BundleConfig, ResolvedSoftware, RuntimeInstaller
from packager.validation import ValidationError, validate_config


def _config(tmp_path: Path) -> BundleConfig:
    source = tmp_path / 'release'
    source.mkdir()
    (source / 'main.exe').write_bytes(b'fixture')
    compiler = tmp_path / 'ISCC.exe'
    compiler.write_bytes(b'fixture')
    return BundleConfig(paths={'C': str(source)}, output=str(tmp_path / 'output'),
                        compiler=str(compiler))


def test_icon_path_roundtrip_is_persisted():
    config = BundleConfig(icon_path='D:/branding/installer.ico')

    restored = BundleConfig.from_dict(config.to_dict())

    assert restored.icon_path == 'D:/branding/installer.ico'
    assert restored == config


def test_outer_script_uses_selected_icon_as_setup_resource(tmp_path):
    icon = tmp_path / 'installer.ico'
    icon.write_bytes(b'icon')
    child = tmp_path / 'child.exe'
    child.write_bytes(b'MZ fixture')
    script = inno.outer_script(
        'Bundle', '1.0.0',
        [{'role': 'C', 'path': child, 'name': 'Feature', 'version': '1.0.0',
          'app_id': '{2DA60969-CB3D-49C9-91F4-6262F0EFA563}',
          'install_dir': 'Feature-C', 'architecture': 'x64'}],
        [], tmp_path / 'ChineseSimplified.isl', icon_filename=icon)

    assert f'SetupIconFile={icon}' in script


def test_icon_must_be_ico_and_outside_source_and_output(tmp_path):
    config = _config(tmp_path)
    invalid = tmp_path / 'installer.png'
    invalid.write_bytes(b'icon')
    config.icon_path = str(invalid)
    with pytest.raises(ValidationError, match='ICO'):
        validate_config(config, check_files=False)

    source_icon = Path(config.paths['C']) / 'installer.ico'
    source_icon.write_bytes(b'icon')
    config.icon_path = str(source_icon)
    with pytest.raises(ValidationError, match='源目录'):
        validate_config(config, check_files=False)

    output_icon = Path(config.output) / 'installer.ico'
    output_icon.parent.mkdir()
    output_icon.write_bytes(b'icon')
    config.icon_path = str(output_icon)
    with pytest.raises(ValidationError, match='输出目录'):
        validate_config(config, check_files=False)
