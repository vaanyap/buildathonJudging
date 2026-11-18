import { useEffect, useState } from "react";
import { api } from "../../api";
import { StatusPill } from "../../components/StatusPill";
import type { CrossTrackReport } from "../../types";

export default function CrossTrack() {
  const [report, setReport] = useState<CrossTrackReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stop = false;
    const pull = () => {
      api<CrossTrackReport>("/api/organizer/cross-track")
        .then((data) => {
          if (!stop) setReport(data);
        })
        .catch((err: unknown) => {
          if (!stop) setError(err instanceof Error ? err.message : "Couldn't load flags");
        });
    };
    pull();
    const id = window.setInterval(pull, 10000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);

  if (!report) return <p className="page-loading">Loading cross-track flags…</p>;

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Cross-track review</h1>
          <p className="lede">
            Flags are recommendations from judges. They do not change a project's track, its assignments, or its rank.
          </p>
        </div>
      </header>
      {error && <p className="banner bad">{error}</p>}
      {report.projects.length === 0 && <p className="empty">No judge has flagged a project for another track.</p>}
      <div className="stack">
        {report.projects.map((project) => (
          <article key={project.project_id} className="panel flag-card">
            <div className="panel-head">
              <div>
                <h2>{project.project_name}</h2>
                <p className="muted">Original track: {project.original_track}</p>
              </div>
              <span className="pill status-ready_to_submit">
                {project.flag_count} judge{project.flag_count === 1 ? "" : "s"}
              </span>
            </div>
            <ul className="plain">
              {project.flags.map((flag) => (
                <li key={flag.evaluation_id}>
                  <strong>{flag.judge_name}</strong> recommends <strong>{flag.recommended_track ?? "another track"}</strong>{" "}
                  <StatusPill status={flag.status} />
                  <p>{flag.explanation || "No explanation yet."}</p>
                </li>
              ))}
            </ul>
          </article>
        ))}
      </div>
    </div>
  );
}
