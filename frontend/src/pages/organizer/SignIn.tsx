import { useEffect, useState } from "react";
import { api } from "../../api";
import { formatStamp } from "../../format";
import type { SignInMember, SignInProject, SignInReport, SignInReviewStatus } from "../../types";

type ImportSummary = {
  created: number;
  updated: number;
  unchanged: number;
  removed: number;
  total: number;
  skipped: { row: number; message: string }[];
  warnings: { row: number; message: string }[];
};

const REVIEW_OPTIONS: { value: SignInReviewStatus; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "reviewed_ok", label: "Reviewed OK" },
  { value: "disqualified", label: "Disqualified-note" },
];

export default function SignIn() {
  const [report, setReport] = useState<SignInReport | null>(null);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    setReport(await api<SignInReport>("/api/organizer/attendance"));
  }

  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : "Couldn't load the sign-in check"));
  }, []);

  async function upload(file: File) {
    setError("");
    setNotice("");
    const body = new FormData();
    body.append("file", file);
    try {
      const result = await api<ImportSummary>("/api/organizer/attendees/import", { method: "POST", body });
      setSummary(result);
      const skipped = result.skipped.length ? ` ${result.skipped.length} row${result.skipped.length === 1 ? "" : "s"} skipped.` : "";
      setNotice(
        `Day-of sign-in list updated. ${result.created} added, ${result.updated} updated, ${result.unchanged} unchanged, ${result.removed} removed. ${result.total} people signed in.${skipped}`,
      );
      if (result.skipped.length) {
        setError(result.skipped.map((item) => `Row ${item.row}: ${item.message}`).join(" "));
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't import the sign-in list");
    }
  }

  async function override(member: SignInMember, decision: "attended" | "absent" | "clear", attendeeId?: number) {
    setError("");
    try {
      await api("/api/organizer/attendance/members", {
        method: "PUT",
        body: JSON.stringify({ name: member.name, decision, attendee_id: attendeeId ?? null }),
      });
      const label =
        decision === "clear" ? "Cleared the override" : decision === "absent" ? "Marked not signed in" : "Marked signed in";
      setNotice(`${label} for ${member.name}.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save that override");
    }
  }

  if (!report) {
    return error ? <p className="banner bad">{error}</p> : <p className="page-loading">Loading sign-in check…</p>;
  }

  const flagged = report.projects.filter((project) => project.flagged);
  const clear = report.projects.filter((project) => !project.flagged);
  const possible = flagged.flatMap((project) =>
    project.members.filter((member) => member.status === "possible").map((member) => `${member.name} (${project.project_name})`),
  );
  const absentTeams = flagged.filter((project) => project.fully_absent).map((project) => project.project_name);

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Sign-in check</h1>
          <p className="lede">
            Every teammate on a submitted project should be on the day-of sign-in list. Someone missing may have submitted
            without signing in. Flagging is for review only and does not change scores, rank, or eligibility.
          </p>
        </div>
        <label className="btn file-label">
          Upload day-of sign-in list
          <input
            className="file-input"
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload(file);
              event.target.value = "";
            }}
          />
        </label>
      </header>
      <p className="hint">
        CSV with exactly 3 columns: first name, last name, and email. Headers such as First Name, first_name, or First are
        accepted. Uploading again replaces the list. Manual confirmations are kept.
      </p>
      {error && <p className="banner bad">{error}</p>}
      {notice && <p className="banner ok">{notice}</p>}
      {summary?.warnings.length ? (
        <p className="banner warn">{summary.warnings.map((item) => `Row ${item.row}: ${item.message}`).join(" ")}</p>
      ) : null}
      <div className="stats">
        <div className="stat">
          <strong>{report.attendee_count}</strong>
          <em>Signed in</em>
        </div>
        <div className="stat">
          <strong>{report.flagged_count}</strong>
          <em>Projects to review</em>
        </div>
      </div>
      {report.attendee_count === 0 && (
        <p className="banner warn">Upload the day-of sign-in list to compare it with submitted teams.</p>
      )}
      {report.flagged_count > 0 && (
        <p className="banner warn">
          {report.flagged_count} project{report.flagged_count === 1 ? "" : "s"} flagged.
          {possible.length > 0 && ` Possible match: ${possible.join(", ")}.`}
          {absentTeams.length > 0 && ` Nobody signed in: ${absentTeams.join(", ")}.`}
        </p>
      )}

      <section className="stack">
        <h2>Flagged projects</h2>
        {flagged.length === 0 && <p className="muted">Every submitted teammate is on the sign-in list.</p>}
        {flagged.map((project) => (
          <ProjectCard key={project.project_id} project={project} people={report.attendees} onOverride={override} onSaved={load} onError={setError} />
        ))}
      </section>

      <details className="panel">
        <summary>
          <strong>Signed-in teams ({clear.length})</strong>
        </summary>
        <div className="stack tight">
          {clear.map((project) => (
            <ProjectCard key={project.project_id} project={project} people={report.attendees} onOverride={override} onSaved={load} onError={setError} />
          ))}
        </div>
      </details>
    </div>
  );
}

function ProjectCard({
  project,
  people,
  onOverride,
  onSaved,
  onError,
}: {
  project: SignInProject;
  people: SignInReport["attendees"];
  onOverride: (member: SignInMember, decision: "attended" | "absent" | "clear", attendeeId?: number) => Promise<void>;
  onSaved: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [status, setStatus] = useState<SignInReviewStatus>(project.review.status);
  const [note, setNote] = useState(project.review.note);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setStatus(project.review.status);
    setNote(project.review.note);
  }, [project.review.status, project.review.note, project.review.updated_at]);

  async function saveReview() {
    setBusy(true);
    onError("");
    try {
      await api(`/api/organizer/projects/${project.project_id}/attendance`, {
        method: "PATCH",
        body: JSON.stringify({ status, note }),
      });
      await onSaved();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Couldn't save the review");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="panel">
      <div className="panel-head">
        <h2>{project.project_name}</h2>
        {project.flagged && <span className="pill sign-in-flag">Sign-in check</span>}
        {project.fully_absent && <span className="pill status-not_found">Nobody signed in</span>}
      </div>
      <p className="muted">{project.tracks.map((track) => track.name).join(", ") || "No track"}</p>
      {project.missing_members.length > 0 && (
        <p>
          <strong>Not signed in: </strong>
          {project.missing_members.join(", ")}
        </p>
      )}
      <div>
        {project.members.map((member) => (
          <div key={member.member_key} className="member-row">
            <strong>{member.name}</strong>
            <span className={`pill status-${member.status}`}>{member.label}</span>
            {member.detail && <span className="muted">{member.detail}</span>}
            {member.matched_attendee && (
              <span className="muted">
                {member.matched_attendee.name} · {member.matched_attendee.email}
              </span>
            )}
            {member.status === "possible" && member.candidates.length > 1 && (
              <span className="muted">{member.candidates.map((person) => `${person.name} (${person.email})`).join(", ")}</span>
            )}
            <button className="btn small" type="button" onClick={() => void onOverride(member, "attended")}>
              Mark signed in
            </button>
            <button className="btn small" type="button" onClick={() => void onOverride(member, "absent")}>
              Mark not signed in
            </button>
            <select
              aria-label={`Link ${member.name} to the sign-in list`}
              value={member.override?.attendee_id ?? ""}
              onChange={(event) => {
                const attendeeId = Number(event.target.value);
                if (attendeeId) void onOverride(member, "attended", attendeeId);
              }}
            >
              <option value="">Link to sign-in list…</option>
              {people.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name} · {person.email}
                </option>
              ))}
            </select>
            {member.override && (
              <button className="btn small ghost" type="button" onClick={() => void onOverride(member, "clear")}>
                Clear override
              </button>
            )}
            {member.override?.updated_by && <span className="muted">by {member.override.updated_by}</span>}
          </div>
        ))}
      </div>
      <div className="inline-form">
        <label>
          Review
          <select aria-label={`Review status for ${project.project_name}`} value={status} onChange={(event) => setStatus(event.target.value as SignInReviewStatus)}>
            {REVIEW_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="span-2">
          Note
          <input aria-label={`Note for ${project.project_name}`} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Organizer note" />
        </label>
        <button className="btn small" type="button" disabled={busy} onClick={() => void saveReview()}>
          Save review
        </button>
      </div>
      <p className="hint">This note does not change scores, rank, or eligibility. Disqualified-note is a review mark only.</p>
      {project.review.updated_by && (
        <p className="muted">
          Last changed by {project.review.updated_by}
          {project.review.updated_at ? ` · ${formatStamp(project.review.updated_at)}` : ""}
        </p>
      )}
    </article>
  );
}
