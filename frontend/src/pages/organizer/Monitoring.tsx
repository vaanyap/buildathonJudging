import { useEffect, useState } from "react";
import { api } from "../../api";
import { StatusPill } from "../../components/StatusPill";
import { percent } from "../../format";
import type { Progress } from "../../types";

export default function Monitoring() {
  const [data, setData] = useState<Progress | null>(null);
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stop = false;
    const pull = () => {
      api<Progress>("/api/organizer/progress")
        .then((next) => {
          if (stop) return;
          setData(next);
          setUpdated(new Date());
        })
        .catch((err: unknown) => {
          if (!stop) setError(err instanceof Error ? err.message : "Couldn't load progress");
        });
    };
    pull();
    const id = window.setInterval(pull, 5000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);

  if (!data) {
    return error ? <p className="banner bad">{error}</p> : <p className="page-loading">Loading progress…</p>;
  }
  const rows = data.projects.filter((project) => !onlyOpen || project.submitted_count < project.assigned_count);

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Live progress</h1>
          <p className="lede">
            {data.counts.submitted} of {data.counts.assignments} submitted ({percent(data.counts.completion_rate)}). Drafts and ready scorecards are not complete.
            {updated ? ` Updated ${updated.toLocaleTimeString()}.` : ""}
          </p>
        </div>
        <button className={onlyOpen ? "chip on" : "chip"} type="button" aria-pressed={onlyOpen} onClick={() => setOnlyOpen((value) => !value)}>
          Missing only
        </button>
      </header>
      {error && <p className="banner bad">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Project</th>
              <th>Track</th>
              <th>Submitted</th>
              <th>Judges</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((project) => (
              <tr key={project.project_id}>
                <td>{project.project_name}</td>
                <td>{project.track_name}</td>
                <td>
                  {project.submitted_count}/{project.assigned_count}
                </td>
                <td>
                  <div className="wrap">
                    {project.judges.map((judge) => (
                      <span key={judge.judge_id} className="inline-status">
                        {judge.judge_name} <StatusPill status={judge.status} />
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
