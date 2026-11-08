#!/usr/bin/env python3
"""Run with .venv/bin/python scripts/backup.py create|verify|restore."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app import store
from app.backups import create_bundle,verify_bundle,restore_bundle
p=argparse.ArgumentParser(description='Резервирование Баланс: SQLite и файлы вместе')
p.add_argument('command',choices=['create','verify','restore'])
p.add_argument('--archive',type=Path)
p.add_argument('--destination',type=Path)
a=p.parse_args()
try:
    if a.command=='create':
        store.init();result=create_bundle()
    elif not a.archive: p.error('Нужен --archive')
    elif a.command=='verify': result=verify_bundle(a.archive)
    elif not a.destination: p.error('Нужна --destination (новая или пустая директория)')
    else: result=restore_bundle(a.archive,a.destination)
    print(json.dumps(result,ensure_ascii=False,indent=2))
except Exception as exc:
    print(str(exc),file=sys.stderr);sys.exit(1)
