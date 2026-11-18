import { useEffect, useState } from "react";
import { api, downloadCsv } from "../../api";
import { SignInBadge } from "../../components/SignInBadge";
import type { Criterion } from "../../types";

type Comment = {
  judge_name: string;
  formal_feedback: string;
  alignment: string | null;
  alignment_explanation: string;
};

type Winner = {
  project_id: number;
  project_name: string;
  rank: number;
  overall: number | null;
  criterion_averages: Record<string, number | null>;
  discrepancy: boolean;
  discrepancy_status: string | null;
  attendance_flagged: boolean;
  summary: { text: string; strengths: string[]; concerns: string[]; themes: string[] };
  formal_comments: Comment[];
};

type Report = {
  per_track: number;
  criteria: Criterion[];
  tracks: { track_id: number; track_name: string; winners: Winner[] }[];
};

export default function Winners() {
  const [perTrack, setPerTrack] = useState(1);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stop = false;
    api<Report>(`/api/organizer/winners?per_track=${perTrack}`)
      .then((data) => {
        if (!stop) setReport(data);
      })
      .catch((err: unknown) => {
        if (!stop) setError(err instanceof Error ? err.message : "Couldn't load winners");
      });
    return () => {
      stop = true;
    };
  }, [perTrack]);

  async function download(path: string, filename: string) {
    setError("");
    try {
      await downloadCsv(`${path}?per_track=${perTrack}`, filename);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    }
  }

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Winners</h1>
          <p className="lede">Summaries use submitted formal feedback only. Tied ranks are all included.</p>
        </div>
        <div className="toolbar">
          <label>
            Per track
            <select value={perTrack} onChange={(event) => setPerTrack(Number(event.target.value))}>
              {[1, 2, 3].map((count) => (
                <option key={count} value={count}>
                  {count}
                </option>
              ))}
            </select>
          </label>
          <button className="btn" type="button" onClick={() => void download("/api/organizer/winners.csv", "winners.csv")}>
            CSV
          </button>
          <button className="btn" type="button" onClick={() => void download("/api/organizer/winners.md", "winners.md")}>
            Markdown
          </button>
        </div>
      </header>
      {error && <p className="banner bad">{error}</p>}
      {!report && <p className="page-loading">Loading winners…</p>}
      {report?.tracks.map((track) => (
        <section key={track.track_id} className="panel">
          <h2>{track.track_name}</h2>
          {track.winners.length === 0 && <p className="muted">No submitted scores yet.</p>}
          {track.winners.map((winner) => (
            <article key={winner.project_id} className="scorecard">
              <div className="panel-head">
                <h3>
                  {winner.rank}. {winner.project_name}
                </h3>
                {winner.discrepancy && <span className="pill delay-overrun">Discrepancy · {winner.discrepancy_status}</span>}
                {winner.attendance_flagged && <SignInBadge />}
              </div>
              <p>Overall {winner.overall == null ? "—" : winner.overall.toFixed(2)}</p>
              <div className="wrap">
                {report.criteria.map((criterion) => (
                  <span key={criterion.key} className="score-chip">
                    {criterion.name} {winner.criterion_averages[criterion.key]?.toFixed(2) ?? "—"}
                  </span>
                ))}
              </div>
              <p>{winner.summary.text}</p>
              <h3>What judges said</h3>
              {winner.formal_comments.length === 0 && <p className="muted">No formal feedback.</p>}
              {winner.formal_comments.map((comment) => (
                <p key={comment.judge_name}>
                  <strong>{comment.judge_name}</strong> ({comment.alignment ?? "no alignment"}): {comment.formal_feedback || "No formal feedback."}
                </p>
              ))}
            </article>
          ))}
        </section>
      ))}
    </div>
  );
}
