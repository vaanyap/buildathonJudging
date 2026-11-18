import { useEffect, useState } from "react";
import { api } from "../../api";
import { DELAY_LABEL, formatStamp, formatWhen, toEtInput } from "../../format";
import type { ScheduleRow } from "../../types";

type Editor = { starts_at: string; duration_minutes: number; location: string };

export default function Schedule() {
  const [rows, setRows] = useState<ScheduleRow[]>([]);
  const [editors, setEditors] = useState<Record<number, Editor>>({});
  const [judgeFilter, setJudgeFilter] = useState("all");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    const data = await api<{ projects: ScheduleRow[] }>("/api/organizer/schedule");
    setRows(data.projects);
    setEditors((current) => {
      const next = { ...current };
      for (const project of data.projects) {
        if (next[project.id]) continue;
        next[project.id] = {
          starts_at: toEtInput(project.starts_at),
          duration_minutes: project.duration_minutes ?? 5,
          location: project.location,
        };
      }
      return next;
    });
  }

  useEffect(() => {
    let stop = false;
    const pull = () => {
      load().catch((err: unknown) => {
        if (!stop) setError(err instanceof Error ? err.message : "Couldn't load the schedule");
      });
    };
    pull();
    const id = window.setInterval(pull, 10000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);

  const judges = Array.from(new Set(rows.flatMap((row) => row.judges.map((judge) => judge.judge_name)))).sort();
  const visible = rows.filter((row) => judgeFilter === "all" || row.judges.some((judge) => judge.judge_name === judgeFilter));

  async function save(project: ScheduleRow) {
    const editor = editors[project.id];
    if (!editor?.starts_at) {
      setError("Choose a start time.");
      return;
    }
    setError("");
    try {
      await api(`/api/organizer/schedule/${project.id}`, {
        method: "PUT",
        body: JSON.stringify({
          starts_at: editor.starts_at,
          duration_minutes: Number(editor.duration_minutes),
          location: editor.location,
        }),
      });
      setNotice(`Saved the slot for ${project.name}.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save the slot");
    }
  }

  async function extend(projectId: number, minutes: number) {
    setError("");
    try {
      await api(`/api/organizer/schedule/${projectId}/extend`, {
        method: "POST",
        body: JSON.stringify({ minutes }),
      });
      setNotice(`Added ${minutes} minute${minutes === 1 ? "" : "s"}.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't extend the slot");
    }
  }

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Schedule</h1>
          <p className="lede">Times are America/New_York. Extensions add minutes to the running presentation without changing the score.</p>
        </div>
        <label>
          Judge
          <select value={judgeFilter} onChange={(event) => setJudgeFilter(event.target.value)}>
            <option value="all">All judges</option>
            {judges.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </header>
      {error && <p className="banner bad">{error}</p>}
      {notice && <p className="banner ok">{notice}</p>}
      <div className="stack">
        {visible.map((project) => {
          const editor = editors[project.id];
          return (
            <article key={project.id} className="panel">
              <div className="panel-head">
                <div>
                  <h2>{project.name}</h2>
                  <p className="muted">
                    {project.track_name} · {formatWhen(project.starts_at)}
                    {project.extension_minutes > 0 ? ` · +${project.extension_minutes} min extension` : ""}
                  </p>
                </div>
                {project.delay && <span className={`pill delay-${project.delay.kind}`}>{DELAY_LABEL[project.delay.kind]}</span>}
              </div>
              {editor && (
                <div className="schedule-grid">
                  <label>
                    Start
                    <input
                      type="datetime-local"
                      value={editor.starts_at}
                      onChange={(event) => setEditors({ ...editors, [project.id]: { ...editor, starts_at: event.target.value } })}
                    />
                  </label>
                  <label>
                    Minutes
                    <input
                      type="number"
                      min={1}
                      max={180}
                      value={editor.duration_minutes}
                      onChange={(event) =>
                        setEditors({ ...editors, [project.id]: { ...editor, duration_minutes: Number(event.target.value) } })
                      }
                    />
                  </label>
                  <label>
                    Room
                    <input
                      value={editor.location}
                      onChange={(event) => setEditors({ ...editors, [project.id]: { ...editor, location: event.target.value } })}
                    />
                  </label>
                  <button className="btn" type="button" onClick={() => void save(project)}>
                    Save slot
                  </button>
                </div>
              )}
              <div className="toolbar">
                <button className="btn small" type="button" onClick={() => void extend(project.id, 1)}>
                  +1 min
                </button>
                <button className="btn small" type="button" onClick={() => void extend(project.id, 2)}>
                  +2 min
                </button>
                <button className="btn small" type="button" onClick={() => void extend(project.id, 5)}>
                  +5 min
                </button>
              </div>
              <ul className="plain">
                {project.judges.map((judge) => (
                  <li key={judge.judge_id}>
                    <strong>{judge.judge_name}</strong>
                    <span className="muted">
                      {" "}
                      timer {judge.timer_status} · start {formatStamp(judge.actual_started_at)} · end {formatStamp(judge.actual_ended_at)}
                    </span>
                  </li>
                ))}
              </ul>
            </article>
          );
        })}
      </div>
    </div>
  );
}
