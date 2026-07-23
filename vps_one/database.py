import asyncio
from pathlib import Path
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from .config import settings
from .models import Base, CLICDNode, Instance, Plan, Setting
from .security import decrypt, encrypt

cfg = settings()
db_path = cfg.database_url.split("///")[-1]
Path(db_path).parent.mkdir(parents=True, exist_ok=True)
engine = create_async_engine(cfg.database_url, pool_pre_ping=True, connect_args={"timeout": 15})
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
write_lock = asyncio.Lock()


@event.listens_for(engine.sync_engine, "connect")
def pragmas(conn, _):
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()


MIGRATIONS = {
    "users": {"is_active": "BOOLEAN NOT NULL DEFAULT 1", "last_login_at": "DATETIME"},
    "plans": {
        "slug": "VARCHAR(100)", "features_json": "TEXT NOT NULL DEFAULT '[]'", "stock": "INTEGER NOT NULL DEFAULT -1", "sort_order": "INTEGER NOT NULL DEFAULT 0", "virtualization": "VARCHAR(16) NOT NULL DEFAULT 'lxc'", "clicd_node_id": "INTEGER REFERENCES clicd_nodes(id)", "network_down_mbps": "INTEGER NOT NULL DEFAULT 100", "network_up_mbps": "INTEGER NOT NULL DEFAULT 50", "io_read_mbps": "INTEGER NOT NULL DEFAULT 0", "io_write_mbps": "INTEGER NOT NULL DEFAULT 0", "assign_nat": "BOOLEAN NOT NULL DEFAULT 1", "port_mapping_count": "INTEGER NOT NULL DEFAULT 1", "assign_ipv4": "BOOLEAN NOT NULL DEFAULT 0", "ipv4_count": "INTEGER NOT NULL DEFAULT 0", "assign_ipv6": "BOOLEAN NOT NULL DEFAULT 1", "ipv6_count": "INTEGER NOT NULL DEFAULT 1", "clicd_template_name": "VARCHAR(200) NOT NULL DEFAULT ''", "clicd_validated_at": "DATETIME", "created_at": "DATETIME"
    },
    "orders": {"plan_snapshot": "TEXT NOT NULL DEFAULT '{}'", "fulfilled_at": "DATETIME"},
    "instances": {"clicd_node_id": "INTEGER REFERENCES clicd_nodes(id)", "ipv6": "VARCHAR(100) NOT NULL DEFAULT ''", "management_url": "TEXT NOT NULL DEFAULT ''", "ssh_password": "TEXT NOT NULL DEFAULT ''", "access_json": "TEXT NOT NULL DEFAULT '{}'", "last_synced_at": "DATETIME"},
    "payment_events": {"platform_txn_id": "VARCHAR(150) NOT NULL DEFAULT ''", "verified": "BOOLEAN NOT NULL DEFAULT 0"},
    "jobs": {"payload": "TEXT NOT NULL DEFAULT '{}'", "locked_at": "DATETIME"},
    "audit_logs": {"ip": "VARCHAR(64) NOT NULL DEFAULT ''"},
}


async def migrate(conn):
    for table, columns in MIGRATIONS.items():
        rows = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in rows}
        if not existing:
            continue
        for name, definition in columns.items():
            if name not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
    await conn.execute(text("UPDATE plans SET slug = 'plan-' || id WHERE slug IS NULL OR slug = ''"))
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_plans_slug ON plans(slug)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_user_status ON orders(user_id,status)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_dispatch ON jobs(status,run_after,id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_instances_user_status ON instances(user_id,status)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_plans_clicd_node_id ON plans(clicd_node_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_instances_clicd_node_id ON instances(clicd_node_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_instances_node_container ON instances(clicd_node_id,clicd_id)"))
    await conn.execute(text("PRAGMA optimize"))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await migrate(conn)
    async with SessionLocal() as db:
        node = (await db.execute(select(CLICDNode).order_by(CLICDNode.id).limit(1))).scalar_one_or_none()
        if not node:
            base_setting = await db.get(Setting, "clicd_base_url")
            token_setting = await db.get(Setting, "clicd_token")
            base_url = base_setting.value.strip() if base_setting else ""
            token = token_setting.value if token_setting else ""
            if token_setting and token_setting.encrypted and token:
                token = decrypt(token)
            if base_url and token:
                node = CLICDNode(name="默认节点", base_url=base_url.rstrip("/"), token=encrypt(token), active=True)
                db.add(node)
                await db.flush()
        if node:
            await db.execute(text("UPDATE plans SET clicd_node_id = :node_id WHERE clicd_node_id IS NULL"), {"node_id": node.id})
            await db.execute(text("UPDATE instances SET clicd_node_id = :node_id WHERE clicd_node_id IS NULL"), {"node_id": node.id})
            await db.commit()


async def session():
    async with SessionLocal() as db:
        yield db
