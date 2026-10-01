# 冷链探头超温台

记录员上报探头编号、**库区编码**与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

**库区编码必须落在开放名册里。** 顶栏第一项即「开放名册」落地页，分三个区块：**名册编辑**、**退回样例**、**变更履历**。

## 开放名册规则

- 记录员（logger）维护名册：新增 / 删除库区；名册增删与变更履历在**同一事务**内原子完成。
- 值班员（watcher）可查看名册、退回样例与变更履历，**禁止改册**（接口返回 403）。
- 名册外库区经三条路径提交都以**同一套规则、同一套话术**退回：
  1. **网页提交**：页面表单提交后被退回并展示统一话术；
  2. **直连请求**：直接调用 `POST /api/readings` 被 400 退回；
  3. **落库前偷改代号**：绕过应用直接写库（INSERT 或 UPDATE 改写 `zone_code`）被数据库触发器 `enforce_zone_roster` 拦下。
- 退回话术统一为 `库区「X」不在开放名册内，已退回`（定义于 `backend/rules.py`，触发器 SQL 也由它生成）。
- 网页与直连路径的退回会记入**退回样例**；校验规则、写口、退回样例三路径共用 `rules.py` 一套规则。
- 删除名册只动名册与履历，**不改写历史读数**上的库区编码：删掉库甲后再交库甲会被退回，但旧单上的库区仍是库甲。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python aiohttp + asyncpg |
| 工人 | `worker.py`（psycopg，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Preact + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16（名册校验触发器兜底） |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3197 |
| 接口 | http://localhost:8197 |
| PostgreSQL | localhost:54397（库名 `coldchain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| logger | log123456 | 记录员，可提交读数、维护名册 |
| watcher | watch123456 | 值班员，只读列表 / 名册 / 样例 / 履历 |

## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录 |
| GET | `/api/readings` | 读数列表（含库区） |
| POST | `/api/readings` | 提交读数（记录员；库区须在名册内，否则 400 退回并记样例） |
| GET | `/api/roster` | 查看开放名册 |
| POST | `/api/roster` | 新增库区（记录员；与履历原子写入） |
| DELETE | `/api/roster/{zone_code}` | 删除库区（记录员；与履历原子写入，不改写旧单） |
| GET | `/api/roster/events` | 变更履历 |
| GET | `/api/roster/rejections` | 退回样例 |

网页提交读数时带 `X-Client: web` 头，退回样例据此区分「网页提交 / 直连请求」来源。

## 种子数据

名册初始只留 **库甲**（履历同步记一条 system 新增）。

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
