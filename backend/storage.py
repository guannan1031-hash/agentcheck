"""Tenant-scoped transactional storage; SQLite now, PostgreSQL via DATABASE_URL."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import re

from sqlalchemy import Column, JSON, MetaData, String, Table, create_engine, delete, insert, select, update

metadata = MetaData()
TABLES = {name: Table(name, metadata, Column("id", String(128), primary_key=True), Column("payload", JSON, nullable=False))
          for name in ("store_meta", "knowledge_versions", "knowledge_tasks", "public_faq_daily", "conversations", "drafts", "send_records", "tickets", "received_events", "audit_events", "inventory_snapshots", "inventory_evaluations", "notification_outbox", "damage_cases", "scenario_cases", "scenario_reviews", "scenario_test_reviews", "scenario_task_plans")}
TENANT_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_tenant_context = ContextVar("cs_tenant_id", default=None)


class Transaction:
    def __init__(self, connection, tenant_id):
        self.connection = connection
        self.tenant_id = tenant_id

    def _key(self, key):
        return self.tenant_id + ":" + str(key)

    def _keys(self):
        return self.tenant_id + ":%"

    def get(self, kind, key):
        table = TABLES[kind]
        row = self.connection.execute(select(table.c.payload).where(table.c.id == self._key(key))).first()
        return deepcopy(row[0]) if row else None

    def put(self, kind, key, value):
        table = TABLES[kind]
        if self.get(kind, key) is None:
            self.connection.execute(insert(table).values(id=self._key(key), payload=value))
        else:
            self.connection.execute(update(table).where(table.c.id == self._key(key)).values(payload=value))

    def all(self, kind):
        table = TABLES[kind]
        return [deepcopy(row[0]) for row in self.connection.execute(select(table.c.payload).where(table.c.id.like(self._keys())).order_by(table.c.id))]

    def clear(self, kind):
        table = TABLES[kind]
        self.connection.execute(delete(table).where(table.c.id.like(self._keys())))


class Database:
    def __init__(self, url, default_tenant_id="local-demo"):
        if not TENANT_ID.fullmatch(default_tenant_id):
            raise ValueError("租户标识只能使用小写字母、数字和连字符，且以字母开头。")
        self.default_tenant_id = default_tenant_id
        self.engine = create_engine(url, echo=False, hide_parameters=True, connect_args={"timeout": 20} if url.startswith("sqlite") else {})
        if self.engine.dialect.name not in ("sqlite", "postgresql"):
            raise ValueError("Only SQLite and PostgreSQL are supported")
        metadata.create_all(self.engine)
        # Initialization is single-process; run the provided launcher with one worker.
        with self.engine.begin() as connection:
            tx = Transaction(connection, self.default_tenant_id)
            if tx.get("store_meta", "demo") is None:
                legacy_demo = connection.execute(select(TABLES["store_meta"].c.id).where(TABLES["store_meta"].c.id == "demo")).first()
                if legacy_demo:
                    # V1 stored a single shop without a key namespace. Move its
                    # complete data set into the configured default tenant atomically.
                    for table in TABLES.values():
                        rows = list(connection.execute(select(table.c.id, table.c.payload)))
                        for key, payload in rows:
                            connection.execute(insert(table).values(id=self.default_tenant_id + ":" + key, payload=payload))
                        connection.execute(delete(table).where(~table.c.id.like(self.default_tenant_id + ":%")))
                else:
                    tx.put("store_meta", "demo", {"schema_version": 1, "knowledge_version": 0, "paused": False, "model_calls": 0})

    def set_tenant(self, tenant_id):
        if not TENANT_ID.fullmatch(tenant_id):
            raise ValueError("租户标识格式无效。")
        return _tenant_context.set(tenant_id)

    def reset_tenant(self, token):
        _tenant_context.reset(token)

    @contextmanager
    def transaction(self, tenant_id=None):
        tenant_id = tenant_id or _tenant_context.get() or self.default_tenant_id
        if not TENANT_ID.fullmatch(tenant_id):
            raise ValueError("租户标识格式无效。")
        with self.engine.connect() as connection:
            if self.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
                connection.execute(select(TABLES["store_meta"]).where(TABLES["store_meta"].c.id == tenant_id + ":demo").with_for_update())
            try:
                yield Transaction(connection, tenant_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
