import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import (
    add_zone,
    create_pool,
    ensure_schema_async,
    list_audit,
    list_zones,
    remove_zone,
    seed_if_empty,
)
from roster_rules import (
    REJECT_STATUS,
    evaluate_zone,
    reject_response_payload,
    reject_sample,
)

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


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
        raise web.HTTPUnauthorized(text=json.dumps({"detail": "未登录"}, ensure_ascii=False), content_type="application/json")
    return user


def require_writer(request: web.Request, detail: str = "仅记录员可执行该操作") -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": detail}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


def roster_rejection(zone_code: str) -> web.HTTPError:
    """三路径共用的统一退回：422 + 同一说法与响应体。"""
    return web.HTTPUnprocessableEntity(
        text=json.dumps(reject_response_payload(zone_code), ensure_ascii=False),
        content_type="application/json",
    )


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
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "用户名或密码错误"}, ensure_ascii=False),
            content_type="application/json",
        )
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


READING_COLS = "id, probe_id, temp_c, zone_code, verdict, reason, status, created_by, created_at, processed_at"


def reading_dict(r) -> dict:
    return {
        "id": r["id"],
        "probe_id": r["probe_id"],
        "temp_c": r["temp_c"],
        "zone_code": r["zone_code"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
    }


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        f"""
        SELECT {READING_COLS}
        FROM probe_readings
        ORDER BY id DESC
        """
    )
    return web.json_response([reading_dict(r) for r in rows])


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request, "仅记录员可提交读数")
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    zone_code = str(body.get("zone_code", "")).strip()
    if not probe_id:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "探头编号不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    if not zone_code:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "库区编码不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc

    pool: asyncpg.Pool = request.app["pool"]

    # 第二道：接口写口按名册校验。即使绕过网页直连接口，也在此被统一退回。
    verdict_zone = evaluate_zone(zone_code, await list_zones(pool))
    if not verdict_zone["accepted"]:
        raise roster_rejection(zone_code)
    zone_code = verdict_zone["zone_code"]

    # 第三道：数据库 BEFORE INSERT 触发器按同一名册兜底，
    # 处理通过接口后落库前偷改代号、或校验后名册被删的竞态。
    try:
        row = await pool.fetchrow(
            f"""
            INSERT INTO probe_readings (probe_id, temp_c, zone_code, status, created_by, created_at)
            VALUES ($1, $2, $3, 'pending', $4, now())
            RETURNING {READING_COLS}
            """,
            probe_id,
            temp_c,
            zone_code,
            user["username"],
        )
    except asyncpg.PostgresError as exc:
        if exc.sqlstate == "P0001":
            raise roster_rejection(zone_code) from exc
        raise
    out = reading_dict(row)
    out["message"] = "已入队，后台工人将认领并判定"
    return web.json_response(out, status=201)


# ---------------- 开放名册 ----------------

async def get_roster(request: web.Request) -> web.Response:
    """开放名册落地页数据：名册、退回样例、变更履历。值班员只读也可查看。"""
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    zones = await list_zones(pool)
    audit = await list_audit(pool)
    return web.json_response(
        {"zones": zones, "reject_sample": reject_sample(), "audit": audit}
    )


async def post_roster(request: web.Request) -> web.Response:
    """记录员登记新库区；名册写入与履历原子完成。"""
    user = require_writer(request, "仅记录员可维护名册")
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    zone_code = str(body.get("zone_code", "")).strip()
    if not zone_code:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "库区编码不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    pool: asyncpg.Pool = request.app["pool"]
    created = await add_zone(pool, zone_code, user["username"])
    if not created:
        raise web.HTTPConflict(
            text=json.dumps({"detail": "该库区编码已在开放名册中"}, ensure_ascii=False),
            content_type="application/json",
        )
    return web.json_response({"zone_code": zone_code, "action": "add"}, status=201)


async def delete_roster(request: web.Request) -> web.Response:
    """记录员从名册移除库区；与履历原子完成，旧单库区不被改写。"""
    user = require_writer(request, "仅记录员可维护名册")
    zone_code = request.match_info["code"].strip()
    pool: asyncpg.Pool = request.app["pool"]
    deleted = await remove_zone(pool, zone_code, user["username"])
    if not deleted:
        raise web.HTTPNotFound(
            text=json.dumps({"detail": "该库区编码不在开放名册中"}, ensure_ascii=False),
            content_type="application/json",
        )
    return web.json_response({"zone_code": zone_code, "action": "remove"})


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
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_get("/api/roster", get_roster)
    app.router.add_post("/api/roster", post_roster)
    app.router.add_delete("/api/roster/{code}", delete_roster)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
