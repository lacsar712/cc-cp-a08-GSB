import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import zone_allowed, zone_rejection_message

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


_JSON_ERRORS = {
    400: web.HTTPBadRequest,
    401: web.HTTPUnauthorized,
    403: web.HTTPForbidden,
    404: web.HTTPNotFound,
    409: web.HTTPConflict,
}


def _json_error(status: int, detail: str) -> web.HTTPException:
    return _JSON_ERRORS[status](
        text=json.dumps({"detail": detail}, ensure_ascii=False),
        content_type="application/json",
    )


def _auth_header(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise _json_error(401, "未登录")
    return user


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise _json_error(403, "仅记录员可提交读数")
    return user


def require_roster_keeper(request: web.Request) -> dict:
    """名册只能由记录员维护；值班员可看名册、退回样例与履历，但禁止改册。"""
    user = require_user(request)
    if user["role"] != "writer":
        raise _json_error(403, "仅记录员可维护名册，值班员只读")
    return user


def _client_path(request: web.Request) -> str:
    """退回样例来源：网页提交走 X-Client: web，其余视为直连请求。"""
    return "web" if request.headers.get("X-Client") == "web" else "api"


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "coldchain-probe-desk"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise _json_error(401, "用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


# ---- 开放名册：记录员维护，值班员只读 ----


async def list_roster(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        "SELECT zone_code, created_by, created_at FROM zone_roster ORDER BY zone_code"
    )
    return web.json_response(
        [
            {
                "zone_code": r["zone_code"],
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    )


async def add_roster_zone(request: web.Request) -> web.Response:
    user = require_roster_keeper(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    zone_code = str(body.get("zone_code", "")).strip()
    if not zone_code:
        raise _json_error(400, "库区编码不能为空")

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 名册新增与履历写入同一事务，原子完成
            inserted = await conn.fetchval(
                """
                INSERT INTO zone_roster (zone_code, created_by)
                VALUES ($1, $2)
                ON CONFLICT (zone_code) DO NOTHING
                RETURNING zone_code
                """,
                zone_code,
                user["username"],
            )
            if not inserted:
                raise _json_error(409, f"库区「{zone_code}」已在开放名册内")
            await conn.execute(
                """
                INSERT INTO zone_roster_events (action, zone_code, actor)
                VALUES ('add', $1, $2)
                """,
                zone_code,
                user["username"],
            )
    return web.json_response(
        {
            "zone_code": zone_code,
            "created_by": user["username"],
            "message": f"库区「{zone_code}」已加入开放名册",
        },
        status=201,
    )


async def remove_roster_zone(request: web.Request) -> web.Response:
    user = require_roster_keeper(request)
    zone_code = request.match_info["zone_code"].strip()
    if not zone_code:
        raise _json_error(400, "库区编码不能为空")

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 名册删除与履历写入同一事务，原子完成；
            # 只动名册与履历，绝不改写历史读数上的库区编码
            removed = await conn.fetchval(
                "DELETE FROM zone_roster WHERE zone_code = $1 RETURNING zone_code",
                zone_code,
            )
            if not removed:
                raise _json_error(404, f"名册中没有库区「{zone_code}」")
            await conn.execute(
                """
                INSERT INTO zone_roster_events (action, zone_code, actor)
                VALUES ('remove', $1, $2)
                """,
                zone_code,
                user["username"],
            )
    return web.json_response(
        {"zone_code": zone_code, "message": f"库区「{zone_code}」已移出开放名册"}
    )


async def list_roster_events(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, action, zone_code, actor, created_at
        FROM zone_roster_events
        ORDER BY id DESC
        """
    )
    return web.json_response(
        [
            {
                "id": r["id"],
                "action": r["action"],
                "zone_code": r["zone_code"],
                "actor": r["actor"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    )


async def list_roster_rejections(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, path, zone_code, probe_id, temp_c, message, attempted_by, created_at
        FROM zone_rejections
        ORDER BY id DESC
        """
    )
    return web.json_response(
        [
            {
                "id": r["id"],
                "path": r["path"],
                "zone_code": r["zone_code"],
                "probe_id": r["probe_id"],
                "temp_c": r["temp_c"],
                "message": r["message"],
                "attempted_by": r["attempted_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    )


# ---- 探头读数 ----


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, probe_id, zone_code, temp_c, verdict, reason, status, created_by, created_at, processed_at
        FROM probe_readings
        ORDER BY id DESC
        """
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "probe_id": r["probe_id"],
                "zone_code": r["zone_code"],
                "temp_c": r["temp_c"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "status": r["status"],
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
            }
        )
    return web.json_response(out)


async def _record_rejection(
    pool: asyncpg.Pool,
    *,
    path: str,
    zone_code: str,
    probe_id: str,
    temp_c: float | None,
    attempted_by: str,
) -> str:
    """退回样例与校验共用同一套话术，记录后原样返回。"""
    message = zone_rejection_message(zone_code)
    await pool.execute(
        """
        INSERT INTO zone_rejections (path, zone_code, probe_id, temp_c, message, attempted_by)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        path,
        zone_code,
        probe_id,
        temp_c,
        message,
        attempted_by,
    )
    return message


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise _json_error(400, "探头编号不能为空")
    zone_code = str(body.get("zone_code", "")).strip()
    if not zone_code:
        raise _json_error(400, "库区编码不能为空")
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise _json_error(400, "温度必须是数字") from exc

    pool: asyncpg.Pool = request.app["pool"]

    # 名册校验（与网页、落库触发器共用 rules 里的同一套规则与话术）
    roster_codes = {
        r["zone_code"] for r in await pool.fetch("SELECT zone_code FROM zone_roster")
    }
    if not zone_allowed(zone_code, roster_codes):
        message = await _record_rejection(
            pool,
            path=_client_path(request),
            zone_code=zone_code,
            probe_id=probe_id,
            temp_c=temp_c,
            attempted_by=user["username"],
        )
        raise _json_error(400, message)

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO probe_readings (probe_id, zone_code, temp_c, status, created_by, created_at)
            VALUES ($1, $2, $3, 'pending', $4, now())
            RETURNING id, probe_id, zone_code, temp_c, verdict, reason, status, created_by, created_at, processed_at
            """,
            probe_id,
            zone_code,
            temp_c,
            user["username"],
        )
    except asyncpg.RaiseError:
        # 落库前触发器兜底：名册在校验后被改动时，仍以同一套话术退回
        message = await _record_rejection(
            pool,
            path=_client_path(request),
            zone_code=zone_code,
            probe_id=probe_id,
            temp_c=temp_c,
            attempted_by=user["username"],
        )
        raise _json_error(400, message)
    return web.json_response(
        {
            "id": row["id"],
            "probe_id": row["probe_id"],
            "zone_code": row["zone_code"],
            "temp_c": row["temp_c"],
            "verdict": row["verdict"],
            "reason": row["reason"],
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "processed_at": None,
            "message": "已入队，后台工人将认领并判定",
        },
        status=201,
    )


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)


async def on_cleanup(app: web.Application) -> None:
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/roster", list_roster)
    app.router.add_post("/api/roster", add_roster_zone)
    app.router.add_delete("/api/roster/{zone_code}", remove_roster_zone)
    app.router.add_get("/api/roster/events", list_roster_events)
    app.router.add_get("/api/roster/rejections", list_roster_rejections)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
