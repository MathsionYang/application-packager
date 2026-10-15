import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from .models import BundleConfig


class AppStore:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir) if base_dir is not None else Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'DesktopPackager'
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _load(self, name: str, default, parse):
        path = self.base_dir / name
        if not path.exists():
            return default
        try:
            return parse(json.loads(path.read_text(encoding='utf-8-sig')))
        except (ValueError, TypeError, KeyError):
            suffix = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
            path.replace(path.with_name(path.name + '.corrupt-' + suffix))
            return default

    def _save(self, name: str, data):
        path = self.base_dir / name
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def load_config(self) -> BundleConfig:
        return self._load('config.json', BundleConfig(), BundleConfig.from_dict)

    def save_config(self, config: BundleConfig) -> None:
        self._save('config.json', config.to_dict())

    def load_history(self) -> list[dict]:
        def parse(data):
            if not isinstance(data, list) or any(not isinstance(record, dict) for record in data):
                raise ValueError('历史记录格式错误')
            for record in data:
                for field in ('id', 'date', 'name', 'status', 'output_file', 'artifact_dir', 'error'):
                    if field in record and not isinstance(record[field], str):
                        raise ValueError('历史记录文本格式错误')
                if 'config' in record:
                    BundleConfig.from_dict(record['config'])
                if 'logs' in record and (not isinstance(record['logs'], list) or any(not isinstance(item, (str, dict)) for item in record['logs'])):
                    raise ValueError('历史记录日志格式错误')
            return data[:30]
        return self._load('history.json', [], parse)

    def add_history(self, record: dict) -> None:
        history = [existing for existing in self.load_history() if not record.get('id') or existing.get('id') != record['id']]
        self._save('history.json', [record, *history][:30])
