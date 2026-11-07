"""Server-local credentials, deliberately excluded from SQLite and workspace exports."""
import json
import os
import re
import tempfile
from pathlib import Path

from . import domain as d


def secret_path():
    return Path(os.getenv('APP_SECRETS_PATH', str(d.BASE / 'data/.secrets.json')))


def read_settings():
    path = secret_path()
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def get_secret(name):
    return os.getenv(name) or read_settings().get(name, '')


def save_settings(payload):
    d.require(set(payload) <= {'qcc_key', 'deepseek_key', 'model'}, '设置含未知字段')
    settings = read_settings()
    for field, name in [('qcc_key', 'QCC_API_KEY'), ('deepseek_key', 'DEEPSEEK_API_KEY')]:
        value = payload.get(field, '')
        d.require(isinstance(value, str), 'API Key应为文本')
        value = re.sub(r'^Bearer\s+', '', value.strip(), flags=re.I)
        if value:
            d.require(8 <= len(value) <= 512 and not re.search(r'\s', value), 'API Key格式无效')
            d.require('•' not in value and '…' not in value and not value.startswith('***'), '请填写完整的新密钥；已保存的密钥无需重复输入')
            settings[name] = value
    if 'model' in payload:
        d.require(isinstance(payload['model'], str) and re.fullmatch(r'[A-Za-z0-9._-]{1,100}', payload['model']), '模型名称无效')
        settings['model'] = payload['model']
    path = secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.secret-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return status()


def status():
    qcc, deepseek = get_secret('QCC_API_KEY'), get_secret('DEEPSEEK_API_KEY')
    return {'qcc_configured': bool(qcc), 'deepseek_configured': bool(deepseek),
            'qcc_key_hint': ('••••••••' + qcc[-4:]) if qcc else '',
            'deepseek_key_hint': ('••••••••' + deepseek[-4:]) if deepseek else '',
            'model': read_settings().get('model', 'deepseek-flash')}


def redact(text):
    text = str(text)
    for name in ('QCC_API_KEY', 'DEEPSEEK_API_KEY'):
        key = get_secret(name)
        if key:
            text = text.replace(key, '[REDACTED]')
    return text
