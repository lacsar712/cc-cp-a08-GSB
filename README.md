# 冷链探头超温台

记录员上报探头编号、库区编码与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

库区编码必须落在**开放名册**内。名册外库区无论从网页提交、绕过页面直连接口、还是落库前偷改代号，都会被同一套规则以同一句说法退回（HTTP 422）。

## 开放名册规则（三路径共用）

| 环节 | 拦截位置 |
|------|----------|
| 1. 网页提交 | 页面先按名册拦，非名册库区不发请求 |
| 2. 直连接口 `POST /api/readings` | 接口写口按同一名册再校验 |
| 3. 落库前偷改代号 / 直连写库 | 数据库 `BEFORE INSERT` 触发器按 `zone_roster` 兜底 |

三处共用 `backend/roster_rules.py` 与同一句退回文案（触发器内为相同文案），返回统一结构 `{"detail": …, "zone_code": …}`，少一环即视为未完成。

- 名册仅记录员可维护：增、删；每笔变更与 `roster_audit` 履历在同一事务内原子完成。
- 删名册**不**改写历史读数：旧单库区列原样保留；删后再交该库区仍被退回。
- 值班员可在开放名册落地页查看名册、退回样例、变更履历，但禁止改册。
- 初始名册只含 **库甲**：交库甲收下，交库乙退回。

### 名册接口

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/roster` | 登录均可 | 名册 + 退回样例 + 变更履历 |
| POST | `/api/roster` | 记录员 | `{"zone_code":"库乙"}` 登记，原子写履历 |
| DELETE | `/api/roster/{code}` | 记录员 | 移除并写履历；旧单库区不变 |

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python aiohttp + asyncpg |
| 工人 | `worker.py`（psycopg，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Preact + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3197 |
| 接口 | http://localhost:8197 |
| PostgreSQL | localhost:54397（库名 `coldchain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| logger | log123456 | 记录员，可提交读数 |
| watcher | watch123456 | 值班员，只读列表 |

## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 种子数据

- 开放名册初始仅含：**库甲**
- 读数样例：

| 探头 | 库区 | 温度 | 结论 |
|------|------|------|------|
| 探头A01 | 库甲 | 4.2℃ | 合格 |
| 探头B02 | 库甲 | 12.5℃ | 超温 |

## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。
