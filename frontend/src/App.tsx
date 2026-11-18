import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  BadgeCheck,
  Boxes,
  ChevronDown,
  CircleAlert,
  ClipboardList,
  FileSpreadsheet,
  Gauge,
  HandCoins,
  LoaderCircle,
  Menu,
  MessageSquareText,
  PackageCheck,
  Search,
  ShieldCheck,
  Truck,
  X,
} from "lucide-react";
import { apiBaseUrl, apiDownload, apiRequest } from "./lib/api";
import PolicyManagement from "./policy/PolicyManagement";

type PageKey = "dashboard" | "chat" | "batch" | "buyer" | "policy" | "suppliers" | "contracts";
type DashboardData = {
  purchase_requests: number;
  po_decisions: number;
  sourcing_requests: number;
  pending_buyer_actions: number;
  active_policy_version: string | null;
  recent_decisions: BuyerDecision[];
};
type BatchRow = Record<string, string | number>;
type BatchResponse = {
  batch_id: string;
  statistics: {
    total_rows: number;
    processed: number;
    not_processed: number;
    duplicates: number;
    errors: number;
    routes: Record<string, number>;
    policy_versions: string[];
    error_details: { row: number; pr_no?: string; pr_line?: string | number; reason: string; type?: string }[];
  };
  rows: BatchRow[];
};
type PRLine = {
  pr_no: string;
  pr_line: number;
  description: string;
  quantity: string;
  uom: string;
  plant: string;
  created_at: string;
  need_by_date: string;
  urgency: string;
};
type BuyerDecision = {
  id: number;
  group_id: string;
  item_code: string | null;
  plant_code: string | null;
  requirement_date: string;
  quantity: string;
  need_by_date: string;
  group_value_inr: string;
  decision: string;
  route: string;
  supplier_id: string | null;
  supplier_name: string | null;
  unit_price: string | null;
  suggested_supplier_ids: string[];
  clause: string;
  policy_version: string | null;
  reason: string;
  purchase_order: string | null;
  status: string;
  overridden: boolean;
  lines: PRLine[];
};
type Supplier = {
  supplier_id: string;
  name: string;
  city: string;
  status: string;
  categories: string[];
  on_time_pct: string;
  quality_rating: string;
  purchase_count: number;
  recent_purchase_history: {
    po_number: string;
    po_date: string;
    item_code: string;
    quantity: string;
    unit_price: string;
    delivered_on_time: boolean;
  }[];
  contracts: { contract_id: string; end_date: string; verified: boolean }[];
  preferred_ranking: { item_code: string; rank: number; reference_price: string }[];
};
type Contract = {
  id: number;
  contract_id: string;
  supplier_id: string;
  supplier_name: string | null;
  start_date: string;
  end_date: string;
  value_cap: string;
  baseline_consumed_value: string;
  allocated_value: string;
  remaining_value: string;
  source_type: string;
  source_reference: string | null;
  verified: boolean;
  review_status: string;
  lines: { item_code: string; description: string; uom: string; rate: string }[];
};
type ChatResult = {
  pr_no: string;
  resolved_item_code: string;
  decision: string;
  route: string;
  supplier_id: string | null;
  suggested_supplier_ids: string[];
  unit_price: string | null;
  group_value_inr: string | null;
  policy_version: string;
  clause: string;
  reason: string;
  match_candidates: { item_code: string; score: string }[];
};

const navigation: { id: PageKey; label: string; icon: typeof Gauge; section?: string }[] = [
  { id: "dashboard", label: "Overview", icon: Gauge, section: "Workspace" },
  { id: "chat", label: "Employee chat", icon: MessageSquareText },
  { id: "batch", label: "Batch processing", icon: FileSpreadsheet },
  { id: "buyer", label: "Buyer workspace", icon: ClipboardList },
  { id: "suppliers", label: "Suppliers", icon: Truck, section: "Procurement" },
  { id: "contracts", label: "Contracts", icon: HandCoins },
  { id: "policy", label: "Policy management", icon: ShieldCheck, section: "Governance" },
];

const pageCopy: Record<PageKey, { title: string; subtitle: string }> = {
  dashboard: { title: "Procurement overview", subtitle: "Operational activity and decisions from your procurement workspace." },
  chat: { title: "Employee purchase request", subtitle: "Describe the need, complete the request details, and review the policy decision." },
  batch: { title: "PR batch processing", subtitle: "Validate and process a procurement request CSV under effective policy versions." },
  buyer: { title: "Buyer workspace", subtitle: "Review pending requirements, source documents, recommendations and next actions." },
  policy: { title: "Policy management", subtitle: "Review effective policy versions and manage amendments." },
  suppliers: { title: "Supplier directory", subtitle: "Explore supplier eligibility, purchase activity and preferred rankings." },
  contracts: { title: "Contract register", subtitle: "Review contract coverage, ceiling consumption and PDF verification status." },
};

function formatCurrency(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function Panel({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function StateMessage({
  loading,
  error,
  empty,
  onRetry,
}: {
  loading?: boolean;
  error?: string;
  empty?: string;
  onRetry?: () => void;
}) {
  if (loading) {
    return (
      <div className="state-message">
        <LoaderCircle className="animate-spin text-teal-700" size={20} />
        <span>Loading live procurement data…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="state-message state-error" role="alert">
        <CircleAlert size={20} />
        <span>{error}</span>
        {onRetry && <button className="button-small" onClick={onRetry}>Retry</button>}
      </div>
    );
  }
  if (empty) {
    return (
      <div className="state-message">
        <ClipboardList className="text-slate-400" size={20} />
        <span>{empty}</span>
      </div>
    );
  }
  return null;
}

function StatusPill({ children }: { children: string }) {
  const normal = children.toLowerCase().replaceAll("_", " ");
  const style = ["active", "approved", "po"].includes(normal)
    ? "pill-green"
    : ["pending", "pending approval"].includes(normal)
      ? "pill-amber"
      : ["blocked", "inactive", "rejected", "not processed"].includes(normal)
        ? "pill-red"
        : "pill-blue";
  return <span className={`status-pill ${style}`}>{normal}</span>;
}

function App() {
  const [page, setPage] = useState<PageKey>("dashboard");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [dashboardLoading, setDashboardLoading] = useState(true);
  const [dashboardError, setDashboardError] = useState("");
  const [apiStatus, setApiStatus] = useState<"checking" | "connected" | "offline">("checking");

  const loadDashboard = useCallback(async () => {
    setDashboardLoading(true);
    setDashboardError("");
    try {
      setDashboard(await apiRequest<DashboardData>("/dashboard"));
    } catch (error) {
      setDashboardError(error instanceof Error ? error.message : "Unable to load dashboard.");
    } finally {
      setDashboardLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);
  useEffect(() => {
    void apiRequest<{ status: string }>("/health")
      .then(() => setApiStatus("connected"))
      .catch(() => setApiStatus("offline"));
  }, []);

  const current = pageCopy[page];
  return (
    <div className="app-shell">
      {mobileNavOpen && (
        <button
          aria-label="Close navigation"
          className="mobile-backdrop"
          onClick={() => setMobileNavOpen(false)}
        />
      )}
      <aside className={`sidebar ${mobileNavOpen ? "sidebar-open" : ""}`}>
        <div className="brand">
          <span className="brand-mark"><Boxes size={22} /></span>
          <span>
            <strong>Cantill</strong>
            <small>PROCUREMENT CLOUD</small>
          </span>
          <button className="icon-button mobile-close" onClick={() => setMobileNavOpen(false)} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>
        <div className="workspace-switch">
          <span className="workspace-avatar">C</span>
          <span className="workspace-label"><strong>Corvo Engineering</strong><small>Procurement workspace</small></span>
          <ChevronDown size={15} className="text-muted" />
        </div>
        <nav className="side-nav" aria-label="Main navigation">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.id}>
                {item.section && <p className="nav-section">{item.section}</p>}
                <button
                  className={`nav-item ${page === item.id ? "nav-item-active" : ""}`}
                  onClick={() => {
                    setPage(item.id);
                    setMobileNavOpen(false);
                  }}
                  aria-current={page === item.id ? "page" : undefined}
                >
                  <Icon size={18} strokeWidth={1.8} />
                  <span>{item.label}</span>
                  {item.id === "buyer" && (dashboard?.pending_buyer_actions ?? 0) > 0 && (
                    <span className="nav-count">{dashboard?.pending_buyer_actions}</span>
                  )}
                </button>
              </div>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className={`connection-indicator connection-${apiStatus}`}><span /> API {apiStatus}</div>
          <p>Procurement operations<br />Policy-driven decisions</p>
        </div>
      </aside>

      <div className="main-frame">
        <header className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setMobileNavOpen(true)} aria-label="Open menu">
            <Menu size={21} />
          </button>
          <div className="breadcrumb"><span>Procurement</span><span className="breadcrumb-divider">/</span><strong>{current.title}</strong></div>
          <div className="topbar-right">
            <div className="environment-badge"><Activity size={14} /> Operations</div>
            <span className="user-avatar">PR</span>
          </div>
        </header>
        <main className="content-area">
          <div className="page-title">
            <div>
              <h1>{current.title}</h1>
              <p>{current.subtitle}</p>
            </div>
            <div className="policy-chip">
              <span className="online-dot" />
              Policy {dashboard?.active_policy_version ?? "—"}
            </div>
          </div>
          {page === "dashboard" && (
            <DashboardPage
              data={dashboard}
              loading={dashboardLoading}
              error={dashboardError}
              retry={() => void loadDashboard()}
              navigate={setPage}
            />
          )}
          {page === "chat" && <EmployeeChatPage />}
          {page === "batch" && <BatchPage />}
          {page === "buyer" && <BuyerPage onUpdated={() => void loadDashboard()} />}
          {page === "policy" && <PolicyManagement embedded />}
          {page === "suppliers" && <SuppliersPage />}
          {page === "contracts" && <ContractsPage />}
        </main>
      </div>
    </div>
  );
}

function DashboardPage({
  data,
  loading,
  error,
  retry,
  navigate,
}: {
  data: DashboardData | null;
  loading: boolean;
  error: string;
  retry: () => void;
  navigate: (page: PageKey) => void;
}) {
  const cards = [
    { label: "Purchase requests", value: data?.purchase_requests, icon: ClipboardList, tone: "blue", foot: "Recorded PR lines" },
    { label: "PO decisions", value: data?.po_decisions, icon: PackageCheck, tone: "green", foot: "Decision outcomes" },
    { label: "Sourcing requests", value: data?.sourcing_requests, icon: HandCoins, tone: "violet", foot: "RFQ, RFP and sourcing" },
    { label: "Buyer actions", value: data?.pending_buyer_actions, icon: Activity, tone: "amber", foot: "Awaiting review" },
  ];
  return (
    <div className="page-stack">
      {error && <StateMessage error={error} onRetry={retry} />}
      <div className="stats-grid">
        {cards.map(({ label, value, icon: Icon, tone, foot }) => (
          <div className="stat-card" key={label}>
            <div className={`stat-icon stat-${tone}`}><Icon size={19} /></div>
            <p>{label}</p>
            <div className="stat-value">{loading ? <span className="skeleton skeleton-number" /> : (value ?? 0).toLocaleString("en-IN")}</div>
            <small>{foot}</small>
          </div>
        ))}
      </div>
      <div className="dashboard-grid">
        <Panel
          title="Recent procurement decisions"
          subtitle="Latest decisions persisted by the policy engine."
          action={<button className="text-action" onClick={() => navigate("buyer")}>Open buyer workspace <ArrowRight size={15} /></button>}
        >
          {loading ? <StateMessage loading /> : !data?.recent_decisions.length ? (
            <StateMessage empty="No procurement decisions have been recorded yet." />
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead><tr><th>Requirement</th><th>Route</th><th>Value</th><th>Supplier</th><th>Status</th></tr></thead>
                <tbody>
                  {data.recent_decisions.map((row) => (
                    <tr key={row.id}>
                      <td><strong>{row.item_code ?? "New item"}</strong><small>{row.group_id}</small></td>
                      <td><span className="route-label">{row.route}</span></td>
                      <td className="numeric">{formatCurrency(row.group_value_inr)}</td>
                      <td>{row.supplier_id ?? "—"}</td>
                      <td><StatusPill>{row.status}</StatusPill></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Quick access" subtitle="Go directly to common procurement work.">
          <div className="quick-links">
            {[
              { label: "Process a PR batch", detail: "Upload and validate request CSV", page: "batch" as PageKey, icon: FileSpreadsheet },
              { label: "Review buyer actions", detail: "Work through pending decisions", page: "buyer" as PageKey, icon: ClipboardList },
              { label: "Manage policy versions", detail: "Review amendments and effective dates", page: "policy" as PageKey, icon: ShieldCheck },
            ].map((link) => {
              const Icon = link.icon;
              return <button className="quick-link" key={link.page} onClick={() => navigate(link.page)}>
                <span className="quick-icon"><Icon size={18} /></span>
                <span><strong>{link.label}</strong><small>{link.detail}</small></span>
                <ArrowRight size={16} className="quick-arrow" />
              </button>;
            })}
          </div>
        </Panel>
      </div>
      <div className="policy-banner">
        <div className="policy-banner-icon"><ShieldCheck size={19} /></div>
        <div><strong>Effective procurement policy</strong><p>Policy {data?.active_policy_version ?? "—"} is selected from the current effective version.</p></div>
        <button className="button-secondary" onClick={() => navigate("policy")}>View policy <ArrowRight size={15} /></button>
      </div>
    </div>
  );
}

function BatchPage() {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<BatchResponse | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const filteredRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (result?.rows ?? []).filter((row) =>
      !needle || [row.pr_no, row.resolved_item_code, row.route, row.decision, row.supplier_id, row.reason]
        .some((value) => String(value ?? "").toLowerCase().includes(needle)),
    );
  }, [query, result]);

  const process = async () => {
    if (!file) {
      setError("Choose a PR batch CSV file.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const body = new FormData();
      body.set("file", file);
      const response = await fetch(`${apiBaseUrl()}/process-batch`, { method: "POST", body });
      if (!response.ok) {
        const errorBody = await response.json() as { error?: { message?: string }; detail?: string };
        throw new Error(errorBody.error?.message ?? errorBody.detail ?? `Processing failed (${response.status})`);
      }
      const parsed = await response.json() as BatchResponse;
      setResult(parsed);
      setNotice(`Processed ${parsed.statistics.total_rows} source rows. ${parsed.statistics.not_processed} require attention.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to process this batch.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-stack">
      {error && <StateMessage error={error} />}
      {notice && <div className="notice-success"><BadgeCheck size={17} />{notice}</div>}
      <Panel title="Upload purchase request batch" subtitle="CSV must match the Cantill PR batch column format. Each invalid source row is retained in the results with its reason.">
        <div className="batch-upload">
          <label className="upload-zone">
            <span className="upload-icon"><FileSpreadsheet size={22} /></span>
            <strong>{file?.name ?? "Select a PR batch CSV"}</strong>
            <small>{file ? `${(file.size / 1024).toFixed(1)} KB · ${file.type || "CSV file"}` : "UTF-8 CSV · original rows are preserved"}</small>
            <input type="file" accept=".csv,text/csv" onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setError("");
              setNotice("");
            }} />
          </label>
          <button className="button-primary" disabled={busy || !file} onClick={() => void process()}>
            {busy ? <LoaderCircle size={17} className="animate-spin" /> : <Activity size={17} />}
            {busy ? "Processing batch…" : "Process batch"}
          </button>
        </div>
      </Panel>
      {result && (
        <>
          <div className="batch-summary">
            <BatchStat label="Rows in batch" value={result.statistics.total_rows} />
            <BatchStat label="Processed" value={result.statistics.processed} tone="green" />
            <BatchStat label="Needs review" value={result.statistics.not_processed} tone={result.statistics.not_processed ? "amber" : "green"} />
            <BatchStat label="Duplicate rows" value={result.statistics.duplicates} tone={result.statistics.duplicates ? "red" : "green"} />
          </div>
          <Panel
            title="Batch decisions"
            subtitle={`${filteredRows.length.toLocaleString("en-IN")} of ${result.rows.length.toLocaleString("en-IN")} lines · Policy ${result.statistics.policy_versions.join(", ") || "not assigned"}`}
            action={<button className="button-secondary" onClick={() => void apiDownload(`/batches/${result.batch_id}/export`, `procurement-decisions-${result.batch_id}.csv`).catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "CSV download failed."))}><ArrowDownToLine size={16} /> Download CSV</button>}
          >
            <div className="table-toolbar">
              <label className="search-field"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search PR, item, route, supplier…" /></label>
              <span className="muted-copy">Batch ID {result.batch_id.slice(0, 8)}</span>
            </div>
            <div className="table-scroll results-table-scroll">
              <table className="data-table">
                <thead><tr><th>PR line</th><th>Resolved item</th><th>Requirement group</th><th>Decision / route</th><th>Supplier</th><th>Unit price</th><th>Group value</th><th>Policy</th><th>Reason</th></tr></thead>
                <tbody>
                  {filteredRows.map((row, index) => (
                    <tr key={`${row.pr_no}-${row.pr_line}-${index}`} className={row.decision === "NOT_PROCESSED" ? "row-warning" : ""}>
                      <td><strong>{row.pr_no}/{row.pr_line}</strong></td>
                      <td>{row.resolved_item_code || "—"}</td>
                      <td><span className="mono">{row.group_id || "—"}</span></td>
                      <td><strong>{row.route}</strong><small>{row.decision}</small></td>
                      <td>{row.supplier_id || "—"}</td>
                      <td className="numeric">{formatCurrency(String(row.unit_price ?? ""))}</td>
                      <td className="numeric">{formatCurrency(String(row.group_value_inr ?? ""))}</td>
                      <td>{row.policy_version || "—"}</td>
                      <td className="reason-cell">{row.reason}</td>
                    </tr>
                  ))}
                  {!filteredRows.length && <tr><td colSpan={9} className="empty-cell">No rows match this search.</td></tr>}
                </tbody>
              </table>
            </div>
            {result.statistics.error_details.length > 0 && (
              <details className="errors-disclosure">
                <summary><CircleAlert size={15} /> Validation and processing errors ({result.statistics.error_details.length})</summary>
                <ul>{result.statistics.error_details.map((item, index) => <li key={`${item.row}-${index}`}><strong>Row {item.row}{item.pr_no ? ` · ${item.pr_no}/${item.pr_line}` : ""}:</strong> {item.reason}</li>)}</ul>
              </details>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}

function BatchStat({ label, value, tone = "blue" }: { label: string; value: number; tone?: string }) {
  return <div className={`batch-stat batch-stat-${tone}`}><small>{label}</small><strong>{value.toLocaleString("en-IN")}</strong></div>;
}

function BuyerPage({ onUpdated }: { onUpdated: () => void }) {
  const [items, setItems] = useState<BuyerDecision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<BuyerDecision | null>(null);
  const [actor, setActor] = useState("");
  const [actionReason, setActionReason] = useState("");
  const [overrideRoute, setOverrideRoute] = useState("RFQ");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setItems(await apiRequest<BuyerDecision[]>("/buyer/requirements"));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load buyer requirements.");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const filtered = items.filter((item) => `${item.group_id} ${item.item_code} ${item.route} ${item.plant_code} ${item.supplier_id}`.toLowerCase().includes(query.toLowerCase()));
  const submitAction = async (kind: "approve" | "override") => {
    if (!selected) return;
    if (!actor.trim() || !actionReason.trim()) {
      setError("Enter your user ID and a written reason before submitting.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await apiRequest(`/buyer/decisions/${selected.id}/${kind}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          actor_id: actor,
          reason: actionReason,
          ...(kind === "override" ? { route: overrideRoute } : {}),
        }),
      });
      setSelected(null);
      setActionReason("");
      await load();
      onUpdated();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to submit buyer action.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page-stack">
      {error && <StateMessage error={error} onRetry={() => void load()} />}
      <Panel title="Requirements awaiting review" subtitle="Decision evidence and original PR lines are available before action." action={<span className="count-chip">{items.length} pending</span>}>
        <div className="table-toolbar">
          <label className="search-field"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search item, group, route…" /></label>
        </div>
        {loading ? <StateMessage loading /> : !filtered.length ? <StateMessage empty={items.length ? "No requirements match your search." : "There are no pending buyer actions."} /> : (
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>Requirement</th><th>Original PRs</th><th>Plant / need by</th><th>Value</th><th>Decision</th><th>Supplier</th><th>Policy</th><th /></tr></thead>
              <tbody>{filtered.map((item) => (
                <tr key={item.id}>
                  <td><strong>{item.item_code ?? "New item"}</strong><small>{item.group_id}</small></td>
                  <td>{item.lines.map((line) => `${line.pr_no}/${line.pr_line}`).join(", ")}</td>
                  <td>{item.plant_code ?? "—"}<small>{formatDate(item.need_by_date)}</small></td>
                  <td className="numeric">{formatCurrency(item.group_value_inr)}</td>
                  <td><strong>{item.route}</strong><small>{item.clause} · {item.decision}</small></td>
                  <td>{item.supplier_name ?? (item.suggested_supplier_ids.join(", ") || "—")}{item.unit_price && <small>{formatCurrency(item.unit_price)} / unit</small>}</td>
                  <td>{item.policy_version}</td>
                  <td><button className="button-small" onClick={() => { setSelected(item); setOverrideRoute(item.route); setError(""); }}>Review</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Panel>
      {selected && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null); }}>
          <section className="review-modal" role="dialog" aria-modal="true" aria-labelledby="review-title">
            <div className="modal-heading"><div><p className="eyebrow">BUYER REVIEW · {selected.group_id}</p><h2 id="review-title">{selected.item_code ?? "New item requirement"}</h2></div><button className="icon-button" onClick={() => setSelected(null)} aria-label="Close"><X size={19} /></button></div>
            <div className="detail-grid">
              <Detail label="Decision / route" value={`${selected.decision} · ${selected.route}`} />
              <Detail label="Group value" value={formatCurrency(selected.group_value_inr)} />
              <Detail label="Quantity" value={selected.quantity} />
              <Detail label="Plant / required date" value={`${selected.plant_code ?? "—"} · ${formatDate(selected.need_by_date)}`} />
              <Detail label="Supplier / unit price" value={`${selected.supplier_name ?? selected.supplier_id ?? "Not selected"} · ${formatCurrency(selected.unit_price)}`} />
              <Detail label="Applied policy clause" value={`${selected.policy_version} · ${selected.clause}`} />
            </div>
            <div className="review-section"><h3>Original PR lines</h3>
              <div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>PR</th><th>Description</th><th>Quantity</th><th>Plant</th><th>Urgency</th></tr></thead><tbody>
                {selected.lines.map((line) => <tr key={`${line.pr_no}-${line.pr_line}`}><td>{line.pr_no}/{line.pr_line}</td><td>{line.description}</td><td>{line.quantity} {line.uom}</td><td>{line.plant}</td><td>{line.urgency}</td></tr>)}
              </tbody></table></div>
            </div>
            {selected.suggested_supplier_ids.length > 0 && <div className="recommendation"><strong>Suggested eligible suppliers</strong><p>{selected.suggested_supplier_ids.join(" · ")}</p></div>}
            <div className="reason-box"><strong>Policy explanation</strong><p>{selected.reason}</p></div>
            <div className="form-grid review-form">
              <label className="field-label">Buyer user ID<input className="input" value={actor} onChange={(event) => setActor(event.target.value)} placeholder="Your user ID" /></label>
              <label className="field-label">Written reason<input className="input" value={actionReason} onChange={(event) => setActionReason(event.target.value)} placeholder="Required for audit history" /></label>
            </div>
            <details className="override-details">
              <summary>Override decision route</summary>
              <div className="override-controls">
                <select className="input" value={overrideRoute} onChange={(event) => setOverrideRoute(event.target.value)}>
                  {["CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO", "RFQ", "RFP", "CAPEX_SOURCING", "NEW_ITEM_SOURCING"].map((route) => <option key={route}>{route}</option>)}
                </select>
                <button className="button-secondary" disabled={busy} onClick={() => void submitAction("override")}>Record override</button>
              </div>
            </details>
            <div className="modal-actions"><button className="button-secondary" onClick={() => setSelected(null)}>Cancel</button><button className="button-primary" disabled={busy} onClick={() => void submitAction("approve")}>{busy ? <LoaderCircle size={16} className="animate-spin" /> : <BadgeCheck size={16} />} Approve recommendation</button></div>
          </section>
        </div>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div className="detail-item"><small>{label}</small><strong>{value}</strong></div>;
}

function EmployeeChatPage() {
  const [message, setMessage] = useState("");
  const [requester, setRequester] = useState("");
  const [department, setDepartment] = useState("");
  const [plant, setPlant] = useState("");
  const [item, setItem] = useState("");
  const [quantity, setQuantity] = useState("");
  const [uom, setUom] = useState("");
  const [needByDate, setNeedByDate] = useState("");
  const [urgency, setUrgency] = useState("Normal");
  const [estimate, setEstimate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ChatResult | null>(null);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setResult(null);
    setBusy(true);
    try {
      const parsed = await apiRequest<ChatResult>("/chat/requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          requester,
          department,
          plant,
          item,
          quantity: Number(quantity),
          uom,
          need_by_date: needByDate,
          urgency,
          estimated_unit_price: estimate ? Number(estimate) : null,
          message,
        }),
      });
      setResult(parsed);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to submit purchase request.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="chat-layout">
      <Panel title="New purchase request" subtitle="Tell us what you need, then complete the request details for a policy-based decision.">
        <div className="chat-thread">
          <div className="chat-message assistant-message"><span className="assistant-avatar"><MessageSquareText size={16} /></span><div><strong>Procurement assistant</strong><p>Describe the item and your requirement. I’ll use the item master and procurement policy to resolve and evaluate it. Please include the requested delivery date and urgency below.</p></div></div>
          <form onSubmit={(event) => void submit(event)} className="chat-form">
            {error && <div className="notice-error" role="alert"><CircleAlert size={16} />{error}</div>}
            <label className="field-label">Your request<textarea className="input chat-textarea" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="For example: Please arrange 200 nitrile gloves for the Chakan plant by next Friday." required /></label>
            <div className="form-grid">
              <label className="field-label">Requester<input className="input" value={requester} onChange={(event) => setRequester(event.target.value)} required /></label>
              <label className="field-label">Department<input className="input" value={department} onChange={(event) => setDepartment(event.target.value)} required /></label>
              <label className="field-label">Plant code<input className="input" value={plant} onChange={(event) => setPlant(event.target.value.toUpperCase())} placeholder="e.g. CHK1" required /></label>
              <label className="field-label">Item name or item code<input className="input" value={item} onChange={(event) => setItem(event.target.value)} placeholder="Search the item master" required /></label>
              <label className="field-label">Quantity<input className="input" type="number" step="0.001" min="0.001" value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
              <label className="field-label">Unit of measure<input className="input" value={uom} onChange={(event) => setUom(event.target.value.toUpperCase())} required /></label>
              <label className="field-label">Need by<input className="input" type="date" value={needByDate} onChange={(event) => setNeedByDate(event.target.value)} required /></label>
              <label className="field-label">Urgency<select className="input" value={urgency} onChange={(event) => setUrgency(event.target.value)}><option>Normal</option><option>Emergency</option></select></label>
              <label className="field-label form-grid-wide">Estimated unit price (INR, if known)<input className="input" type="number" min="0" step="0.01" value={estimate} onChange={(event) => setEstimate(event.target.value)} placeholder="Required if this is a new item without a known price" /></label>
            </div>
            <div className="chat-submit-row"><span>Submitting evaluates the request and records the decision.</span><button className="button-primary" disabled={busy}>{busy ? <LoaderCircle size={16} className="animate-spin" /> : <ArrowRight size={16} />} Submit request</button></div>
          </form>
          {result && <div className="chat-result"><div className="result-heading"><BadgeCheck size={19} /><div><strong>Request {result.pr_no} evaluated</strong><small>Resolved item {result.resolved_item_code} · Policy {result.policy_version} · Clause {result.clause}</small></div></div><div className="chat-result-grid"><Detail label="Decision" value={`${result.decision} · ${result.route}`} /><Detail label="Group value" value={formatCurrency(result.group_value_inr)} /><Detail label="Supplier" value={result.supplier_id ?? (result.suggested_supplier_ids.join(", ") || "No supplier selected")} /><Detail label="Unit price" value={formatCurrency(result.unit_price)} /></div><p>{result.reason}</p>{result.match_candidates.length > 1 && <small>Item match candidates: {result.match_candidates.map((candidate) => `${candidate.item_code} (${candidate.score})`).join(" · ")}</small>}</div>}
        </div>
      </Panel>
      <div className="chat-side-note"><ShieldCheck size={20} /><strong>Policy-controlled evaluation</strong><p>Item resolution, valuation, supplier eligibility and decision routes are handled by the procurement backend. The chat interface does not make purchasing decisions.</p></div>
    </div>
  );
}

function SuppliersPage() {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10));
  const [search, setSearch] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ as_of: asOf });
      if (search.trim()) params.set("search", search.trim());
      setSuppliers(await apiRequest<Supplier[]>(`/suppliers?${params}`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load suppliers.");
    } finally {
      setLoading(false);
    }
  }, [asOf, search]);
  useEffect(() => { void load(); }, [load]);
  const download = () => apiDownload(`/preferred-suppliers/export?as_of=${asOf}`, `preferred-suppliers-${asOf}.csv`).catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Export failed."));
  return <div className="page-stack">
    {error && <StateMessage error={error} onRetry={() => void load()} />}
    <Panel title="Supplier directory" subtitle="Supplier status and activity are loaded from master data and purchase history." action={<button className="button-secondary" onClick={() => void download()}><ArrowDownToLine size={16} /> Preferred supplier export</button>}>
      <div className="table-toolbar supplier-toolbar">
        <label className="search-field"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search supplier name or ID…" /></label>
        <label className="date-filter">Rank as of <input className="input" type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} /></label>
      </div>
      {loading ? <StateMessage loading /> : !suppliers.length ? <StateMessage empty="No supplier records match this search." /> : <div className="table-scroll"><table className="data-table"><thead><tr><th>Supplier</th><th>Status</th><th>Location</th><th>Categories</th><th>PO history</th><th>On time</th><th>Quality</th><th>Preferred ranking</th></tr></thead><tbody>
        {suppliers.map((supplier) => <tr key={supplier.supplier_id}><td><strong>{supplier.name}</strong><small>{supplier.supplier_id}</small></td><td><StatusPill>{supplier.status}</StatusPill></td><td>{supplier.city}</td><td className="category-cell">{supplier.categories.join(", ")}</td><td>{supplier.purchase_count}{supplier.recent_purchase_history[0] && <small>{supplier.recent_purchase_history[0].po_number} · {formatDate(supplier.recent_purchase_history[0].po_date)}</small>}</td><td>{supplier.on_time_pct}%</td><td>{supplier.quality_rating} / 5</td><td>{supplier.preferred_ranking.length ? supplier.preferred_ranking.slice(0, 3).map((ranking) => <span className="ranking-chip" key={ranking.item_code}>{ranking.item_code} · #{ranking.rank} · {formatCurrency(ranking.reference_price)}</span>) : "—"}{supplier.contracts.map((contract) => <span className="ranking-chip" key={contract.contract_id}>{contract.contract_id} · ends {formatDate(contract.end_date)}</span>)}</td></tr>)}
      </tbody></table></div>}
    </Panel>
    <p className="helper-text">Preferred ranks use eligible purchase and contract history under the effective policy. Suppliers without ranking history are not assigned fabricated ranks.</p>
  </div>;
}

function ContractsPage() {
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actor, setActor] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setContracts(await apiRequest<Contract[]>("/contracts"));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load contracts.");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const review = async (contract: Contract, verified: boolean) => {
    if (!actor.trim() || !reviewReason.trim()) {
      setError("Enter a reviewer ID and review reason.");
      return;
    }
    setBusyId(contract.id);
    setError("");
    try {
      await apiRequest(`/contracts/${contract.id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actor_id: actor, verified, reason: reviewReason }),
      });
      setReviewReason("");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to save contract review.");
    } finally {
      setBusyId(null);
    }
  };
  return <div className="page-stack">
    {error && <StateMessage error={error} onRetry={() => void load()} />}
    <Panel title="Contract register" subtitle="Ceilings show register baseline, allocated value and currently remaining capacity.">
      <div className="contract-review-bar">
        <label className="field-label">Procurement reviewer ID<input className="input" value={actor} onChange={(event) => setActor(event.target.value)} placeholder="Required for PDF contract review" /></label>
        <label className="field-label">Review note<input className="input" value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} placeholder="Record the source checked and review outcome" /></label>
      </div>
      {loading ? <StateMessage loading /> : !contracts.length ? <StateMessage empty="No contract records found. Import the contract register and signed PDFs to populate this page." /> : (
        <div className="contract-list">
          {contracts.map((contract) => <article className="contract-card" key={contract.id}>
            <div className="contract-topline"><div><div className="contract-title"><h3>{contract.contract_id}</h3><StatusPill>{contract.review_status}</StatusPill>{contract.source_type === "pdf_manual_review" && <span className="source-tag">PDF-only source</span>}</div><p>{contract.supplier_name ?? contract.supplier_id} · {formatDate(contract.start_date)} — {formatDate(contract.end_date)}</p></div>
              {contract.source_type === "pdf_manual_review" && !contract.verified && <button className="button-primary" disabled={busyId === contract.id} onClick={() => void review(contract, true)}>{busyId === contract.id ? <LoaderCircle size={16} className="animate-spin" /> : <BadgeCheck size={16} />} Verify contract</button>}
            </div>
            <div className="ceiling-grid"><Ceiling label="Contract ceiling" amount={contract.value_cap} /><Ceiling label="Register consumption" amount={contract.baseline_consumed_value} /><Ceiling label="Reserved / committed" amount={contract.allocated_value} /><Ceiling label="Remaining ceiling" amount={contract.remaining_value} /></div>
            <details className="contract-lines"><summary>Covered items ({contract.lines.length})</summary><div className="table-scroll"><table className="data-table compact-table"><thead><tr><th>Item</th><th>Description</th><th>UOM</th><th>Rate</th></tr></thead><tbody>{contract.lines.map((line) => <tr key={line.item_code}><td>{line.item_code}</td><td>{line.description}</td><td>{line.uom}</td><td>{formatCurrency(line.rate)}</td></tr>)}</tbody></table></div></details>
            <div className="contract-foot"><span>Source: {contract.source_reference ?? contract.source_type}</span><span>{contract.verified ? "Buyer verified" : "Review required"}</span></div>
            {contract.source_type === "pdf_manual_review" && !contract.verified && <button className="text-action reject-contract" disabled={busyId === contract.id} onClick={() => void review(contract, false)}>Reject verification<ArrowRight size={14} /></button>}
          </article>)}
        </div>
      )}
    </Panel>
    <p className="helper-text">New PDF-only contract extraction is available through the backend import workflow. This screen exposes the review action; it does not invent contract values or terms.</p>
  </div>;
}

function Ceiling({ label, amount }: { label: string; amount: string }) {
  return <div><small>{label}</small><strong>{formatCurrency(amount)}</strong></div>;
}

export default App;
