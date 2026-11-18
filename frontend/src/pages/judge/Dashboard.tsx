import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { useAuth } from "../../auth";
import { StatusPill } from "../../components/StatusPill";
import { formatClock } from "../../format";
import type { JudgeCard, TimerState } from "../../types";

type Filter = "all" | "open" | "submitted";

function timerLine(timer: TimerState): string {
  const phase = timer.phases[timer.current_phase];
  if (phase.status === "running") return `${phase.label} · ${formatClock(phase.remaining_seconds)} left`;
  if (phase.status === "paused") return `${phase.label} · paused`;
  if (phase.time_up) return `${phase.label} · time is up`;
  if (timer.current_phase === "qa" && timer.phases.presentation.time_up) return "Q&A not started";
  return "";
}

export default function JudgeDashboard() {
  const { user, logout } = useAuth();
  const [projects, setProjects] = useState<JudgeCard[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api<{ projects: JudgeCard[] }>("/api/judge/dashboard")
      .then((data) => {
        if (!cancelled) setProjects(data.projects);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load projects");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const visible = projects.filter((project) => {
    if (filter === "submitted") return project.status === "submitted";
    if (filter === "open") return project.status !== "submitted";
    return true;
  });
  const next = projects.find((project) => project.status !== "submitted");

  return (
    <div className="judge-shell">
      <header className="topbar">
        <div>
          <p className="wordmark">Tech@NYU</p>
          <h1>Your projects</h1>
        </div>
        <div className="user-chip">
          <span>{user?.name}</span>
          <button className="btn ghost small" type="button" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>
      <p className="lede">
        Open any project. Order is a suggestion, not a sequence. Only private notes stay hidden from organizers. A draft is not a submission.
      </p>
      {error && <p className="banner bad">{error}</p>}
      {next && (
        <Link className="next-card" to={`/judge/projects/${next.id}/tracks/${next.track_id}`}>
          <span className="eyebrow">Up next</span>
          <strong>{next.name}</strong>
          <span>
            {next.track_name}
            {next.presentation_order ? ` · ${next.presentation_order}` : ""}
          </span>
        </Link>
      )}
      <div className="filters" role="tablist">
        {(
          [
            ["all", "All"],
            ["open", "Needs scoring"],
            ["submitted", "Submitted"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={filter === key ? "chip on" : "chip"}
            aria-pressed={filter === key}
            onClick={() => setFilter(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="stack">
        {visible.map((project) => {
          const line = timerLine(project.timer);
          return (
            <Link key={`${project.id}-${project.track_id}`} className="card card-link" to={`/judge/projects/${project.id}/tracks/${project.track_id}`}>
              <div className="meta-row">
                <span className="time">{project.presentation_order ? `#${project.presentation_order}` : "Unordered"}</span>
                <StatusPill status={project.status} />
              </div>
              <h2>{project.name}</h2>
              <p className="track-line">{project.track_name}</p>
              <p className="muted">{project.team_members.join(", ") || "Team not listed"}</p>
              {project.other_tracks.length > 0 && (
                <p className="muted">Also score for {project.other_tracks.map((track) => track.track_name).join(", ")}</p>
              )}
              {line && <p className="muted">{line}</p>}
            </Link>
          );
        })}
        {!error && visible.length === 0 && <p className="empty">Nothing in this filter.</p>}
      </div>
    </div>
  );
}
