import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import judge_temp, zone_rejection_raise_sql

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

# 结构变更与落库前校验触发器。触发器话术由 rules.zone_rejection_raise_sql()
# 生成，与网页、直连两条路径共用同一套退回话术。
SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS zone_code text NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);

CREATE TABLE IF NOT EXISTS zone_roster (
    zone_code text PRIMARY KEY,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS zone_roster_events (
    id serial PRIMARY KEY,
    action text NOT NULL,
    zone_code text NOT NULL,
    actor text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS zone_rejections (
    id serial PRIMARY KEY,
    path text NOT NULL,
    zone_code text NOT NULL,
    probe_id text NOT NULL DEFAULT '',
    temp_c double precision,
    message text NOT NULL,
    attempted_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION enforce_zone_roster() RETURNS trigger AS $func$
BEGIN
    IF NEW.zone_code IS NULL OR NOT EXISTS (
        SELECT 1 FROM zone_roster z WHERE z.zone_code = NEW.zone_code
    ) THEN
        RAISE EXCEPTION '{zone_rejection_raise_sql()}', COALESCE(NEW.zone_code, '');
    END IF;
    RETURN NEW;
END;
$func$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_probe_readings_zone_roster ON probe_readings;
CREATE TRIGGER trg_probe_readings_zone_roster
    BEFORE INSERT OR UPDATE OF zone_code ON probe_readings
    FOR EACH ROW
    EXECUTE FUNCTION enforce_zone_roster();
"""

SEED_ZONES = ["库甲"]

SEED_READINGS = [
    ("探头A01", "库甲", 4.2),
    ("探头B02", "库甲", 12.5),
]


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM zone_roster")
        if not n:
            async with conn.transaction():
                for zone_code in SEED_ZONES:
                    # 名册增删与履历必须原子完成，种子数据同样遵守
                    inserted = await conn.fetchval(
                        """
                        INSERT INTO zone_roster (zone_code, created_by)
                        VALUES ($1, 'system')
                        ON CONFLICT (zone_code) DO NOTHING
                        RETURNING zone_code
                        """,
                        zone_code,
                    )
                    if inserted:
                        await conn.execute(
                            """
                            INSERT INTO zone_roster_events (action, zone_code, actor)
                            VALUES ('add', $1, 'system')
                            """,
                            zone_code,
                        )
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        for probe_id, zone_code, temp_c in SEED_READINGS:
            verdict, reason = judge_temp(temp_c)
            await conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, zone_code, temp_c, verdict, reason, status, created_by, processed_at)
                VALUES ($1, $2, $3, $4, $5, 'done', 'logger', now())
                """,
                probe_id,
                zone_code,
                temp_c,
                verdict,
                reason,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM zone_roster").fetchone()
    if row["n"] == 0:
        with conn.transaction():
            for zone_code in SEED_ZONES:
                inserted = conn.execute(
                    """
                    INSERT INTO zone_roster (zone_code, created_by)
                    VALUES (%s, 'system')
                    ON CONFLICT (zone_code) DO NOTHING
                    RETURNING zone_code
                    """,
                    (zone_code,),
                ).fetchone()
                if inserted:
                    conn.execute(
                        """
                        INSERT INTO zone_roster_events (action, zone_code, actor)
                        VALUES ('add', %s, 'system')
                        """,
                        (zone_code,),
                    )
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    for probe_id, zone_code, temp_c in SEED_READINGS:
        verdict, reason = judge_temp(temp_c)
        conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, zone_code, temp_c, verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, %s, 'done', 'logger', now())
            """,
            (probe_id, zone_code, temp_c, verdict, reason),
        )
    conn.commit()
