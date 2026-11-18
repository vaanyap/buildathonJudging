import { useEffect, useState } from "react";
import { api, downloadCsv } from "../../api";
import { StatusPill } from "../../components/StatusPill";
import { formatScore, formatStamp } from "../../format";
import type { AuditEvent, Evaluation, ResultProject, ResultsReport } from "../../types";

function describeAudit(audit: AuditEvent): string {
  if (audit.action === "submit") return "Submitted the scorecard";
  if (audit.action === "reopen") return "Reopened the scorecard";
  return Object.entries(audit.changes)
    .map(([key, value]) => {
      if (value && typeof value === "object" && "old" in value && "new" in value) {
        const change = value as { old: unknown; new: unknown };
        return `${key}: ${String(change.old)} → ${String(change.new)}`;
      }
      return key;
    })
    .join("; ");
}

export default function Results() {
  const [report, setReport] = useState<ResultsReport | null>(null);
  const [track, setTrack] = useState("all");
  const [openId, setOpenId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    setReport(await api<ResultsReport>("/api/organizer/results"));
  }

  useEffect(() => {
    let stop = false;
    const pull = () => {
      load().catch((err: unknown) => {
        if (!stop) setError(err instanceof Error ? err.message : "Couldn't load results");
      });
    };
    pull();
    const id = window.setInterval(pull, 10000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);

  async function reopen(evaluation: Evaluation) {
    if (!window.confirm(`Reopen ${evaluation.judge_name ?? "this judge"}'s submission?`)) return;
    setError("");
    try {
      await api(`/api/organizer/evaluations/${evaluation.id}/reopen`, { method: "POST" });
      setNotice("Evaluation reopened. It no longer counts as complete.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't reopen");
    }
  }

  async function exportFile(path: string, filename: string) {
    setError("");
    try {
      await downloadCsv(path, filename);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    }
  }

  if (!report) return <p className="page-loading">Loading results…</p>;
  const tracks = Array.from(new Set(report.projects.map((project) => project.track_name))).sort();
  const rows = report.projects.filter((project) => track === "all" || project.track_name === track);
  const discrepancies = report.projects.filter((project) => project.discrepancy);

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Results</h1>
          <p className="lede">Ranks are within each track and use submitted quality scores only. Alignment is shown beside the rank, not inside it.</p>
        </div>
        <div className="toolbar">
          <button className="btn" type="button" onClick={() => void exportFile("/api/organizer/export/results.csv", "results.csv")}>
            Export results
          </button>
          <button className="btn" type="button" onClick={() => void exportFile("/api/organizer/export/feedback.csv", "feedback.csv")}>
            Export feedback
          </button>
        </div>
      </header>
      {error && <p className="banner bad">{error}</p>}
      {notice && <p className="banner ok">{notice}</p>}
      {discrepancies.length > 0 && (
        <p className="banner warn">
          {discrepancies.length} project{discrepancies.length === 1 ? "" : "s"} with a scoring discrepancy:{" "}
          {discrepancies.map((project) => project.project_name).join(", ")}.
        </p>
      )}
      <div className="filters">
        <button type="button" className={track === "all" ? "chip on" : "chip"} onClick={() => setTrack("all")}>
          All tracks
        </button>
        {tracks.map((name) => (
          <button key={name} type="button" className={track === name ? "chip on" : "chip"} onClick={() => setTrack(name)}>
            {name}
          </button>
        ))}
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Rank</th>
              <th>Project</th>
              <th>Track</th>
              <th>Average</th>
              {report.criteria.map((criterion) => (
                <th key={criterion.key}>{criterion.name}</th>
              ))}
              <th>Alignment</th>
              <th>Submitted</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((project) => (
              <ResultRows
                key={project.project_id}
                project={project}
                criteria={report.criteria}
                open={openId === project.project_id}
                onToggle={() => setOpenId(openId === project.project_id ? null : project.project_id)}
                onReopen={(evaluation) => void reopen(evaluation)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ResultRows({
  project,
  criteria,
  open,
  onToggle,
  onReopen,
}: {
  project: ResultProject;
  criteria: ResultsReport["criteria"];
  open: boolean;
  onToggle: () => void;
  onReopen: (evaluation: Evaluation) => void;
}) {
  const columns = 7 + criteria.length;
  return (
    <>
      <tr className={project.discrepancy ? "discrepancy" : ""}>
        <td>{project.rank ?? "—"}</td>
        <td>
          <button className="linkish" type="button" onClick={onToggle}>
            {project.project_name}
          </button>
          {project.discrepancy && <span className="pill delay-overrun">Discrepancy</span>}
        </td>
        <td>{project.track_name}</td>
        <td>{formatScore(project.overall)}</td>
        {criteria.map((criterion) => (
          <td key={criterion.key}>{formatScore(project.criterion_averages[criterion.key])}</td>
        ))}
        <td>
          {project.alignment_counts.strong} strong · {project.alignment_counts.partial} partial · {project.alignment_counts.weak} weak
        </td>
        <td>
          {project.submitted_count}/{project.assigned_count}
        </td>
        <td>
          <button className="btn small" type="button" onClick={onToggle}>
            {open ? "Hide" : "Scores"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="detail-row">
          <td colSpan={columns}>
            {project.discrepancy && <p className="hint">{project.discrepancy_reasons.join(" ")}</p>}
            <p className="muted">{project.team_members.join(", ")}</p>
            {project.evaluations.length === 0 && <p className="muted">No scorecards have been started.</p>}
            {project.evaluations.map((evaluation) => (
              <article key={evaluation.id} className="scorecard">
                <div className="panel-head">
                  <strong>{evaluation.judge_name}</strong>
                  <StatusPill status={evaluation.status} />
                  <span className="muted">overall {formatScore(evaluation.overall)}</span>
                  {evaluation.status === "submitted" && (
                    <button className="btn small" type="button" onClick={() => onReopen(evaluation)}>
                      Reopen
                    </button>
                  )}
                </div>
                {evaluation.status !== "submitted" && <p className="hint">This scorecard is not part of the average.</p>}
                <div className="wrap">
                  {evaluation.scores.map((score) => (
                    <span key={score.key} className="score-chip">
                      {score.name}: {score.value ?? "—"}
                    </span>
                  ))}
                </div>
                <p>
                  <strong>Alignment. </strong>
                  {evaluation.alignment ?? "Not set"}
                  {evaluation.alignment_explanation ? ` — ${evaluation.alignment_explanation}` : ""}
                </p>
                {evaluation.cross_track_flag && (
                  <p>
                    <strong>Cross-track. </strong>
                    {evaluation.recommended_track_name}: {evaluation.cross_track_explanation}
                  </p>
                )}
                <p>
                  <strong>Formal feedback. </strong>
                  {evaluation.formal_feedback || "None yet."}
                </p>
                {evaluation.audits && evaluation.audits.length > 0 && (
                  <ul className="audit">
                    {evaluation.audits.map((audit) => (
                      <li key={audit.id}>
                        {formatStamp(audit.created_at)} · {audit.actor_name ?? "Someone"} · {describeAudit(audit)}
                      </li>
                    ))}
                  </ul>
                )}
              </article>
            ))}
          </td>
        </tr>
      )}
    </>
  );
}
