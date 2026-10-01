import { useCallback, useEffect, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

const PATH_LABELS = { web: "网页提交", api: "直连请求", db: "落库改写" };
const ACTION_LABELS = { add: "新增", remove: "删除" };

function fmtTime(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 19);
}

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  });
  const [view, setView] = useState("roster");
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [submitForm, setSubmitForm] = useState({ probe_id: "", zone_code: "", temp_c: "" });
  const [zoneInput, setZoneInput] = useState("");
  const [rows, setRows] = useState([]);
  const [roster, setRoster] = useState([]);
  const [events, setEvents] = useState([]);
  const [rejections, setRejections] = useState([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [rosterError, setRosterError] = useState("");
  const [rosterMsg, setRosterMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadAll = useCallback(async () => {
    if (!token) return;
    const headers = authHeaders();
    const [readingsRes, rosterRes, eventsRes, rejectionsRes] = await Promise.all([
      fetch("/api/readings", { headers }),
      fetch("/api/roster", { headers }),
      fetch("/api/roster/events", { headers }),
      fetch("/api/roster/rejections", { headers }),
    ]);
    if (!readingsRes.ok || !rosterRes.ok) {
      setError("加载数据失败，请重新登录");
      return;
    }
    setRows(await readingsRes.json());
    setRoster(await rosterRes.json());
    if (eventsRes.ok) setEvents(await eventsRes.json());
    if (rejectionsRes.ok) setRejections(await rejectionsRes.json());
  }, [token, authHeaders]);

  useEffect(() => {
    loadAll();
    if (!token) return undefined;
    const t = setInterval(loadAll, 3000);
    return () => clearInterval(t);
  }, [loadAll, token]);

  async function onLogin(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginForm),
      });
      if (!res.ok) {
        setError("用户名或密码错误");
        return;
      }
      const data = await res.json();
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(
        USER_KEY,
        JSON.stringify({ username: data.username, role: data.role })
      );
      setToken(data.access_token);
      setUser({ username: data.username, role: data.role });
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    setRows([]);
    setRoster([]);
    setEvents([]);
    setRejections([]);
  }

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: { ...authHeaders(), "X-Client": "web" },
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          zone_code: submitForm.zone_code,
          temp_c: parseFloat(submitForm.temp_c),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "提交失败");
        await loadAll();
        return;
      }
      setMsg(data.message || "已提交");
      setSubmitForm({ probe_id: "", zone_code: "", temp_c: "" });
      await loadAll();
    } finally {
      setLoading(false);
    }
  }

  async function onAddZone(e) {
    e.preventDefault();
    setRosterError("");
    setRosterMsg("");
    const zone_code = zoneInput.trim();
    if (!zone_code) return;
    const res = await fetch("/api/roster", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ zone_code }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setRosterError(data.detail || "添加失败");
      return;
    }
    setRosterMsg(data.message || "已加入开放名册");
    setZoneInput("");
    await loadAll();
  }

  async function onRemoveZone(zoneCode) {
    setRosterError("");
    setRosterMsg("");
    const res = await fetch(`/api/roster/${encodeURIComponent(zoneCode)}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setRosterError(data.detail || "删除失败");
      return;
    }
    setRosterMsg(data.message || "已移出开放名册");
    await loadAll();
  }

  if (!token) {
    return (
      <div class="wrap">
        <h1>冷链探头超温台</h1>
        <p class="sub">记录员提交探头编号与摄氏温度，后台工人认领后判定合格或超温。</p>
        <div class="card">
          <form onSubmit={onLogin}>
            <div class="row">
              <label>
                用户名
                <input
                  value={loginForm.username}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, username: e.target.value })
                  }
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginForm.password}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, password: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                登录
              </button>
            </div>
            {error && <p class="err">{error}</p>}
          </form>
          <p class="sub" style={{ marginBottom: 0 }}>
            记录员 logger / log123456 · 值班员 watcher / watch123456
          </p>
        </div>
      </div>
    );
  }

  const isWriter = user?.role === "writer";

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <p class="sub">温度不超过 8℃ 为合格，否则为超温；库区编码必须落在开放名册内。</p>
        </div>
        <div class="user">
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      <nav class="nav">
        <button
          type="button"
          class={view === "roster" ? "active" : ""}
          onClick={() => setView("roster")}
        >
          开放名册
        </button>
        <button
          type="button"
          class={view === "readings" ? "active" : ""}
          onClick={() => setView("readings")}
        >
          探头读数
        </button>
      </nav>

      {view === "roster" && (
        <div>
          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>名册编辑</h2>
            <p class="sub" style={{ marginBottom: "0.75rem" }}>
              只有落在开放名册里的库区编码才能入库；网页提交、直连请求、落库改写三条路径共用这一套名册。
            </p>
            {isWriter ? (
              <form onSubmit={onAddZone}>
                <div class="row">
                  <label>
                    库区编码
                    <input
                      value={zoneInput}
                      onInput={(e) => setZoneInput(e.target.value)}
                      placeholder="例如 库乙"
                    />
                  </label>
                  <button type="submit">添加</button>
                </div>
              </form>
            ) : (
              <p class="sub" style={{ marginBottom: "0.75rem" }}>
                值班员仅可查看名册、退回样例与变更履历，禁止改册。
              </p>
            )}
            {rosterError && <p class="err">{rosterError}</p>}
            {rosterMsg && <p class="ok">{rosterMsg}</p>}
            <table>
              <thead>
                <tr>
                  <th>库区编码</th>
                  <th>维护人</th>
                  <th>加入时间</th>
                  {isWriter && <th>操作</th>}
                </tr>
              </thead>
              <tbody>
                {roster.map((z) => (
                  <tr key={z.zone_code}>
                    <td>{z.zone_code}</td>
                    <td>{z.created_by}</td>
                    <td>{fmtTime(z.created_at)}</td>
                    {isWriter && (
                      <td>
                        <button
                          type="button"
                          class="danger"
                          onClick={() => onRemoveZone(z.zone_code)}
                        >
                          删除
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
                {roster.length === 0 && (
                  <tr>
                    <td colspan={isWriter ? 4 : 3}>名册为空，任何库区都无法入库</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>退回样例</h2>
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>路径</th>
                  <th>库区</th>
                  <th>探头</th>
                  <th>温度℃</th>
                  <th>退回说明</th>
                  <th>提交人</th>
                </tr>
              </thead>
              <tbody>
                {rejections.map((r) => (
                  <tr key={r.id}>
                    <td>{fmtTime(r.created_at)}</td>
                    <td>{PATH_LABELS[r.path] || r.path}</td>
                    <td>{r.zone_code}</td>
                    <td>{r.probe_id || "—"}</td>
                    <td>{r.temp_c ?? "—"}</td>
                    <td>{r.message}</td>
                    <td>{r.attempted_by}</td>
                  </tr>
                ))}
                {rejections.length === 0 && (
                  <tr>
                    <td colspan="7">暂无退回记录</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>变更履历</h2>
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>动作</th>
                  <th>库区</th>
                  <th>操作人</th>
                </tr>
              </thead>
              <tbody>
                {events.map((ev) => (
                  <tr key={ev.id}>
                    <td>{fmtTime(ev.created_at)}</td>
                    <td>{ACTION_LABELS[ev.action] || ev.action}</td>
                    <td>{ev.zone_code}</td>
                    <td>{ev.actor}</td>
                  </tr>
                ))}
                {events.length === 0 && (
                  <tr>
                    <td colspan="4">暂无变更</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {view === "readings" && (
        <div>
          {isWriter && (
            <div class="card">
              <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交读数</h2>
              <form onSubmit={onSubmit}>
                <div class="row">
                  <label>
                    探头编号
                    <input
                      required
                      value={submitForm.probe_id}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, probe_id: e.target.value })
                      }
                      placeholder="例如 探头C03"
                    />
                  </label>
                  <label>
                    库区编码
                    <input
                      required
                      list="roster-zones"
                      value={submitForm.zone_code}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, zone_code: e.target.value })
                      }
                      placeholder="须在开放名册内"
                    />
                    <datalist id="roster-zones">
                      {roster.map((z) => (
                        <option key={z.zone_code} value={z.zone_code} />
                      ))}
                    </datalist>
                  </label>
                  <label>
                    温度（℃）
                    <input
                      required
                      type="number"
                      step="0.1"
                      value={submitForm.temp_c}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, temp_c: e.target.value })
                      }
                    />
                  </label>
                  <button type="submit" disabled={loading}>
                    提交
                  </button>
                </div>
                {error && <p class="err">{error}</p>}
                {msg && <p class="ok">{msg}</p>}
              </form>
            </div>
          )}

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>读数列表</h2>
            <table>
              <thead>
                <tr>
                  <th>编号</th>
                  <th>探头</th>
                  <th>库区</th>
                  <th>温度℃</th>
                  <th>结论</th>
                  <th>说明</th>
                  <th>状态</th>
                  <th>提交人</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>{r.id}</td>
                    <td>{r.probe_id}</td>
                    <td>{r.zone_code}</td>
                    <td>{r.temp_c}</td>
                    <td>
                      <span class={verdictClass(r.verdict, r.status)}>
                        {displayVerdict(r)}
                      </span>
                    </td>
                    <td>{r.reason || "—"}</td>
                    <td>{r.status}</td>
                    <td>{r.created_by}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colspan="8">暂无数据</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
