"""Consistent SQLite backup without copying a database while it is changing."""
import argparse
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1] / 'state/local/customer_service.db'
if not source.exists():
    raise SystemExit('本地数据库尚未创建。')
if args.output.exists():
    raise SystemExit('备份目标已存在，不覆盖。')
args.output.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(args.output) as dst:
    src.backup(dst)
    assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
print('备份完成且通过SQLite完整性检查。')
