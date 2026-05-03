"use client";

import React, { useEffect, useState } from "react";

interface CountEntry {
    label: string;
    count: number;
}

interface TraceRunSummary {
    workflow_id: string;
    engine: string;
    selected_engine: string;
    status: string;
    task: string;
    logged_at: string | null;
    route_rationale: string | null;
    summary: string[];
    checks_passed: boolean | null;
    boundary_ok: boolean | null;
    design_review_passed: boolean | null;
    api_review_required: boolean;
    api_review_passed: boolean | null;
    issue_count: number;
    command_failure_count: number;
    verification_command_count: number;
    touched_file_count: number;
    doctrine_note_count: number;
    guidance_match_count: number;
    guidance_paths: string[];
    langsmith_trace_url: string | null;
    run_log_path: string;
}

interface TraceDiagnostics {
    total_runs: number;
    runs_with_trace_urls: number;
    boundary_block_count: number;
    runs_with_doctrine: number;
    runs_with_guidance: number;
    design_review_fail_count: number;
    api_review_fail_count: number;
    verification_pass_rate: number | null;
    engine_mix: Record<string, number>;
    status_mix: Record<string, number>;
    latest_logged_at: string | null;
}

interface TraceLearningNoteDetail {
    label: string;
    value: string;
}

interface TraceLearningNote {
    slug: string;
    title: string;
    summary: string;
    runbook_path: string;
    details: TraceLearningNoteDetail[];
}

interface TraceLearning {
    recurring_issues: CountEntry[];
    common_verification_commands: CountEntry[];
    common_touched_files: CountEntry[];
    common_route_rationales: CountEntry[];
    common_guidance_paths: CountEntry[];
    chart_tuning_notes: TraceLearningNote[];
}

interface TraceDashboardPayload {
    project_name: string;
    tracing_enabled: boolean;
    api_key_configured: boolean;
    api_host: string | null;
    recent_runs: TraceRunSummary[];
    diagnostics: TraceDiagnostics;
    learning: TraceLearning;
}

const panelClasses = "rounded-xl border border-[#dbe9f6] bg-[#f7fbff] shadow-sm";

const normalizePayload = (data: TraceDashboardPayload): TraceDashboardPayload => ({
    ...data,
    recent_runs: Array.isArray(data.recent_runs) ? data.recent_runs.map((run) => ({
        ...run,
        design_review_passed: run.design_review_passed ?? null,
        api_review_required: run.api_review_required ?? false,
        api_review_passed: run.api_review_passed ?? null,
        doctrine_note_count: run.doctrine_note_count ?? 0,
        guidance_match_count: run.guidance_match_count ?? 0,
        guidance_paths: Array.isArray(run.guidance_paths) ? run.guidance_paths : [],
    })) : [],
    diagnostics: {
        total_runs: data.diagnostics?.total_runs ?? 0,
        runs_with_trace_urls: data.diagnostics?.runs_with_trace_urls ?? 0,
        boundary_block_count: data.diagnostics?.boundary_block_count ?? 0,
        runs_with_doctrine: data.diagnostics?.runs_with_doctrine ?? 0,
        runs_with_guidance: data.diagnostics?.runs_with_guidance ?? 0,
        design_review_fail_count: data.diagnostics?.design_review_fail_count ?? 0,
        api_review_fail_count: data.diagnostics?.api_review_fail_count ?? 0,
        verification_pass_rate: data.diagnostics?.verification_pass_rate ?? null,
        engine_mix: data.diagnostics?.engine_mix ?? {},
        status_mix: data.diagnostics?.status_mix ?? {},
        latest_logged_at: data.diagnostics?.latest_logged_at ?? null,
    },
    learning: {
        recurring_issues: Array.isArray(data.learning?.recurring_issues) ? data.learning.recurring_issues : [],
        common_verification_commands: Array.isArray(data.learning?.common_verification_commands) ? data.learning.common_verification_commands : [],
        common_touched_files: Array.isArray(data.learning?.common_touched_files) ? data.learning.common_touched_files : [],
        common_route_rationales: Array.isArray(data.learning?.common_route_rationales) ? data.learning.common_route_rationales : [],
        common_guidance_paths: Array.isArray(data.learning?.common_guidance_paths) ? data.learning.common_guidance_paths : [],
        chart_tuning_notes: Array.isArray(data.learning?.chart_tuning_notes) ? data.learning.chart_tuning_notes : [],
    },
});

const formatTimestamp = (value: string | null): string => {
    if (!value) {
        return "No timestamp";
    }

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return value;
    }

    return new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
    }).format(date);
};

const statusTone = (status: string): string => {
    const normalized = status.toLowerCase();
    if (normalized === "completed") {
        return "bg-[#e5f5e0] text-[#238b45] border-[#a1d99b]";
    }
    if (normalized === "needs_attention") {
        return "bg-[#fff5eb] text-[#d94801] border-[#fdae6b]";
    }
    if (normalized === "planned") {
        return "bg-[#eff3ff] text-[#2171b5] border-[#bdd7e7]";
    }
    return "bg-white text-[#636363] border-[#d9d9d9]";
};

const reviewTone = (passed: boolean | null): string => {
    if (passed === true) {
        return "bg-[#e5f5e0] text-[#238b45]";
    }
    if (passed === false) {
        return "bg-[#fff5eb] text-[#d94801]";
    }
    return "bg-[#f3f4f6] text-[#6b7280]";
};

const CountList: React.FC<{ items: CountEntry[]; emptyLabel: string }> = ({ items, emptyLabel }) => {
    if (!items.length) {
        return <p className="text-sm text-[#6b7280]">{emptyLabel}</p>;
    }

    return (
        <ul className="space-y-2">
            {items.map((item) => (
                <li key={item.label} className="flex items-start justify-between gap-4 text-sm text-[#084594]">
                    <span className="leading-5">{item.label}</span>
                    <span className="rounded-full bg-white px-2 py-0.5 text-xs font-semibold text-[#2171b5]">
                        {item.count}
                    </span>
                </li>
            ))}
        </ul>
    );
};

const LearningNoteList: React.FC<{ notes: TraceLearningNote[] }> = ({ notes }) => {
    if (!notes.length) {
        return <p className="text-sm text-[#6b7280]">No chart tuning notes recorded yet.</p>;
    }

    return (
        <div className="space-y-3">
            {notes.map((note) => (
                <article key={note.slug} className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <p className="text-sm font-semibold text-[#084594]">{note.title}</p>
                        <span className="rounded-full bg-[#eff3ff] px-2 py-1 text-xs font-semibold text-[#2171b5]">Learning artifact</span>
                    </div>
                    <p className="mt-2 text-sm text-[#4b5563]">{note.summary}</p>
                    <p className="mt-3 text-xs text-[#6b7280]">Runbook: <span className="font-mono text-[#2171b5]">{note.runbook_path}</span></p>
                    <div className="mt-3 flex flex-wrap gap-2 text-xs text-[#4b5563]">
                        {note.details.map((detail) => (
                            <span key={`${note.slug}-${detail.label}`} className="rounded-full bg-[#f7fbff] px-2 py-1">
                                {detail.label}: {detail.value}
                            </span>
                        ))}
                    </div>
                </article>
            ))}
        </div>
    );
};

const TraceDashboard: React.FC = () => {
    const [payload, setPayload] = useState<TraceDashboardPayload | null>(null);
    const [error, setError] = useState<string>("");

    useEffect(() => {
        let cancelled = false;

        fetch("/api/agentic/traces")
            .then((response) => {
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                return response.json();
            })
            .then((data: TraceDashboardPayload) => {
                if (!cancelled) {
                    setPayload(normalizePayload(data));
                }
            })
            .catch((fetchError: Error) => {
                if (!cancelled) {
                    setError(fetchError.message || "Unable to load trace dashboard.");
                }
            });

        return () => {
            cancelled = true;
        };
    }, []);

    if (error) {
        return (
            <div className={`${panelClasses} p-6`}>
                <h1 className="text-2xl font-semibold tracking-tight text-[#084594]">Trace Dashboard</h1>
                <p className="mt-3 text-sm text-[#b91c1c]">Unable to load trace data: {error}</p>
            </div>
        );
    }

    if (!payload) {
        return (
            <div className={`${panelClasses} p-6`}>
                <h1 className="text-2xl font-semibold tracking-tight text-[#084594]">Trace Dashboard</h1>
                <p className="mt-3 text-sm text-[#6baed6]">Loading LangSmith status and recent agent runs...</p>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <section className={`${panelClasses} overflow-hidden`}>
                <div className="border-b border-[#dbe9f6] bg-gradient-to-r from-[#eff3ff] to-[#f7fbff] px-6 py-6">
                    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#6baed6]">LangSmith</p>
                    <h1 className="mt-2 text-3xl font-semibold tracking-tight text-[#084594]">Trace Dashboard</h1>
                    <p className="mt-3 max-w-3xl text-sm leading-6 text-[#4b5563]">
                        Inspect the battlestats agent workflow process from two angles: operational diagnostics for the current tracing setup and reusable learning signals from recent local runs.
                    </p>
                </div>
                <div className="grid gap-4 px-6 py-5 md:grid-cols-2 xl:grid-cols-4">
                    <div className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                        <p className="text-xs font-semibold uppercase tracking-wide text-[#6baed6]">Tracing</p>
                        <p className="mt-2 text-xl font-semibold text-[#084594]">{payload.tracing_enabled ? "Enabled" : "Disabled"}</p>
                        <p className="mt-2 text-sm text-[#6b7280]">{payload.tracing_enabled ? "Runs can publish LangSmith traces when credentials are valid." : "Local logs still work even when hosted tracing is off."}</p>
                    </div>
                    <div className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                        <p className="text-xs font-semibold uppercase tracking-wide text-[#6baed6]">Project</p>
                        <p className="mt-2 text-xl font-semibold text-[#084594]">{payload.project_name}</p>
                        <p className="mt-2 text-sm text-[#6b7280]">{payload.api_host || "Using the default LangSmith host."}</p>
                    </div>
                    <div className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                        <p className="text-xs font-semibold uppercase tracking-wide text-[#6baed6]">API Key</p>
                        <p className="mt-2 text-xl font-semibold text-[#084594]">{payload.api_key_configured ? "Present" : "Missing"}</p>
                        <p className="mt-2 text-sm text-[#6b7280]">The dashboard never exposes the secret itself.</p>
                    </div>
                    <div className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                        <p className="text-xs font-semibold uppercase tracking-wide text-[#6baed6]">Recent Runs</p>
                        <p className="mt-2 text-xl font-semibold text-[#084594]">{payload.diagnostics.total_runs}</p>
                        <p className="mt-2 text-sm text-[#6b7280]">Latest activity: {formatTimestamp(payload.diagnostics.latest_logged_at)}</p>
                    </div>
                </div>
            </section>

            <section className="grid gap-6 lg:grid-cols-[1.35fr_1fr]">
                <div className={`${panelClasses} p-6`}>
                    <div className="flex items-center justify-between gap-4">
                        <div>
                            <h2 className="text-xl font-semibold text-[#084594]">Recent Runs</h2>
                            <p className="mt-1 text-sm text-[#6b7280]">Local workflow history with trace links when available.</p>
                        </div>
                        <div className="rounded-full border border-[#dbe9f6] bg-white px-3 py-1 text-xs font-semibold text-[#2171b5]">
                            {payload.diagnostics.runs_with_trace_urls} with trace URLs
                        </div>
                    </div>

                    {!payload.recent_runs.length ? (
                        <div className="mt-5 rounded-lg border border-dashed border-[#c6dbef] bg-white p-5 text-sm text-[#6b7280]">
                            No workflow logs found yet. Run one of the agent commands from the server directory, then refresh this page.
                        </div>
                    ) : (
                        <div className="mt-5 space-y-4">
                            {payload.recent_runs.map((run) => (
                                <article key={`${run.engine}-${run.workflow_id}`} className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                                    <div className="flex flex-wrap items-center justify-between gap-3">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <span className="rounded-full bg-[#eff3ff] px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-[#2171b5]">
                                                {run.selected_engine}
                                            </span>
                                            <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${statusTone(run.status)}`}>
                                                {run.status}
                                            </span>
                                        </div>
                                        <p className="text-xs text-[#6b7280]">{formatTimestamp(run.logged_at)}</p>
                                    </div>

                                    <p className="mt-3 text-sm font-semibold text-[#084594]">{run.task}</p>
                                    {run.route_rationale ? (
                                        <p className="mt-2 text-sm text-[#4b5563]">{run.route_rationale}</p>
                                    ) : null}

                                    {run.summary.length ? (
                                        <ul className="mt-3 space-y-1 text-sm text-[#4b5563]">
                                            {run.summary.map((line) => (
                                                <li key={line}>- {line}</li>
                                            ))}
                                        </ul>
                                    ) : null}

                                    <div className="mt-4 flex flex-wrap gap-2 text-xs text-[#4b5563]">
                                        <span className="rounded-full bg-[#f7fbff] px-2 py-1">checks: {run.checks_passed === null ? "n/a" : run.checks_passed ? "pass" : "fail"}</span>
                                        <span className="rounded-full bg-[#f7fbff] px-2 py-1">boundaries: {run.boundary_ok === null ? "n/a" : run.boundary_ok ? "pass" : "fail"}</span>
                                        <span className={`rounded-full px-2 py-1 ${reviewTone(run.design_review_passed)}`}>design review: {run.design_review_passed === null ? "n/a" : run.design_review_passed ? "pass" : "revise"}</span>
                                        <span className={`rounded-full px-2 py-1 ${reviewTone(run.api_review_required ? run.api_review_passed : null)}`}>api review: {!run.api_review_required ? "not needed" : run.api_review_passed === null ? "n/a" : run.api_review_passed ? "pass" : "revise"}</span>
                                        <span className="rounded-full bg-[#f7fbff] px-2 py-1">doctrine notes: {run.doctrine_note_count}</span>
                                        <span className="rounded-full bg-[#f7fbff] px-2 py-1">guidance matches: {run.guidance_match_count}</span>
                                        <span className="rounded-full bg-[#f7fbff] px-2 py-1">issues: {run.issue_count}</span>
                                        <span className="rounded-full bg-[#f7fbff] px-2 py-1">command failures: {run.command_failure_count}</span>
                                        <span className="rounded-full bg-[#f7fbff] px-2 py-1">verification commands: {run.verification_command_count}</span>
                                        <span className="rounded-full bg-[#f7fbff] px-2 py-1">touched files: {run.touched_file_count}</span>
                                    </div>

                                    {run.guidance_paths.length ? (
                                        <div className="mt-4">
                                            <p className="text-xs font-semibold uppercase tracking-wide text-[#6baed6]">Retrieved guidance</p>
                                            <div className="mt-2 flex flex-wrap gap-2 text-xs text-[#4b5563]">
                                                {run.guidance_paths.map((path) => (
                                                    <span key={`${run.workflow_id}-${path}`} className="rounded-full bg-[#eff3ff] px-2 py-1 text-[#2171b5]">
                                                        {path}
                                                    </span>
                                                ))}
                                            </div>
                                        </div>
                                    ) : null}

                                    <div className="mt-4 flex flex-wrap gap-4 text-sm">
                                        <span className="text-[#6b7280]">log: {run.run_log_path}</span>
                                        {run.langsmith_trace_url ? (
                                            <a href={run.langsmith_trace_url} target="_blank" rel="noreferrer" className="font-medium text-[#2171b5] underline-offset-2 hover:text-[#084594] hover:underline">
                                                Open LangSmith trace
                                            </a>
                                        ) : (
                                            <span className="text-[#9ca3af]">No trace URL stored for this run.</span>
                                        )}
                                    </div>
                                </article>
                            ))}
                        </div>
                    )}
                </div>

                <div className="space-y-6">
                    <section className={`${panelClasses} p-6`}>
                        <h2 className="text-xl font-semibold text-[#084594]">Diagnostics</h2>
                        <div className="mt-4 grid gap-3 sm:grid-cols-2">
                            <div className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                                <p className="text-xs font-semibold uppercase tracking-wide text-[#6baed6]">Verification Pass Rate</p>
                                <p className="mt-2 text-2xl font-semibold text-[#084594]">{payload.diagnostics.verification_pass_rate == null ? "n/a" : `${payload.diagnostics.verification_pass_rate}%`}</p>
                            </div>
                            <div className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                                <p className="text-xs font-semibold uppercase tracking-wide text-[#6baed6]">Boundary Blocks</p>
                                <p className="mt-2 text-2xl font-semibold text-[#084594]">{payload.diagnostics.boundary_block_count}</p>
                            </div>
                            <div className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                                <p className="text-xs font-semibold uppercase tracking-wide text-[#6baed6]">Doctrine Coverage</p>
                                <p className="mt-2 text-2xl font-semibold text-[#084594]">{payload.diagnostics.runs_with_doctrine}</p>
                                <p className="mt-2 text-sm text-[#6b7280]">Runs that recorded doctrine notes during planning or review.</p>
                            </div>
                            <div className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                                <p className="text-xs font-semibold uppercase tracking-wide text-[#6baed6]">Guidance Retrieval</p>
                                <p className="mt-2 text-2xl font-semibold text-[#084594]">{payload.diagnostics.runs_with_guidance}</p>
                                <p className="mt-2 text-sm text-[#6b7280]">Runs that matched curated runbooks or review notes.</p>
                            </div>
                            <div className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                                <p className="text-xs font-semibold uppercase tracking-wide text-[#6baed6]">Design Revisions</p>
                                <p className="mt-2 text-2xl font-semibold text-[#084594]">{payload.diagnostics.design_review_fail_count}</p>
                                <p className="mt-2 text-sm text-[#6b7280]">Runs that failed the design-pattern review gate at least once.</p>
                            </div>
                            <div className="rounded-lg border border-[#dbe9f6] bg-white p-4">
                                <p className="text-xs font-semibold uppercase tracking-wide text-[#6baed6]">API Revisions</p>
                                <p className="mt-2 text-2xl font-semibold text-[#084594]">{payload.diagnostics.api_review_fail_count}</p>
                                <p className="mt-2 text-sm text-[#6b7280]">Runs that required API review and still needed plan revision.</p>
                            </div>
                        </div>
                        <div className="mt-5 grid gap-5">
                            <div>
                                <p className="text-sm font-semibold text-[#084594]">Engine mix</p>
                                <div className="mt-2 flex flex-wrap gap-2 text-sm text-[#4b5563]">
                                    {Object.entries(payload.diagnostics.engine_mix).length ? Object.entries(payload.diagnostics.engine_mix).map(([key, value]) => (
                                        <span key={key} className="rounded-full bg-white px-3 py-1">{key}: {value}</span>
                                    )) : <span className="text-[#6b7280]">No runs yet.</span>}
                                </div>
                            </div>
                            <div>
                                <p className="text-sm font-semibold text-[#084594]">Status mix</p>
                                <div className="mt-2 flex flex-wrap gap-2 text-sm text-[#4b5563]">
                                    {Object.entries(payload.diagnostics.status_mix).length ? Object.entries(payload.diagnostics.status_mix).map(([key, value]) => (
                                        <span key={key} className="rounded-full bg-white px-3 py-1">{key}: {value}</span>
                                    )) : <span className="text-[#6b7280]">No statuses available.</span>}
                                </div>
                            </div>
                        </div>
                    </section>

                    <section className={`${panelClasses} p-6`}>
                        <h2 className="text-xl font-semibold text-[#084594]">Learning</h2>
                        <div className="mt-5 space-y-5">
                            <div>
                                <p className="mb-2 text-sm font-semibold text-[#084594]">Recurring issues</p>
                                <CountList items={payload.learning.recurring_issues} emptyLabel="No repeated issues recorded yet." />
                            </div>
                            <div>
                                <p className="mb-2 text-sm font-semibold text-[#084594]">Common verification commands</p>
                                <CountList items={payload.learning.common_verification_commands} emptyLabel="No verification commands recorded yet." />
                            </div>
                            <div>
                                <p className="mb-2 text-sm font-semibold text-[#084594]">Common touched files</p>
                                <CountList items={payload.learning.common_touched_files} emptyLabel="No touched-file patterns recorded yet." />
                            </div>
                            <div>
                                <p className="mb-2 text-sm font-semibold text-[#084594]">Route rationale patterns</p>
                                <CountList items={payload.learning.common_route_rationales} emptyLabel="No route rationales recorded yet." />
                            </div>
                            <div>
                                <p className="mb-2 text-sm font-semibold text-[#084594]">Common guidance sources</p>
                                <CountList items={payload.learning.common_guidance_paths} emptyLabel="No retrieved guidance sources recorded yet." />
                            </div>
                            <div>
                                <p className="mb-2 text-sm font-semibold text-[#084594]">Chart tuning notes</p>
                                <LearningNoteList notes={payload.learning.chart_tuning_notes} />
                            </div>
                        </div>
                    </section>
                </div>
            </section>
        </div>
    );
};

export default TraceDashboard;