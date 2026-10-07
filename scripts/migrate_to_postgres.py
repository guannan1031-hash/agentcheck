"""Copy the local validation database to an explicitly configured EMPTY target."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.storage import Database, TABLES, Transaction
from sqlalchemy import create_engine, delete, inspect, insert, select


def migrate(source, target):
    # Both Database objects refer to explicitly supplied test/owned databases.
    with source.transaction() as src, target.transaction() as dst:
        for kind in TABLES:
            if kind != 'store_meta' and dst.all(kind):
                raise ValueError('目标包含业务数据，拒绝覆盖。')
        target_meta = dst.get('store_meta', 'demo')
        if target_meta['knowledge_version'] != 0:
            raise ValueError('目标不是空白初始化库。')
        counts = {}
        for kind, table in TABLES.items():
            rows = list(src.connection.execute(select(table.c.id, table.c.payload)))
            # Database creates one tenant-scoped bootstrap row. Replace it with an
            # exact raw copy so every tenant namespace is retained unchanged.
            dst.connection.execute(delete(table))
            for key, payload in rows:
                dst.connection.execute(insert(table).values(id=key, payload=payload))
            actual = list(dst.connection.execute(select(table.c.id, table.c.payload).order_by(table.c.id)))
            if sorted(rows, key=lambda r:r[0]) != actual:
                raise ValueError('迁移验证不一致，目标事务回滚。')
            counts[kind] = len(rows)
        return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    target_url = os.environ.get('CS_TARGET_DATABASE_URL', '')
    if not target_url.startswith('postgresql+psycopg://'):
        raise SystemExit('请在运行环境提供自己独立空库的CS_TARGET_DATABASE_URL；不要写入项目文件。')
    source_path = ROOT / 'state/local/customer_service.db'
    if not source_path.exists():
        raise SystemExit('本地源数据库不存在。')
    engine = create_engine(target_url, echo=False, hide_parameters=True)
    try:
        with engine.connect() as conn:
            existing = set(inspect(conn).get_table_names())
            if existing:
                raise SystemExit('目标库已有数据表；请使用专用空库。')
        if not args.apply:
            print('只读检查通过：目标可连接且没有数据表。未迁移；停止本地服务并备份后才执行--apply。')
        else:
            source = Database('sqlite:///' + str(source_path))
            target = Database(target_url)
            print('迁移完成，逐表内容核对通过：', migrate(source, target))
            source.engine.dispose()
            target.engine.dispose()
    except SystemExit:
        raise
    except Exception:
        raise SystemExit('数据库连接或迁移失败。未输出连接串；请检查独立空库、权限和网络。') from None
    finally:
        engine.dispose()
