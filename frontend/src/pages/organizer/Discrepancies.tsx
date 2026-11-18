import { useEffect, useState } from "react";
import { api } from "../../api";

type JudgeSide = {
  judge_id: number;
  judge_name: string;
  evaluation_id: number;
  scores: Record<string, number | null>;
  overall: number | null;
  alignment: string | null;
  alignment_explanation: string;
  formal_feedback: string;
  private_notes: string | null;
  private_notes_visible: boolean;
};

type Flag = {
  project_id: number;
  project_name: string;
  track_id: number;
  track_name: string;
  active: boolean;
  status: string;
  reasons: string[];
  divergent_criteria: string[];
  involved_judge_ids: number[];
  review: { status: string; note: string; updated_by: string | null; updated_at: string | null };
  judges: JudgeSide[];
};

type Settings = { overall_spread: number; high_score: number; low_score: number };

export default function Discrepancies() {
  const [rows, setRows] = useState<Flag[]>([]);
  const [open, setOpen] = useState<Flag | null>(null);
  const [settings, setSettings] = useState<Settings>({ overall_spread: 1.5, high_score: 4, low_score: 2 });
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("open");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    const [list, current] = await Promise.all([
      api<{ projects: Flag[] }>("/api/organizer/discrepancies"),
      api<Settings>("/api/organizer/discrepancy-settings"),
    ]);
    setRows(list.projects);
    setSettings(current);
  }

  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : "Couldn't load discrepancies"));
  }, []);

  async function openFlag(row: Flag) {
    setError("");
    try {
      const detail = await api<Flag>(`/api/organizer/discrepancies/${row.project_id}/tracks/${row.track_id}`);
      setOpen(detail);
      setNote(detail.review.note);
      setStatus(detail.review.status);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't open that discrepancy");
    }
  }

  async function saveReview() {
    if (!open) return;
    setError("");
    try {
      const saved = await api<Flag>(`/api/organizer/discrepancies/${open.project_id}/tracks/${open.track_id}`, {
        method: "PATCH",
        body: JSON.stringify({ status, note }),
      });
      setNotice(`Marked ${open.project_name} ${saved.status}.`);
      setOpen(saved.active ? { ...open, ...saved } : null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save the review");
    }
  }

  async function saveSettings() {
    setError("");
    try {
      const saved = await api<Settings>("/api/organizer/discrepancy-settings", {
        method: "PUT",
        body: JSON.stringify({
          overall_spread: Number(settings.overall_spread),
          high_score: Number(settings.high_score),
          low_score: Number(settings.low_score),
        }),
      });
      setSettings(saved);
      setNotice("Saved the discrepancy thresholds.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save the thresholds");
    }
  }

  async function reopen(evaluationId: number) {
    setError("");
    try {
      await api(`/api/organizer/evaluations/${evaluationId}/reopen`, { method: "POST" });
      setNotice("Scorecard reopened. The judge can revise it.");
      if (open) await openFlag(open);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't reopen that scorecard");
    }
  }

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Discrepancies</h1>
          <p className="lede">
            A project is flagged when submitted scores in one track spread by {settings.overall_spread} or more, or one judge scores a criterion {settings.high_score}–5 and another scores it 1–{settings.low_score}.
          </p>
        </div>
      </header>
      {error && <p className="banner bad">{error}</p>}
      {notice && <p className="banner ok">{notice}</p>}
      <section className="panel">
        <h2>Thresholds</h2>
        <div className="schedule-grid">
          <label>
            Overall spread
            <input
              type="number"
              min={0.1}
              max={4}
              step={0.1}
              value={settings.overall_spread}
              onChange={(event) => setSettings({ ...settings, overall_spread: Number(event.target.value) })}
            />
          </label>
          <label>
            High score
            <input
              type="number"
              min={1}
              max={5}
              value={settings.high_score}
              onChange={(event) => setSettings({ ...settings, high_score: Number(event.target.value) })}
            />
          </label>
          <label>
            Low score
            <input
              type="number"
              min={1}
              max={5}
              value={settings.low_score}
              onChange={(event) => setSettings({ ...settings, low_score: Number(event.target.value) })}
            />
          </label>
          <button className="btn" type="button" onClick={() => void saveSettings()}>
            Save thresholds
          </button>
        </div>
      </section>
      {rows.length === 0 && <p className="muted">No open discrepancies.</p>}
      {rows.map((row) => (
        <article key={`${row.project_id}-${row.track_id}`} className="panel">
          <div className="panel-head">
            <div>
              <h2>{row.project_name}</h2>
              <p className="muted">{row.track_name}</p>
              <p>{row.reasons.join(" ")}</p>
            </div>
            <button className="btn" type="button" onClick={() => void openFlag(row)}>
              Review
            </button>
          </div>
        </article>
      ))}
      {open && (
        <section className="panel">
          <h2>
            {open.project_name} · {open.track_name}
          </h2>
          <p className="muted">
            {open.review.updated_by ? `Last changed by ${open.review.updated_by}.` : "No one has changed the status yet."}
          </p>
          <div className="split">
            {open.judges.map((judge) => (
              <article key={judge.judge_id} className="section">
                <h3>{judge.judge_name}</h3>
                <p>
                  Overall {judge.overall == null ? "—" : judge.overall.toFixed(2)} · {judge.alignment ?? "no alignment"}
                </p>
                <ul className="plain">
                  {Object.entries(judge.scores).map(([key, value]) => (
                    <li key={key} className={open.divergent_criteria.includes(key) ? "score-chip" : ""}>
                      <strong>{key}</strong> {value ?? "—"}
                      {open.divergent_criteria.includes(key) ? " · diverges" : ""}
                    </li>
                  ))}
                </ul>
                <p>{judge.alignment_explanation}</p>
                <p>{judge.formal_feedback}</p>
                {judge.private_notes_visible && (
                  <p className="banner warn">
                    <strong>Private notes (visible because of discrepancy). </strong>
                    {judge.private_notes || "None written."}
                  </p>
                )}
                <button className="btn small" type="button" onClick={() => void reopen(judge.evaluation_id)}>
                  Reopen scorecard
                </button>
              </article>
            ))}
          </div>
          <label>
            Status
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="open">Open</option>
              <option value="discussing">Discussing</option>
              <option value="resolved">Resolved</option>
            </select>
          </label>
          <label>
            Resolution note
            <textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} />
          </label>
          <button className="btn primary" type="button" onClick={() => void saveReview()}>
            Save review
          </button>
        </section>
      )}
    </div>
  );
}
