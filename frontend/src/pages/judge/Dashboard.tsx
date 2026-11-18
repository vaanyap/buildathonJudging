import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { useAuth } from "../../auth";
import { StatusPill } from "../../components/StatusPill";
import { formatClock, formatWhen } from "../../format";
import type { JudgeCard } from "../../types";

type Filter = "all" | "upcoming" | "open" | "submitted";

function isUpcoming(project: JudgeCard, now: number): boolean {
  if (project.status === "submitted") return false;
  if (!project.starts_at) return true;
  const start = new Date(project.starts_at).getTime();
  return start >= now - 15 * 60 * 1000;
}

export default function JudgeDashboard() {
  const { user, logout } = useAuth();
  const [projects, setProjects] = useState<JudgeCard[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let cancelled = false;
    api<{ projects: JudgeCard[] }>("/api/judge/dashboard")
      .then((data) => {
        if (!cancelled) setProjects(data.projects);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load projects");
      });
    const clock = window.setInterval(() => setNow(Date.now()), 30000);
    return () => {
      cancelled = true;
      window.clearInterval(clock);
    };
  }, []);

  const visible = projects.filter((project) => {
    if (filter === "submitted") return project.status === "submitted";
    if (filter === "open") return project.status !== "submitted";
    if (filter === "upcoming") return isUpcoming(project, now);
    return true;
  });
  const next =
    projects.find((project) => project.status !== "submitted" && isUpcoming(project, now)) ??
    projects.find((project) => project.status !== "submitted");

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
        <Link className="next-card" to={`/judge/projects/${next.id}`}>
          <span className="eyebrow">Up next</span>
          <strong>{next.name}</strong>
          <span>
            {formatWhen(next.starts_at)}
            {next.location ? ` · ${next.location}` : ""}
          </span>
        </Link>
      )}
      <div className="filters" role="tablist">
        {(
          [
            ["all", "All"],
            ["upcoming", "Up next"],
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
        {visible.map((project) => (
          <Link key={project.id} className="card card-link" to={`/judge/projects/${project.id}`}>
            <div className="meta-row">
              <span className="time">{formatWhen(project.starts_at)}</span>
              <StatusPill status={project.status} />
            </div>
            <h2>{project.name}</h2>
            <p className="track-line">{project.track_name}</p>
            <p className="muted">{project.team_members.join(", ") || "Team not listed"}</p>
            <p className="muted">
              {project.location || "Room TBA"}
              {project.timer.status === "running" ? ` · ${formatClock(project.timer.remaining_seconds)} left` : ""}
              {project.timer.time_up ? " · Time is up" : ""}
            </p>
          </Link>
        ))}
        {!error && visible.length === 0 && <p className="empty">Nothing in this filter.</p>}
      </div>
    </div>
  );
}
