import os

import asyncpg
import psycopg

from psycopg.rows import dict_row

from rules import judge_temp
from roster_rules import REJECT_DETAIL

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

SEED_ZONE = "库甲"

# 写口规则：任何往 probe_readings 插入库区编码的路径（含绕过接口直连写库、
# 落库前偷改代号）都由该触发器按 zone_roster 校验，退回说法与应用层完全一致。
# 仅在插入时校验；删名册不会改写旧单，旧单库区原样保留。
SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS zone_roster (
    code text PRIMARY KEY,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS roster_audit (
    id serial PRIMARY KEY,
    action text NOT NULL CHECK (action IN ('add', 'remove')),
    zone_code text NOT NULL,
    operator text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_roster_audit_created ON roster_audit (id DESC);

CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    zone_code text,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS zone_code text;
CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);

CREATE OR REPLACE FUNCTION check_zone_in_roster() RETURNS trigger AS $$
BEGIN
    IF NEW.zone_code IS NULL OR btrim(NEW.zone_code) = '' THEN
        RAISE EXCEPTION '{REJECT_DETAIL}' USING ERRCODE = 'P0001';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM zone_roster WHERE code = NEW.zone_code) THEN
        RAISE EXCEPTION '{REJECT_DETAIL}' USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_probe_readings_zone_roster ON probe_readings;
CREATE TRIGGER trg_probe_readings_zone_roster
    BEFORE INSERT ON probe_readings
    FOR EACH ROW EXECUTE FUNCTION check_zone_in_roster();
"""


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


# ---------------- 开放名册 ----------------

def _audit_row(r) -> dict:
    return {
        "id": r["id"],
        "action": r["action"],
        "zone_code": r["zone_code"],
        "operator": r["operator"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


async def list_zones(pool: asyncpg.Pool) -> list[str]:
    rows = await pool.fetch("SELECT code FROM zone_roster ORDER BY code")
    return [r["code"] for r in rows]


async def list_audit(pool: asyncpg.Pool, limit: int = 100) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, action, zone_code, operator, created_at
        FROM roster_audit
        ORDER BY id DESC
        LIMIT $1
        """,
        limit,
    )
    return [
        {
            "id": r["id"],
            "action": r["action"],
            "zone_code": r["zone_code"],
            "operator": r["operator"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


async def add_zone(pool: asyncpg.Pool, code: str, operator: str) -> bool:
    """名册新增与履历写入在同一事务内原子完成。已存在返回 False。"""
    async with pool.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval(
                "SELECT 1 FROM zone_roster WHERE code = $1", code
            )
            if exists:
                return False
            await conn.execute(
                "INSERT INTO zone_roster (code, created_by, created_at) VALUES ($1, $2, now())",
                code,
                operator,
            )
            await conn.execute(
                "INSERT INTO roster_audit (action, zone_code, operator, created_at) "
                "VALUES ('add', $1, $2, now())",
                code,
                operator,
            )
            return True


async def remove_zone(pool: asyncpg.Pool, code: str, operator: str) -> bool:
    """名册删除与履历写入在同一事务内原子完成；旧单库区列不动。"""
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await conn.execute(
                "DELETE FROM zone_roster WHERE code = $1", code
            )
            # asyncpg 返回 'DELETE n'
            count = int(deleted.split()[-1]) if deleted else 0
            if count == 0:
                return False
            await conn.execute(
                "INSERT INTO roster_audit (action, zone_code, operator, created_at) "
                "VALUES ('remove', $1, $2, now())",
                code,
                operator,
            )
            return True


def list_audit_sync(conn, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT id, action, zone_code, operator, created_at "
        "FROM roster_audit ORDER BY id DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [_audit_row(r) for r in rows]


def list_zones_sync(conn) -> list[str]:
    rows = conn.execute("SELECT code FROM zone_roster ORDER BY code").fetchall()
    return [r["code"] for r in rows]


def ensure_seed_zone_sync(conn) -> None:
    """保证初始名册含库甲；仅首次登记时写一条履历。"""
    row = conn.execute(
        "SELECT 1 FROM zone_roster WHERE code = %s", (SEED_ZONE,)
    ).fetchone()
    if row:
        return
    with conn.transaction():
        conn.execute(
            "INSERT INTO zone_roster (code, created_by, created_at) VALUES (%s, 'system', now())",
            (SEED_ZONE,),
        )
        conn.execute(
            "INSERT INTO roster_audit (action, zone_code, operator, created_at) "
            "VALUES ('add', %s, 'system', now())",
            (SEED_ZONE,),
        )


async def ensure_seed_zone_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval(
                "SELECT 1 FROM zone_roster WHERE code = $1", SEED_ZONE
            )
            if exists:
                return
            await conn.execute(
                "INSERT INTO zone_roster (code, created_by, created_at) VALUES ($1, 'system', now())",
                SEED_ZONE,
            )
            await conn.execute(
                "INSERT INTO roster_audit (action, zone_code, operator, created_at) "
                "VALUES ('add', $1, 'system', now())",
                SEED_ZONE,
            )


# ---------------- 种子读数 ----------------

async def seed_if_empty(pool: asyncpg.Pool) -> None:
    await ensure_seed_zone_async(pool)
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        samples = [
            ("探头A01", 4.2, SEED_ZONE),
            ("探头B02", 12.5, SEED_ZONE),
        ]
        for probe_id, temp_c, zone_code in samples:
            verdict, reason = judge_temp(temp_c)
            await conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, zone_code, verdict, reason, status, created_by, processed_at)
                VALUES ($1, $2, $3, $4, $5, 'done', 'logger', now())
                """,
                probe_id,
                temp_c,
                zone_code,
                verdict,
                reason,
            )


def seed_if_empty_sync(conn) -> None:
    ensure_seed_zone_sync(conn)
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        conn.commit()
        return
    samples = [
        ("探头A01", 4.2, SEED_ZONE),
        ("探头B02", 12.5, SEED_ZONE),
    ]
    for probe_id, temp_c, zone_code in samples:
        verdict, reason = judge_temp(temp_c)
        conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, temp_c, zone_code, verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, %s, 'done', 'logger', now())
            """,
            (probe_id, temp_c, zone_code, verdict, reason),
        )
    conn.commit()
