import { useEffect, useState } from "react";
import { api } from "../../api";
import { formatStamp, timerStatusLabel } from "../../format";
import type { JudgeAccount, Track, TrackLive } from "../../types";

type Live = TrackLive & {
  projects: {
    project_id: number;
    project_name: string;
    presentation_order: number | null;
    state: string;
    detail: string;
    presentation_extension_minutes: number;
    qa_extension_minutes: number;
    judges: {
      judge_id: number;
      judge_name: string;
      presentation_status: string;
      qa_status: string;
      presentation_started_at: string | null;
      presentation_ended_at: string | null;
      qa_started_at: string | null;
      qa_ended_at: string | null;
    }[];
  }[];
};

type Defaults = { presentation_minutes: number; qa_minutes: number };

export default function Tracks() {
  const [board, setBoard] = useState<Live[]>([]);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [judges, setJudges] = useState<JudgeAccount[]>([]);
  const [defaults, setDefaults] = useState<Defaults>({ presentation_minutes: 3, qa_minutes: 2 });
  const [pick, setPick] = useState<Record<number, string>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    const [live, trackRows, judgeRows, timerDefaults] = await Promise.all([
      api<{ tracks: Live[] }>("/api/organizer/tracks/live"),
      api<Track[]>("/api/organizer/tracks"),
      api<JudgeAccount[]>("/api/organizer/judges"),
      api<Defaults>("/api/organizer/timer-defaults"),
    ]);
    setBoard(live.tracks);
    setTracks(trackRows);
    setJudges(judgeRows);
    setDefaults(timerDefaults);
  }

  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : "Couldn't load tracks"));
  }, []);

  async function saveDefaults() {
    setError("");
    try {
      const saved = await api<Defaults>("/api/organizer/timer-defaults", {
        method: "PUT",
        body: JSON.stringify({
          presentation_minutes: Number(defaults.presentation_minutes),
          qa_minutes: Number(defaults.qa_minutes),
        }),
      });
      setDefaults(saved);
      setNotice("Saved the default phase lengths. Existing project overrides stay as they are.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save the defaults");
    }
  }

  async function addJudge(trackId: number) {
    const judgeId = Number(pick[trackId]);
    if (!judgeId) return;
    setError("");
    try {
      await api("/api/organizer/track-assignments", {
        method: "POST",
        body: JSON.stringify({ judge_id: judgeId, track_id: trackId }),
      });
      setNotice("Judge assigned to the track.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't assign that judge");
    }
  }

  async function removeJudge(trackId: number, judgeId: number) {
    setError("");
    try {
      await api(`/api/organizer/track-assignments?judge_id=${judgeId}&track_id=${trackId}`, { method: "DELETE" });
      setNotice("Removed the judge from the track.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't remove that judge");
    }
  }

  async function extend(projectId: number, phase: "presentation" | "qa", minutes: number) {
    setError("");
    try {
      await api(`/api/organizer/projects/${projectId}/timer/extend`, {
        method: "POST",
        body: JSON.stringify({ phase, minutes }),
      });
      setNotice(`Added ${minutes} min to ${phase === "qa" ? "Q&A" : "the presentation"}.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't extend the timer");
    }
  }

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Tracks</h1>
          <p className="lede">
            Judges work through a prize category in order. The timer is 3 minutes of presentation, then 2 minutes of Q&A, unless you change it.
          </p>
        </div>
      </header>
      {error && <p className="banner bad">{error}</p>}
      {notice && <p className="banner ok">{notice}</p>}
      <section className="panel">
        <h2>Default phase lengths</h2>
        <div className="schedule-grid">
          <label>
            Presentation minutes
            <input
              type="number"
              min={1}
              max={180}
              value={defaults.presentation_minutes}
              onChange={(event) => setDefaults({ ...defaults, presentation_minutes: Number(event.target.value) })}
            />
          </label>
          <label>
            Q&A minutes
            <input
              type="number"
              min={1}
              max={180}
              value={defaults.qa_minutes}
              onChange={(event) => setDefaults({ ...defaults, qa_minutes: Number(event.target.value) })}
            />
          </label>
          <button className="btn" type="button" onClick={() => void saveDefaults()}>
            Save defaults
          </button>
        </div>
      </section>
      {board.map((track) => (
        <article key={track.track_id} className="panel">
          <div className="panel-head">
            <div>
              <h2>{track.track_name}</h2>
              <p className="muted">
                {track.current_project_name ? `Now: ${track.current_project_name}. ` : ""}
                {track.current_detail}. {track.finished_projects} of {track.project_count} finished both phases.
              </p>
            </div>
          </div>
          <div className="toolbar">
            {track.judges.map((judge) => (
              <span key={judge.judge_id} className="inline-status">
                {judge.judge_name}
                <button className="btn small ghost" type="button" onClick={() => void removeJudge(track.track_id, judge.judge_id)}>
                  Remove
                </button>
              </span>
            ))}
            <select
              aria-label={`Add a judge to ${track.track_name}`}
              value={pick[track.track_id] ?? ""}
              onChange={(event) => setPick({ ...pick, [track.track_id]: event.target.value })}
            >
              <option value="">Add a judge</option>
              {judges
                .filter((judge) => judge.active && !track.judges.some((assigned) => assigned.judge_id === judge.id))
                .map((judge) => (
                  <option key={judge.id} value={judge.id}>
                    {judge.name}
                  </option>
                ))}
            </select>
            <button className="btn small" type="button" onClick={() => void addJudge(track.track_id)}>
              Assign
            </button>
          </div>
          <ul className="plain">
            {track.projects.map((project) => (
              <li key={project.project_id}>
                <strong>
                  {project.presentation_order ? `${project.presentation_order}. ` : ""}
                  {project.project_name}
                </strong>
                <span className="muted"> {project.detail}</span>
                {project.judges.map((judge) => (
                  <span key={judge.judge_id} className="muted">
                    {judge.judge_name}: presentation {timerStatusLabel(judge.presentation_status)} ({formatStamp(judge.presentation_started_at)}–
                    {formatStamp(judge.presentation_ended_at)}) · Q&A {timerStatusLabel(judge.qa_status)} ({formatStamp(judge.qa_started_at)}–
                    {formatStamp(judge.qa_ended_at)})
                  </span>
                ))}
                <div className="toolbar">
                  <button className="btn small" type="button" onClick={() => void extend(project.project_id, "presentation", 1)}>
                    +1 presentation
                  </button>
                  <button className="btn small" type="button" onClick={() => void extend(project.project_id, "qa", 1)}>
                    +1 Q&A
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </article>
      ))}
      {board.length === 0 && tracks.length === 0 && <p className="muted">Import the submissions file to create tracks.</p>}
    </div>
  );
}
