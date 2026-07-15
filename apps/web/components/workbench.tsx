"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Run = {
  id: string;
  status: string;
  current_phase: string;
  stop_reason?: string | null;
  error?: string | null;
  report_artifact_id?: string | null;
  meeting?: Meeting | null;
  updated_at: string;
};

type Meeting = {
  id: string;
  status: string;
  agenda: string[];
  evidence_packet: Record<string, number | string[]>;
  position_cards: Array<{
    agent: string;
    recommendation: string;
    confidence: number;
    reason: string;
  }>;
};

type ProjectSummary = {
  id: string;
  title: string;
  question: string;
  status: string;
  latest_run?: Run | null;
};

type Dashboard = {
  project: { id: string; title: string; status: string; stage: string; created_at: string };
  question: { text: string; success_criteria: string[] };
  counts: {
    tasks: number;
    sources: number;
    claims: number;
    contested_claims: number;
    artifacts: number;
  };
  tasks: Array<{ id: string; title: string; objective: string; owner: string; status: string }>;
  sources: Array<{
    id: string;
    title: string;
    source_type: string;
    uri: string;
    authors: string[];
    published_at?: string | null;
    license_status: string;
  }>;
  claims: Array<{
    id: string;
    canonical_text: string;
    status: string;
    confidence: number;
    owner: string;
  }>;
  runs: Run[];
  artifacts: Array<{
    id: string;
    title: string;
    kind: string;
    created_at: string;
    content_hash: string;
  }>;
};

type Tab = "cockpit" | "evidence" | "meeting" | "artifacts" | "settings";

const nav: Array<{ id: Tab; label: string; icon: string }> = [
  { id: "cockpit", label: "项目驾驶舱", icon: "▦" },
  { id: "evidence", label: "证据工作区", icon: "◫" },
  { id: "meeting", label: "结构化组会", icon: "◉" },
  { id: "artifacts", label: "研究产物", icon: "◇" },
  { id: "settings", label: "运行设置", icon: "⚙" },
];

const phaseLabels: Record<string, string> = {
  charter: "研究章程",
  librarian: "文献检索",
  skeptic: "反证审查",
  meeting: "用户组会",
  writer: "综合写作",
  complete: "完成",
};

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(payload.detail ?? "请求失败");
  }
  return response.json() as Promise<T>;
}

export function Workbench() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("cockpit");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>("");
  const [showCreate, setShowCreate] = useState(false);
  const [revision, setRevision] = useState("");

  const loadProjects = useCallback(async () => {
    try {
      const values = await requestJson<ProjectSummary[]>("/api/projects");
      setProjects(values);
      if (!selected && values[0]) setSelected(values[0].id);
      if (!values.length) setShowCreate(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法连接 API");
    }
  }, [selected]);

  const loadDashboard = useCallback(async (projectId: string) => {
    if (!projectId) return;
    try {
      const value = await requestJson<Dashboard>(`/api/projects/${projectId}`);
      setDashboard(value);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "课题加载失败");
    }
  }, []);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    void loadDashboard(selected);
  }, [selected, loadDashboard]);

  const currentRun = dashboard?.runs[0];
  useEffect(() => {
    if (!selected || currentRun?.status !== "running") return;
    const timer = window.setInterval(() => void loadDashboard(selected), 1400);
    return () => window.clearInterval(timer);
  }, [selected, currentRun?.status, loadDashboard]);

  const runMeeting = currentRun?.meeting;
  const dateLabel = useMemo(
    () =>
      new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "long",
        day: "numeric",
        weekday: "short",
      }).format(new Date()),
    [],
  );

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const value = await requestJson<ProjectSummary>("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: form.get("title"),
          question: form.get("question"),
          success_criteria: ["保留可核查引用", "明确支持、反证与未决问题"],
        }),
      });
      setShowCreate(false);
      setSelected(value.id);
      await loadProjects();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "新建课题失败");
    } finally {
      setBusy(false);
    }
  }

  async function startRun() {
    if (!selected) return;
    setBusy(true);
    try {
      await requestJson(`/api/projects/${selected}/runs`, { method: "POST" });
      await loadDashboard(selected);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "启动失败");
    } finally {
      setBusy(false);
    }
  }

  async function decide(kind: "continue" | "revise" | "stop") {
    if (!currentRun) return;
    setBusy(true);
    try {
      await requestJson(`/api/runs/${currentRun.id}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind,
          rationale:
            kind === "continue"
              ? "证据覆盖足以进入谨慎综合。"
              : kind === "revise"
                ? "按补充方向继续检索并重新审查。"
                : "当前证据不足或课题暂不继续。",
          direction: kind === "revise" ? revision || "补充研究设计局限与相反结果" : null,
          dissent: [],
        }),
      });
      await loadDashboard(selected);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提交决策失败");
    } finally {
      setBusy(false);
    }
  }

  async function uploadSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      await requestJson(`/api/projects/${selected}/sources/upload`, {
        method: "POST",
        body: form,
      });
      event.currentTarget.reset();
      await loadDashboard(selected);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文件摄取失败");
    } finally {
      setBusy(false);
    }
  }

  async function addUrl(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      await requestJson(`/api/projects/${selected}/sources/url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: form.get("url") }),
      });
      event.currentTarget.reset();
      await loadDashboard(selected);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "网页摄取失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="canvas">
      <section className="workbench-shell">
        <aside className="sidebar">
          <div className="brand">
            <span className="brand-mark"><i /><i /><i /></span>
            <span>Oplab</span>
          </div>

          <nav className="navigation" aria-label="主导航">
            {nav.map((item) => (
              <button
                key={item.id}
                className={activeTab === item.id ? "nav-item active" : "nav-item"}
                onClick={() => setActiveTab(item.id)}
              >
                <span className="nav-icon">{item.icon}</span>
                {item.label}
              </button>
            ))}
          </nav>

          <div className="sidebar-project">
            <div className="project-orb">研</div>
            <strong>{dashboard?.project.title ?? "尚未创建课题"}</strong>
            <span>{dashboard ? "Phase A · Evidence loop" : "Research OS"}</span>
          </div>
          <button className="ghost-action" onClick={() => setShowCreate(true)}>＋ 新建课题</button>
        </aside>

        <div className="main-area">
          <header className="topbar">
            <div>
              <p className="eyebrow">RESEARCH OPERATING SYSTEM</p>
              <h1>{nav.find((item) => item.id === activeTab)?.label}</h1>
              <p className="date-line">{dateLabel}</p>
            </div>
            <div className="header-actions">
              <span className="live-pill"><i /> {currentRun?.status === "running" ? "Agent 运行中" : "系统就绪"}</span>
              <select value={selected} onChange={(event) => setSelected(event.target.value)} aria-label="选择课题">
                {projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
              </select>
              <button className="profile-chip" aria-label="用户资料"><span>PI</span><b>Researcher</b></button>
            </div>
          </header>

          {error && <div className="error-banner"><span>!</span>{error}<button onClick={() => setError("")}>×</button></div>}

          {!dashboard && !showCreate ? (
            <div className="empty-state"><div className="loader" /><p>正在连接研究工作台…</p></div>
          ) : activeTab === "cockpit" ? (
            <Cockpit dashboard={dashboard} run={currentRun} meeting={runMeeting} busy={busy} onStart={startRun} />
          ) : activeTab === "evidence" ? (
            <EvidenceWorkspace dashboard={dashboard} busy={busy} onUpload={uploadSource} onAddUrl={addUrl} />
          ) : activeTab === "meeting" ? (
            <MeetingRoom meeting={runMeeting} run={currentRun} revision={revision} setRevision={setRevision} busy={busy} decide={decide} />
          ) : activeTab === "artifacts" ? (
            <Artifacts dashboard={dashboard} />
          ) : (
            <SettingsPanel />
          )}
        </div>
      </section>

      {showCreate && (
        <div className="modal-backdrop" role="presentation">
          <form className="create-modal" onSubmit={createProject}>
            <div className="modal-accent">✦</div>
            <p className="eyebrow">NEW RESEARCH PROJECT</p>
            <h2>把问题变成可审查的课题</h2>
            <p>先写清研究问题。PI Agent 会据此建立章程、证据要求和检索任务。</p>
            <label>课题名称<input name="title" minLength={2} required placeholder="例如：开源社区韧性研究" /></label>
            <label>核心研究问题<textarea name="question" minLength={10} required rows={4} placeholder="什么机制、在何种边界条件下、如何被证伪？" /></label>
            <div className="modal-actions">
              {projects.length > 0 && <button type="button" className="button secondary" onClick={() => setShowCreate(false)}>取消</button>}
              <button className="button primary" disabled={busy}>{busy ? "创建中…" : "创建课题"}</button>
            </div>
          </form>
        </div>
      )}
    </main>
  );
}

function Cockpit({ dashboard, run, meeting, busy, onStart }: { dashboard: Dashboard | null; run?: Run; meeting?: Meeting | null; busy: boolean; onStart: () => void }) {
  if (!dashboard) return null;
  return (
    <div className="content-stack">
      <section className="metric-grid">
        <MetricCard tone="lavender" label="证据来源" value={dashboard.counts.sources} detail="已绑定来源与元数据" icon="◫" />
        <MetricCard tone="blue" label="可审查论断" value={dashboard.counts.claims} detail="均由 passage 支撑" icon="⌁" />
        <MetricCard tone="mint" label="争议论断" value={dashboard.counts.contested_claims} detail="保留独立反证" icon="⇄" />
        <div className="checkpoint-card">
          <span>当前检查点</span>
          <strong>{phaseLabels[run?.current_phase ?? "charter"]}</strong>
          <p>{run?.status === "needs_user" ? "等待你的组会决策" : run?.status === "completed" ? "研究备忘录已生成" : "可恢复流程已持久化"}</p>
          <button onClick={onStart} disabled={busy || run?.status === "running" || run?.status === "needs_user"}>{run ? "启动新一轮" : "开始研究"}</button>
        </div>
      </section>

      <section className="dashboard-grid">
        <div className="panel pipeline-panel">
          <PanelHeader title="科研闭环" subtitle="每一步都有明确状态与停止条件" badge={run?.status ?? "not started"} />
          <Pipeline phase={run?.current_phase ?? "charter"} status={run?.status} />
          <div className="question-card">
            <span>核心研究问题</span>
            <p>{dashboard.question.text}</p>
          </div>
        </div>

        <div className="panel review-panel">
          <PanelHeader title="组会预览" subtitle="独立立场先于讨论" badge={meeting?.status ?? "待触发"} />
          {meeting ? (
            <div className="mini-positions">
              {meeting.position_cards.map((position, index) => (
                <div key={position.agent}><span className={`agent-dot tone-${index}`}>{position.agent[0]}</span><p><b>{position.agent}</b><small>{position.recommendation} · {Math.round(position.confidence * 100)}%</small></p></div>
              ))}
            </div>
          ) : <div className="quiet-empty">证据审查完成后自动召集组会。</div>}
          <div className="review-note"><span>i</span><p>组会决议会转为正式事件，不会只留在聊天记录中。</p></div>
        </div>
      </section>

      <section className="panel board-panel">
        <PanelHeader title="Research Board" subtitle="任务、责任方与完成条件" badge={`${dashboard.tasks.length} tasks`} />
        <div className="task-table">
          <div className="task-row task-head"><span>任务</span><span>Agent</span><span>状态</span><span>证据门槛</span></div>
          {dashboard.tasks.length ? dashboard.tasks.map((task) => (
            <div className="task-row" key={task.id}><span><b>{task.title}</b><small>{task.objective}</small></span><span className="owner-chip">{task.owner}</span><span><Status value={task.status} /></span><span className="evidence-bar"><i style={{ width: task.status === "done" ? "100%" : task.status === "in_progress" ? "56%" : "22%" }} /></span></div>
          )) : <div className="quiet-empty table-empty">启动研究后，PI 会生成首批类型化任务。</div>}
        </div>
      </section>
    </div>
  );
}

function MetricCard({ tone, label, value, detail, icon }: { tone: string; label: string; value: number; detail: string; icon: string }) {
  return <div className={`metric-card ${tone}`}><div><span className="metric-icon">{icon}</span><span>{label}</span></div><strong>{value.toString().padStart(2, "0")}</strong><p>{detail}</p></div>;
}

function Pipeline({ phase, status }: { phase: string; status?: string }) {
  const phases = ["charter", "librarian", "skeptic", "meeting", "writer", "complete"];
  const current = phases.indexOf(phase);
  return <div className="pipeline">{phases.map((item, index) => <div key={item} className={index < current || status === "completed" ? "pipeline-step done" : index === current ? "pipeline-step current" : "pipeline-step"}><span>{index < current || status === "completed" ? "✓" : index + 1}</span><small>{phaseLabels[item]}</small></div>)}</div>;
}

function EvidenceWorkspace({ dashboard, busy, onUpload, onAddUrl }: { dashboard: Dashboard | null; busy: boolean; onUpload: (event: FormEvent<HTMLFormElement>) => void; onAddUrl: (event: FormEvent<HTMLFormElement>) => void }) {
  if (!dashboard) return null;
  return <div className="content-stack"><section className="ingest-grid"><form className="panel ingest-card" onSubmit={onUpload}><span className="ingest-icon lavender">↑</span><div><h3>上传本地文献</h3><p>支持 PDF、Markdown 与 UTF-8 文本；摄取时即绑定 passage。</p></div><input type="file" name="file" accept=".pdf,.md,.markdown,.txt" required /><button className="button primary" disabled={busy}>摄取文件</button></form><form className="panel ingest-card" onSubmit={onAddUrl}><span className="ingest-icon mint">↗</span><div><h3>添加网页来源</h3><p>保存规范 URL、内容哈希和段落定位，不复制站点脚本。</p></div><input type="url" name="url" required placeholder="https://…" /><button className="button dark" disabled={busy}>读取网页</button></form></section><section className="evidence-columns"><div className="panel"><PanelHeader title="来源台账" subtitle="Source records" badge={`${dashboard.sources.length} sources`} /><div className="source-list">{dashboard.sources.map((source, index) => <a key={source.id} href={source.uri} target="_blank" rel="noreferrer" className="source-item"><span className={`source-index tone-${index % 4}`}>{String(index + 1).padStart(2, "0")}</span><div><b>{source.title}</b><small>{source.authors.slice(0, 3).join(", ") || source.source_type} · {source.published_at ?? "n.d."}</small></div><em>{source.license_status.replaceAll("_", " ")}</em></a>)}</div></div><div className="panel"><PanelHeader title="Claim ledger" subtitle="支持与反证不会被合并抹平" badge={`${dashboard.claims.length} claims`} /><div className="claim-list">{dashboard.claims.map((claim) => <article key={claim.id} className="claim-card"><div><Status value={claim.status} /><span>{Math.round(claim.confidence * 100)}% confidence</span></div><p>{claim.canonical_text}</p><small>owner · {claim.owner}</small></article>)}{!dashboard.claims.length && <div className="quiet-empty">尚无论断。启动研究流程后生成。</div>}</div></div></section></div>;
}

function MeetingRoom({ meeting, run, revision, setRevision, busy, decide }: { meeting?: Meeting | null; run?: Run; revision: string; setRevision: (value: string) => void; busy: boolean; decide: (kind: "continue" | "revise" | "stop") => void }) {
  if (!meeting) return <div className="panel meeting-empty"><span>◉</span><h2>组会尚未触发</h2><p>PI 会在证据与反证收集后创建议程。每位 Agent 会先独立提交 PositionCard。</p></div>;
  return <div className="content-stack"><section className="meeting-hero"><div><p className="eyebrow">STRUCTURED REVIEW</p><h2>证据审查组会</h2><p>{run?.status === "needs_user" ? "流程已安全暂停，等待你的决策。" : "本轮组会已有决议。"}</p></div><Status value={meeting.status} /></section><section className="meeting-layout"><div className="panel"><PanelHeader title="独立立场" subtitle="发言前互不可见，减少无依据从众" badge={`${meeting.position_cards.length} agents`} /><div className="position-grid">{meeting.position_cards.map((position, index) => <article key={position.agent} className={`position-card macaron-${index}`}><div><span>{position.agent[0]}</span><p><b>{position.agent}</b><small>{position.recommendation.toUpperCase()}</small></p><strong>{Math.round(position.confidence * 100)}%</strong></div><p>{position.reason}</p></article>)}</div></div><aside className="panel decision-panel"><PanelHeader title="PI 决策" subtitle="写入正式 DecisionRecord" /><label>若需修订，补充检索方向<textarea value={revision} onChange={(event) => setRevision(event.target.value)} rows={3} placeholder="例如：加入纵向研究与零结果" /></label><div className="decision-actions"><button className="button primary" disabled={busy || run?.status !== "needs_user"} onClick={() => decide("continue")}>继续综合</button><button className="button secondary" disabled={busy || run?.status !== "needs_user"} onClick={() => decide("revise")}>修订检索</button><button className="text-danger" disabled={busy || run?.status !== "needs_user"} onClick={() => decide("stop")}>停止本轮</button></div><div className="evidence-packet"><b>Evidence packet</b><span>来源 {String(meeting.evidence_packet.source_count ?? 0)}</span><span>论断 {String(meeting.evidence_packet.claim_count ?? 0)}</span><span>争议 {String(meeting.evidence_packet.contested_count ?? 0)}</span></div></aside></section></div>;
}

function Artifacts({ dashboard }: { dashboard: Dashboard | null }) {
  if (!dashboard) return null;
  return <div className="panel artifact-panel"><PanelHeader title="研究产物" subtitle="每个产物绑定 run、trace、来源与内容哈希" badge={`${dashboard.artifacts.length} files`} /><div className="artifact-grid">{dashboard.artifacts.map((artifact) => <a key={artifact.id} href={`${API}/api/artifacts/${artifact.id}`} target="_blank" rel="noreferrer" className="artifact-card"><span>MD</span><div><b>{artifact.title}</b><small>{new Date(artifact.created_at).toLocaleString("zh-CN")}</small><code>{artifact.content_hash.slice(0, 16)}</code></div><em>↗</em></a>)}{!dashboard.artifacts.length && <div className="quiet-empty">组会批准综合后，Writer 会在这里发布研究备忘录。</div>}</div></div>;
}

function SettingsPanel() {
  return <div className="settings-grid"><section className="panel setting-card"><span className="ingest-icon blue">AI</span><div><h3>模型路由</h3><p>未配置密钥时使用确定性路径；配置 OpenAI 兼容端点后启用结构化综合。</p></div><Status value="policy controlled" /></section><section className="panel setting-card"><span className="ingest-icon mint">DB</span><div><h3>状态边界</h3><p>领域状态、LangGraph checkpoint 与 artifact store 相互独立。</p></div><Status value="separated" /></section><section className="panel setting-card"><span className="ingest-icon lavender">ID</span><div><h3>幂等副作用</h3><p>重复执行节点不会创建重复来源、组会或产物。</p></div><Status value="enforced" /></section></div>;
}

function PanelHeader({ title, subtitle, badge }: { title: string; subtitle: string; badge?: string }) {
  return <div className="panel-header"><div><h2>{title}</h2><p>{subtitle}</p></div>{badge && <span>{badge.replaceAll("_", " ")}</span>}</div>;
}

function Status({ value }: { value: string }) {
  const normalized = value.toLowerCase().replaceAll("_", "-");
  return <span className={`status status-${normalized}`}>{value.replaceAll("_", " ")}</span>;
}
