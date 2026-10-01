import { useCallback, useEffect, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

// 与后端 roster_rules.REJECT_DETAIL 保持同一说法：网页先拦时也用它。
const REJECT_DETAIL = "库区编码不在开放名册内，已退回；须由记录员在开放名册中登记后再提交";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function actionLabel(action) {
  if (action === "add") return "登记";
  if (action === "remove") return "移除";
  return action;
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
  const [view, setView] = useState("roster"); // 顶栏先开开放名册落地页
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [submitForm, setSubmitForm] = useState({ probe_id: "", zone_code: "", temp_c: "" });
  const [rows, setRows] = useState([]);
  const [zones, setZones] = useState([]);
  const [audit, setAudit] = useState([]);
  const [rejectSample, setRejectSample] = useState(null);
  const [newZone, setNewZone] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const isWriter = user?.role === "writer";

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadReadings = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (!res.ok) {
      setError("加载列表失败，请重新登录");
      return;
    }
    setRows(await res.json());
  }, [token, authHeaders]);

  const loadRoster = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/roster", { headers: authHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    setZones(data.zones || []);
    setAudit(data.audit || []);
    setRejectSample(data.reject_sample || null);
  }, [token, authHeaders]);

  useEffect(() => {
    if (!token) return undefined;
    loadReadings();
    loadRoster();
    const t = setInterval(() => {
      loadReadings();
      loadRoster();
    }, 3000);
    return () => clearInterval(t);
  }, [loadReadings, loadRoster, token, view]);

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
      setView("roster");
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
    setZones([]);
    setAudit([]);
  }

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");

    // 第一道：网页先拦。非名册库区不发请求，说法与直连/写口完全一致。
    const zone = submitForm.zone_code.trim();
    if (!zone || !zones.includes(zone)) {
      setError(REJECT_DETAIL);
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          zone_code: zone,
          temp_c: parseFloat(submitForm.temp_c),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // 第二/三道（直连接口、落库前改写）的统一退回也回到这里，说法一致。
        setError(data.detail || "提交失败");
        return;
      }
      setMsg(data.message || "已提交");
      setSubmitForm({ probe_id: "", zone_code: "", temp_c: "" });
      await loadReadings();
    } finally {
      setLoading(false);
    }
  }

  async function onAddZone(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    const code = newZone.trim();
    if (!code) return;
    const res = await fetch("/api/roster", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ zone_code: code }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.detail || "登记失败");
      return;
    }
    setMsg(`库区「${code}」已登记入开放名册`);
    setNewZone("");
    await loadRoster();
  }

  async function onRemoveZone(code) {
    setError("");
    setMsg("");
    const res = await fetch(`/api/roster/${encodeURIComponent(code)}`, {
      method: "DELETE",
      headers: authHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.detail || "移除失败");
      return;
    }
    setMsg(`库区「${code}」已从开放名册移除；旧单库区保持不变`);
    await loadRoster();
  }

  if (!token) {
    return (
      <div class="wrap">
        <h1>冷链探头超温台</h1>
        <p class="sub">记录员提交探头编号与摄氏温度，后台工人认领后判定合格或超温；库区编码须落在开放名册内。</p>
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

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <nav class="nav">
            <button
              type="button"
              class={view === "roster" ? "tab active" : "tab"}
              onClick={() => setView("roster")}
            >
              开放名册
            </button>
            <button
              type="button"
              class={view === "desk" ? "tab active" : "tab"}
              onClick={() => setView("desk")}
            >
              提交台
            </button>
          </nav>
        </div>
        <div class="user">
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {error && <p class="err">{error}</p>}
      {msg && <p class="ok">{msg}</p>}

      {view === "roster" && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>开放名册</h2>
          <p class="sub" style={{ marginBottom: "0.75rem" }}>
            库区编码必须落在开放名册内。{isWriter ? "记录员可在此登记或移除库区。" : "值班员仅可查看名册、退回样例与变更履历，禁止改册。"}
          </p>

          <div class="zone-list">
            {zones.map((z) => (
              <span class="zone-chip" key={z}>
                {z}
                {isWriter && (
                  <button
                    type="button"
                    class="chip-x"
                    title="从名册移除（旧单库区不变）"
                    onClick={() => onRemoveZone(z)}
                  >
                    ×
                  </button>
                )}
              </span>
            ))}
            {zones.length === 0 && <span class="sub">名册为空，所有库区提交都将被退回。</span>}
          </div>

          {isWriter && (
            <form onSubmit={onAddZone} style={{ marginTop: "0.9rem" }}>
              <div class="row">
                <label>
                  登记库区编码
                  <input
                    value={newZone}
                    onInput={(e) => setNewZone(e.target.value)}
                    placeholder="例如 库乙"
                  />
                </label>
                <button type="submit">登记入册</button>
              </div>
            </form>
          )}
        </div>
      )}

      {view === "roster" && rejectSample && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>退回样例</h2>
          <p class="sub" style={{ marginBottom: "0.5rem" }}>
            名册外库区经三条路径提交都会退回，且说法完全一致（HTTP {rejectSample.status}）：
          </p>
          <ul class="paths">
            {rejectSample.paths.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
          <div class="sample-grid">
            <div>
              <div class="sample-title">请求（名册外库区）</div>
              <pre>{JSON.stringify(rejectSample.request, null, 2)}</pre>
            </div>
            <div>
              <div class="sample-title">统一退回响应</div>
              <pre>{JSON.stringify(rejectSample.response, null, 2)}</pre>
            </div>
          </div>
        </div>
      )}

      {view === "roster" && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>变更履历</h2>
          <table>
            <thead>
              <tr>
                <th>编号</th>
                <th>动作</th>
                <th>库区编码</th>
                <th>操作人</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((a) => (
                <tr key={a.id}>
                  <td>{a.id}</td>
                  <td>
                    <span class={a.action === "add" ? "tag pass" : "tag fail"}>
                      {actionLabel(a.action)}
                    </span>
                  </td>
                  <td>{a.zone_code}</td>
                  <td>{a.operator}</td>
                  <td>{a.created_at ? new Date(a.created_at).toLocaleString("zh-CN") : "—"}</td>
                </tr>
              ))}
              {audit.length === 0 && (
                <tr><td colspan="5">暂无变更</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {view === "desk" && isWriter && (
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
                  list="zone-options"
                  value={submitForm.zone_code}
                  onInput={(e) =>
                    setSubmitForm({ ...submitForm, zone_code: e.target.value })
                  }
                  placeholder="须在开放名册内"
                />
                <datalist id="zone-options">
                  {zones.map((z) => <option value={z} key={z} />)}
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
          </form>
        </div>
      )}

      {view === "desk" && (
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
                  <td>{r.zone_code || "—"}</td>
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
      )}
    </div>
  );
}
