import { useCallback, useEffect, useState } from "react";
import {
  ArrowDownToLine,
  BadgeCheck,
  BookOpenCheck,
  Check,
  Clock3,
  FileText,
  GitCompareArrows,
  LoaderCircle,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import { apiRequest } from "../lib/api";

type PolicyVersion = {
  id: number;
  version: string;
  effective_from: string;
  status: string;
  source_reference: string | null;
  change_summary: string | null;
  created_by: string | null;
  approved_by: string | null;
  approved_at: string | null;
  parent_policy_version_id: number | null;
};

type PolicyDraft = {
  id: number;
  version: string;
  effective_from: string;
  status: string;
  extracted_text: string;
  policy_document: Record<string, unknown>;
  change_summary: string | null;
  extraction_status: string;
};

type Diff = {
  policy_id: number;
  version: string;
  base_version: string;
  status: string;
  effective_from: string;
  summary: string;
  changes: { path: string; before: unknown; after: unknown }[];
};

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

export default function PolicyManagement({ embedded = false }: { embedded?: boolean }) {
  const [policies, setPolicies] = useState<PolicyVersion[]>([]);
  const [active, setActive] = useState<Record<string, unknown> | null>(null);
  const [draft, setDraft] = useState<PolicyDraft | null>(null);
  const [policyJson, setPolicyJson] = useState("");
  const [diff, setDiff] = useState<Diff | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [actorId, setActorId] = useState("");
  const [approvalReason, setApprovalReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    const [versions, activePolicy] = await Promise.all([
      apiRequest<PolicyVersion[]>("/policies"),
      apiRequest<Record<string, unknown>>("/policies/active"),
    ]);
    setPolicies(versions);
    setActive(activePolicy);
  }, []);

  useEffect(() => {
    void refresh().catch((caught: unknown) =>
      setError(caught instanceof Error ? caught.message : "Unable to load policies."),
    );
  }, [refresh]);

  const runAction = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Policy action failed.");
    } finally {
      setBusy(false);
    }
  };

  const upload = () =>
    runAction(async () => {
      if (!file) throw new Error("Choose a policy PDF first.");
      if (!effectiveFrom) throw new Error("Set the proposed effective date.");
      if (!actorId.trim()) throw new Error("Enter your Procurement user ID.");
      const body = new FormData();
      body.set("file", file);
      body.set("effective_from", effectiveFrom);
      body.set("actor_id", actorId);
      const response = await apiRequest<PolicyDraft>("/policies/upload", {
        method: "POST",
        body,
      });
      setDraft(response);
      setPolicyJson(pretty(response.policy_document));
      setDiff(null);
      setNotice(`Policy ${response.version} uploaded. Review the extracted text and edit structured rules before validation.`);
      await refresh();
    });

  const validate = () =>
    runAction(async () => {
      if (!draft) throw new Error("Upload a PDF to create a draft.");
      if (!actorId.trim()) throw new Error("Enter your Procurement user ID.");
      let document: Record<string, unknown>;
      try {
        document = JSON.parse(policyJson) as Record<string, unknown>;
      } catch {
        throw new Error("The structured policy editor contains invalid JSON.");
      }
      const result = await apiRequest<{ status: string; change_summary: string }>(
        `/policies/${draft.id}/validate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ policy_document: document, actor_id: actorId }),
        },
      );
      setNotice(`Validation passed. ${result.change_summary}`);
      const nextDiff = await apiRequest<Diff>(`/policies/${draft.id}/diff`);
      setDiff(nextDiff);
      setDraft({ ...draft, status: result.status, policy_document: document });
      await refresh();
    });

  const approve = () =>
    runAction(async () => {
      if (!draft) throw new Error("Choose a validated policy draft.");
      if (!actorId.trim() || !approvalReason.trim()) {
        throw new Error("Approver ID and written approval reason are required.");
      }
      const result = await apiRequest<{ status: string }>(
        `/policies/${draft.id}/approve`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            actor_id: actorId,
            reason: approvalReason,
          }),
        },
      );
      setNotice(
        result.status === "active"
          ? `Policy ${draft.version} is active.`
          : `Policy ${draft.version} is approved and will activate on ${draft.effective_from}.`,
      );
      setDraft(null);
      setDiff(null);
      setApprovalReason("");
      await refresh();
    });

  const reject = () =>
    runAction(async () => {
      if (!draft) throw new Error("Choose a policy draft.");
      if (!actorId.trim() || !approvalReason.trim()) {
        throw new Error("Procurement user ID and a written rejection reason are required.");
      }
      await apiRequest(`/policies/${draft.id}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actor_id: actorId, reason: approvalReason }),
      });
      setNotice(`Policy ${draft.version} was rejected and retained in version history.`);
      setDraft(null);
      setDiff(null);
      setApprovalReason("");
      await refresh();
    });

  const openVersion = (version: PolicyVersion) => {
    setNotice(
      `Policy ${version.version} is ${version.status}. Its version metadata and approval history are preserved.`,
    );
  };

  return (
    <div className={embedded ? "space-y-6 text-slate-900" : "min-h-screen bg-slate-50 text-slate-900"}>
      {!embedded && <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-xl bg-teal-700 text-white">
              <BookOpenCheck size={21} />
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-teal-700">
                Cantill Procurement
              </p>
              <h1 className="text-lg font-semibold">Policy management</h1>
            </div>
          </div>
          <span className="inline-flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-800">
            <span className="size-2 rounded-full bg-emerald-500" />
            Active policy {String(active?.version ?? "…")}
          </span>
        </div>
      </header>}

      <main className={embedded ? "grid gap-6 lg:grid-cols-[minmax(0,1.7fr)_minmax(300px,0.8fr)]" : "mx-auto grid max-w-7xl gap-6 px-6 py-8 lg:grid-cols-[minmax(0,1.7fr)_minmax(300px,0.8fr)]"}>
        <section className="space-y-6">
          <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="mb-5 flex items-start justify-between gap-4">
              <div>
                <h2 className="text-xl font-semibold">Propose a policy amendment</h2>
                <p className="mt-1 text-sm text-slate-500">
                  Upload the signed document, then review and edit its structured policy draft.
                </p>
              </div>
              <span className="rounded-lg bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-800">
                Human approval required
              </span>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="field-label">
                Policy PDF
                <span className="file-picker mt-2">
                  <Upload size={17} />
                  <span className="truncate">{file?.name ?? "Choose a PDF…"}</span>
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                  />
                </span>
              </label>
              <label className="field-label">
                Proposed effective date
                <input
                  className="input mt-2"
                  type="date"
                  value={effectiveFrom}
                  onChange={(event) => setEffectiveFrom(event.target.value)}
                />
              </label>
              <label className="field-label sm:col-span-2">
                Procurement user ID
                <input
                  className="input mt-2"
                  placeholder="Your user ID"
                  value={actorId}
                  onChange={(event) => setActorId(event.target.value)}
                />
              </label>
            </div>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
              <p className="max-w-xl text-xs leading-5 text-slate-500">
                PDF text is extracted for review only. No model or document content can run code or
                directly alter procurement decisions.
              </p>
              <button className="button-primary" disabled={busy} onClick={() => void upload()}>
                {busy ? <LoaderCircle className="animate-spin" size={16} /> : <Upload size={16} />}
                Upload and extract
              </button>
            </div>
          </div>

          {draft && (
            <div className="space-y-6 rounded-2xl border border-teal-200 bg-white p-6 shadow-sm">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-xl font-semibold">Draft {draft.version}</h2>
                    <span className="status-pill">{draft.status}</span>
                  </div>
                  <p className="mt-1 text-sm text-slate-500">
                    Effective {draft.effective_from} · {draft.extraction_status.replaceAll("_", " ")}
                  </p>
                </div>
                <button
                  className="button-secondary"
                  onClick={() => setDraft(null)}
                  aria-label="Close policy draft"
                >
                  <X size={16} /> Close draft
                </button>
              </div>

              <details className="rounded-xl border border-slate-200 bg-slate-50">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium">
                  <FileText size={16} className="text-teal-700" />
                  Extracted PDF text
                  <ArrowDownToLine size={15} className="ml-auto text-slate-400" />
                </summary>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap border-t border-slate-200 p-4 text-xs leading-5 text-slate-600">
                  {draft.extracted_text || "No selectable text found. Use the structured editor below."}
                </pre>
              </details>

              <label className="field-label">
                Structured policy JSON
                <textarea
                  className="policy-editor mt-2"
                  spellCheck={false}
                  value={policyJson}
                  disabled={busy || draft.status !== "draft"}
                  onChange={(event) => setPolicyJson(event.target.value)}
                  aria-label="Structured policy JSON editor"
                />
              </label>

              {diff && (
                <div className="rounded-xl border border-indigo-100 bg-indigo-50/60 p-4">
                  <div className="flex items-center gap-2 font-semibold text-indigo-950">
                    <GitCompareArrows size={17} />
                    Policy diff · {diff.base_version} → {diff.version}
                  </div>
                  <p className="mt-2 text-sm leading-6 text-indigo-900">{diff.summary}</p>
                  <div className="mt-4 max-h-72 overflow-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="text-indigo-700">
                        <tr>
                          <th className="py-2 pr-3">Setting / rule</th>
                          <th className="py-2 pr-3">Current</th>
                          <th className="py-2">Proposed</th>
                        </tr>
                      </thead>
                      <tbody>
                        {diff.changes.map((change) => (
                          <tr className="border-t border-indigo-100" key={change.path}>
                            <td className="py-2 pr-3 font-medium">{change.path}</td>
                            <td className="py-2 pr-3 text-slate-600">{displayValue(change.before)}</td>
                            <td className="py-2 text-indigo-950">{displayValue(change.after)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              <div className="flex flex-wrap gap-3">
                <button
                  className="button-primary"
                  disabled={busy || draft.status !== "draft"}
                  onClick={() => void validate()}
                >
                  {busy ? <LoaderCircle className="animate-spin" size={16} /> : <ShieldCheck size={16} />}
                  Validate and compare
                </button>
                {draft.status === "validated" && (
                  <div className="flex min-w-full flex-col gap-3 border-t border-slate-100 pt-4 sm:flex-row">
                    <input
                      className="input flex-1"
                      placeholder="Required approval or rejection reason"
                      value={approvalReason}
                      onChange={(event) => setApprovalReason(event.target.value)}
                    />
                    <div className="flex gap-2">
                      <button className="button-secondary" disabled={busy} onClick={() => void reject()}>
                        <X size={16} /> Reject
                      </button>
                      <button className="button-approve" disabled={busy} onClick={() => void approve()}>
                        <Check size={16} /> Approve
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {(error || notice) && (
            <div
              className={`flex items-start gap-3 rounded-xl border p-4 text-sm ${
                error
                  ? "border-rose-200 bg-rose-50 text-rose-800"
                  : "border-emerald-200 bg-emerald-50 text-emerald-900"
              }`}
              role={error ? "alert" : "status"}
            >
              {error ? <X size={17} /> : <BadgeCheck size={17} />}
              <span>{error || notice}</span>
            </div>
          )}
        </section>

        <aside className="space-y-6">
          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center gap-2">
              <ShieldCheck className="text-emerald-700" size={19} />
              <h2 className="font-semibold">Effective policy</h2>
            </div>
            <div className="mt-4 rounded-xl bg-emerald-50 p-4">
              <p className="text-2xl font-semibold text-emerald-950">
                v{String(active?.version ?? "…")}
              </p>
              <p className="mt-1 text-sm text-emerald-800">
                Effective {String(active?.effective_from ?? "—")}
              </p>
              <p className="mt-3 text-xs leading-5 text-emerald-900">
                {String(active?.change_summary ?? "Current active procurement policy.")}
              </p>
            </div>
            <p className="mt-4 flex items-center gap-2 text-xs text-slate-500">
              <Clock3 size={14} /> Approved drafts activate only on their effective date.
            </p>
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="font-semibold">Version history</h2>
            <ol className="mt-4 space-y-3">
              {policies.map((policy) => (
                <li key={policy.id}>
                  <button
                    className="history-row"
                    onClick={() => void openVersion(policy)}
                    type="button"
                  >
                    <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-600">
                      <FileText size={17} />
                    </span>
                    <span className="min-w-0 flex-1 text-left">
                      <span className="flex items-center gap-2 font-medium">
                        v{policy.version}
                        {policy.status === "active" && (
                          <span className="size-2 rounded-full bg-emerald-500" />
                        )}
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-slate-500">
                        {policy.effective_from} · {policy.status}
                      </span>
                    </span>
                  </button>
                  {policy.change_summary && (
                    <p className="ml-12 mt-1 line-clamp-2 text-xs text-slate-500">
                      {policy.change_summary}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </section>
          <p className="flex gap-2 rounded-xl bg-slate-100 p-4 text-xs leading-5 text-slate-600">
            <ShieldCheck className="mt-0.5 shrink-0" size={15} />
            Rule conditions and outcomes are validated against a declarative schema; uploaded PDFs
            never contain executable application logic.
          </p>
        </aside>
      </main>
    </div>
  );
}
