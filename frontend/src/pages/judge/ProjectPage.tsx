import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../../api";
import { useAuth } from "../../auth";
import { StatusPill } from "../../components/StatusPill";
import { formatClock, formatStamp, timerStatusLabel } from "../../format";
import type { Alignment, Criterion, EvalForm, EvalStatus, Evaluation, ProjectDetail, TimerPhase, TimerPhaseKey, TimerState } from "../../types";

function formFrom(evaluation: Evaluation): EvalForm {
  const scores: Record<string, number | null> = {};
  for (const score of evaluation.scores) scores[score.key] = score.value;
  return {
    scores,
    private_notes: evaluation.private_notes ?? "",
    formal_feedback: evaluation.formal_feedback,
    alignment: evaluation.alignment,
    alignment_explanation: evaluation.alignment_explanation,
    cross_track_flag: evaluation.cross_track_flag,
    recommended_track_id: evaluation.recommended_track_id,
    cross_track_explanation: evaluation.cross_track_explanation,
  };
}

function missingFields(form: EvalForm, criteria: Criterion[]): string[] {
  const missing: string[] = [];
  for (const criterion of criteria) {
    if (criterion.required && form.scores[criterion.key] == null) missing.push(criterion.name);
  }
  if (!form.alignment) missing.push("Track alignment");
  else if (!form.alignment_explanation.trim()) missing.push("Alignment explanation");
  if (form.cross_track_flag) {
    if (!form.recommended_track_id) missing.push("Recommended track");
    if (!form.cross_track_explanation.trim()) missing.push("Cross-track explanation");
  }
  return missing;
}

export default function ProjectPage() {
  const { projectId, trackId } = useParams();
  const { user, logout } = useAuth();
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [form, setForm] = useState<EvalForm | null>(null);
  const [status, setStatus] = useState<EvalStatus>("not_started");
  const [reopened, setReopened] = useState(false);
  const [timer, setTimer] = useState<TimerState | null>(null);
  const [shownPhase, setShownPhase] = useState<TimerPhaseKey>("presentation");
  const [syncedAt, setSyncedAt] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const [saveState, setSaveState] = useState<"idle" | "pending" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const saveTimer = useRef<number | null>(null);
  const formRef = useRef<EvalForm | null>(null);
  const revisionRef = useRef(1);
  const saveLock = useRef<Promise<void>>(Promise.resolve());
  const saving = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoadError("");
    api<ProjectDetail>(`/api/judge/projects/${projectId}/tracks/${trackId}`)
      .then(async (data) => {
        if (cancelled) return;
        setDetail(data);
        setForm(formFrom(data.evaluation));
        formRef.current = formFrom(data.evaluation);
        revisionRef.current = data.evaluation.revision;
        setStatus(data.evaluation.status);
        setReopened(data.evaluation.reopened);
        setTimer(data.timer);
        serverPhase.current = data.timer.current_phase;
        setShownPhase(data.timer.current_phase);
        setSyncedAt(Date.now());
        if (data.evaluation.status === "not_started") {
          const viewed = await api<Evaluation>(`/api/judge/projects/${projectId}/tracks/${trackId}/view`, { method: "POST" });
          if (!cancelled) setStatus(viewed.status);
        } else if (data.evaluation.status !== "viewed") {
          setSaveState("saved");
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "Couldn't open this project");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, trackId]);

  useEffect(() => {
    const clock = window.setInterval(() => setNow(Date.now()), 250);
    const poll = window.setInterval(() => {
      void api<TimerState>(`/api/judge/projects/${projectId}/timer`)
        .then((next) => {
          if (serverPhase.current !== next.current_phase) {
            serverPhase.current = next.current_phase;
            setShownPhase(next.current_phase);
          }
          setTimer(next);
          setSyncedAt(Date.now());
        })
        .catch(() => undefined);
    }, 1000);
    return () => {
      window.clearInterval(clock);
      window.clearInterval(poll);
    };
  }, [projectId]);

  useEffect(() => {
    const flush = () => {
      if (!formRef.current || status === "submitted" || saveTimer.current == null || saving.current) return;
      window.clearTimeout(saveTimer.current);
      saveTimer.current = null;
      const token = localStorage.getItem("judging_token");
      void fetch(`/api/judge/projects/${projectId}/tracks/${trackId}/evaluation`, {
        method: "PUT",
        keepalive: true,
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ ...formRef.current, revision: revisionRef.current }),
      });
    };
    window.addEventListener("pagehide", flush);
    return () => window.removeEventListener("pagehide", flush);
  }, [projectId, trackId, status]);

  useEffect(() => {
    if (status !== "submitted") return;
    const poll = window.setInterval(() => {
      void api<Evaluation>(`/api/judge/projects/${projectId}/tracks/${trackId}/evaluation`)
        .then((evaluation) => {
          if (evaluation.status === "submitted") return;
          revisionRef.current = evaluation.revision;
          setStatus(evaluation.status);
          setReopened(evaluation.reopened);
          const next = formFrom(evaluation);
          formRef.current = next;
          setForm(next);
          setSaveState("saved");
        })
        .catch(() => undefined);
    }, 8000);
    return () => window.clearInterval(poll);
  }, [projectId, status]);

  function persist(): Promise<Evaluation | null> {
    const run = saveLock.current.then(async () => {
      const body = formRef.current;
      if (!body) return null;
      saving.current = true;
      setSaveState("saving");
      try {
        const saved = await api<Evaluation>(`/api/judge/projects/${projectId}/evaluation`, {
          method: "PUT",
          body: JSON.stringify({ ...body, revision: revisionRef.current }),
        });
        revisionRef.current = saved.revision;
        if (formRef.current === body) {
          setStatus(saved.status);
          setReopened(saved.reopened);
          setSaveState("saved");
          setError("");
        }
        return saved;
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          const fresh = await api<Evaluation>(`/api/judge/projects/${projectId}/evaluation`).catch(() => null);
          if (fresh) {
            revisionRef.current = fresh.revision;
            const next = formFrom(fresh);
            formRef.current = next;
            setForm(next);
            setStatus(fresh.status);
            setReopened(fresh.reopened);
          }
        }
        setSaveState("error");
        setError(err instanceof Error ? err.message : "Couldn't save");
        return null;
      } finally {
        saving.current = false;
      }
    });
    saveLock.current = run.then(
      () => undefined,
      () => undefined,
    );
    return run;
  }

  function update(next: EvalForm, immediate: boolean) {
    formRef.current = next;
    setForm(next);
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    if (immediate) {
      void persist();
      return;
    }
    setSaveState("pending");
    saveTimer.current = window.setTimeout(() => void persist(), 450);
  }

  async function onTimer(phase: TimerPhaseKey, action: "start" | "pause" | "reset") {
    const label = phase === "qa" ? "Q&A" : "presentation";
    if (action === "reset" && !window.confirm(`Reset the ${label} timer?`)) return;
    setError("");
    try {
      const next = await api<TimerState>(`/api/judge/projects/${projectId}/timer/${phase}/${action}`, { method: "POST" });
      serverPhase.current = next.current_phase;
      setTimer(next);
      setShownPhase(next.current_phase);
      setSyncedAt(Date.now());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Timer update failed");
      const fresh = await api<TimerState>(`/api/judge/projects/${projectId}/timer`).catch(() => null);
      if (fresh) {
        serverPhase.current = fresh.current_phase;
        setTimer(fresh);
        setShownPhase(fresh.current_phase);
        setSyncedAt(Date.now());
      }
    }
  }

  async function submit() {
    if (!formRef.current) return;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    const saved = await persist();
    if (!saved || saved.status !== "ready_to_submit") {
      setError("Finish the required scores and track alignment before submitting.");
      return;
    }
    if (!window.confirm("Submit this evaluation? You can edit again only if an organizer reopens it.")) return;
    try {
      const submitted = await api<Evaluation>(`/api/judge/projects/${projectId}/tracks/${trackId}/evaluation/submit`, { method: "POST" });
      setStatus(submitted.status);
      setReopened(submitted.reopened);
      setSaveState("saved");
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submit failed");
    }
  }

  if (loadError) {
    return (
      <div className="judge-shell">
        <p className="banner bad">{loadError}</p>
        <Link to="/judge">Back to projects</Link>
      </div>
    );
  }
  if (!detail || !form || !timer) return <p className="page-loading">Loading project…</p>;

  const locked = status === "submitted";
  const adjust = (now - syncedAt) / 1000;
  const presentation = timer.phases.presentation;
  const qa = timer.phases.qa;
  const shown = timer.phases[shownPhase] ?? presentation;

  function liveRemaining(phase: TimerPhase): number {
    if (phase.status !== "running") return phase.remaining_seconds;
    return Math.max(0, phase.remaining_seconds - adjust);
  }

  function phaseTimeUp(phase: TimerPhase, remaining: number): boolean {
    return phase.time_up || (phase.status === "running" && remaining <= 0);
  }

  const presentationRemaining = liveRemaining(presentation);
  const qaRemaining = liveRemaining(qa);
  const shownRemaining = liveRemaining(shown);
  const presentationTimeUp = phaseTimeUp(presentation, presentationRemaining);
  const qaTimeUp = phaseTimeUp(qa, qaRemaining);
  const presentationWarning =
    presentation.status === "running" &&
    presentationRemaining > 0 &&
    presentation.warning_seconds > 0 &&
    presentationRemaining <= presentation.warning_seconds;
  const qaWarning =
    qa.status === "running" && qaRemaining > 0 && qa.warning_seconds > 0 && qaRemaining <= qa.warning_seconds;
  const shownWarning = shown.key === "qa" ? qaWarning : presentationWarning;
  const shownTimeUp = shown.key === "qa" ? qaTimeUp : presentationTimeUp;
  const missing = missingFields(form, detail.criteria);
  const otherTracks = detail.tracks.filter((track) => track.id !== detail.track_id);
  const saveLabel =
    saveState === "saving" || saveState === "pending"
      ? "Saving…"
      : saveState === "saved"
        ? "Saved on the server"
        : saveState === "error"
          ? "Not saved"
          : "Edits save automatically";

  return (
    <div className="judge-shell project-page">
      <header className="topbar">
        <Link className="back" to="/judge">
          Projects
        </Link>
        <div className="user-chip">
          <span>{user?.name}</span>
          <button className="btn ghost small" type="button" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>

      <div className="project-head">
        <div className="meta-row">
          <span className="track-line">{detail.track_name}</span>
          <StatusPill status={status} />
        </div>
        <h1>{detail.project.name}</h1>
        <p className="track-line">Scoring for {detail.track_name}</p>
        {detail.other_tracks.length > 0 && (
          <p className="muted">You also score this project for {detail.other_tracks.map((track) => track.track_name).join(", ")}.</p>
        )}
        <p className="muted">{detail.project.team_members.join(", ") || "Team not listed"}</p>
      </div>

      <section className={`timer ${shownWarning ? "warning" : ""} ${shownTimeUp ? "done" : ""}`} aria-live="polite">
        <div className="phase-tabs" role="tablist" aria-label="Timer phases">
          {([presentation, qa] as const).map((phase) => {
            const remaining = phase.key === "qa" ? qaRemaining : presentationRemaining;
            const current = phase.key === timer.current_phase;
            const mark = !current ? null : phase.key === "qa" && phase.status === "idle" ? "Next" : "Now";
            return (
              <button
                key={phase.key}
                className={`phase-tab ${shown.key === phase.key ? "on" : ""}`}
                type="button"
                role="tab"
                aria-selected={shown.key === phase.key}
                onClick={() => setShownPhase(phase.key)}
              >
                <span className="eyebrow">{mark ?? ""}</span>
                <span className="phase-name">{phase.label}</span>
                <strong className="clock">{formatClock(remaining)}</strong>
                <span className="muted">
                  {phaseTimeUp(phase, remaining) ? "Time is up" : timerStatusLabel(phase.status)}
                </span>
              </button>
            );
          })}
        </div>
        <p className="muted">
          {shown.scheduled_minutes} min scheduled
          {shown.extension_minutes > 0 ? ` + ${shown.extension_minutes} min from organizer` : ""}
          {" · "}
          {shownTimeUp ? "Time is up" : timerStatusLabel(shown.status)}
        </p>
        <p className="muted">
          Actual start {formatStamp(shown.actual_started_at)} · end {formatStamp(shown.actual_ended_at)}
        </p>
        <div className="timer-actions">
          <button
            className="btn primary"
            type="button"
            disabled={shown.status === "running" || shownRemaining <= 0}
            onClick={() => void onTimer(shown.key, "start")}
          >
            {shown.status === "running"
              ? shown.key === "qa"
                ? "Start Q&A"
                : "Start"
              : shown.key === "qa"
                ? shown.status === "idle"
                  ? "Start Q&A"
                  : "Resume Q&A"
                : shown.status === "idle"
                  ? "Start"
                  : "Resume"}
          </button>
          <button className="btn" type="button" disabled={shown.status !== "running"} onClick={() => void onTimer(shown.key, "pause")}>
            Pause
          </button>
          <button className="btn ghost" type="button" onClick={() => void onTimer(shown.key, "reset")}>
            Reset
          </button>
        </div>
        {shown.key === "presentation" && qa.status !== "running" && !qaTimeUp && qaRemaining > 0 && (
          <button className="btn primary qa-start" type="button" onClick={() => void onTimer("qa", "start")}>
            {qa.status === "idle" ? "Start Q&A" : "Resume Q&A"}
          </button>
        )}
      </section>

      {presentationWarning && (
        <p className="banner warn">
          {presentation.warning_seconds >= 60
            ? "One minute left in the presentation."
            : `${presentation.warning_seconds} seconds left in the presentation.`}
        </p>
      )}
      {qaWarning && (
        <p className="banner warn">
          {qa.warning_seconds >= 60 ? "One minute left in Q&A." : `${qa.warning_seconds} seconds left in Q&A.`}
        </p>
      )}
      {presentationTimeUp && qa.status === "idle" && !qaTimeUp && (
        <p className="banner warn">Presentation time is up. Start Q&A when you're ready. Scoring stays open.</p>
      )}
      {qaTimeUp && <p className="banner warn">Q&A time is up. Scoring stays open. An organizer can add time.</p>}
      {reopened && !locked && <p className="banner ok">An organizer reopened this. Submit again when you are done.</p>}
      {locked && <p className="banner ok">Submitted. It stays locked unless an organizer reopens it.</p>}
      {error && <p className="banner bad">{error}</p>}

      <section className="section">
        <h2>Project</h2>
        <p>{detail.project.description}</p>
        <p>
          <strong>Team. </strong>
          {detail.project.team_members.join(", ") || "Not listed"}
        </p>
        <div className="link-row">
          {detail.project.github_url && (
            <a className="btn" href={detail.project.github_url} target="_blank" rel="noreferrer">
              GitHub
            </a>
          )}
          {detail.project.demo_url && (
            <a className="btn" href={detail.project.demo_url} target="_blank" rel="noreferrer">
              Demo
            </a>
          )}
          {detail.project.submission_url && (
            <a className="btn" href={detail.project.submission_url} target="_blank" rel="noreferrer">
              Submission
            </a>
          )}
        </div>
      </section>

      <section className="section">
        <h2>Scores</h2>
        <p className="hint">1 is weakest, 5 is strongest. You can leave scores blank and come back.</p>
        {detail.criteria.map((criterion) => (
          <div key={criterion.key} className="score">
            <div>
              <strong>{criterion.name}</strong>
              <p className="hint">{criterion.description}</p>
            </div>
            <div className="score-row" role="group" aria-label={criterion.name}>
              {Array.from({ length: criterion.max_score - criterion.min_score + 1 }, (_, index) => {
                const value = criterion.min_score + index;
                const selected = form.scores[criterion.key] === value;
                return (
                  <button
                    key={value}
                    type="button"
                    className={selected ? "score-btn on" : "score-btn"}
                    aria-pressed={selected}
                    disabled={locked}
                    onClick={() =>
                      update(
                        {
                          ...form,
                          scores: { ...form.scores, [criterion.key]: selected ? null : value },
                        },
                        true,
                      )
                    }
                  >
                    {value}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </section>

      <section className="section">
        <h2>Track alignment</h2>
        <p className="hint">Alignment is separate from the quality scores. A weak fit does not lower the other scores.</p>
        <div className="segment" role="group" aria-label="Track alignment">
          {(["strong", "partial", "weak"] as Alignment[]).map((value) => (
            <button
              key={value}
              type="button"
              className={form.alignment === value ? "on" : ""}
              disabled={locked}
              aria-pressed={form.alignment === value}
              onClick={() => update({ ...form, alignment: form.alignment === value ? null : value }, true)}
            >
              {value}
            </button>
          ))}
        </div>
        <label>
          Why this fit?
          <textarea
            value={form.alignment_explanation}
            disabled={locked}
            rows={3}
            onChange={(event) => update({ ...form, alignment_explanation: event.target.value }, false)}
          />
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={form.cross_track_flag}
            disabled={locked}
            onChange={(event) => update({ ...form, cross_track_flag: event.target.checked }, true)}
          />
          Flag this project for another track
        </label>
        {form.cross_track_flag && (
          <>
            <label>
              Recommended track
              <select
                value={form.recommended_track_id ?? ""}
                disabled={locked}
                onChange={(event) =>
                  update(
                    { ...form, recommended_track_id: event.target.value ? Number(event.target.value) : null },
                    true,
                  )
                }
              >
                <option value="">Choose a track</option>
                {otherTracks.map((track) => (
                  <option key={track.id} value={track.id}>
                    {track.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Why flag it?
              <textarea
                value={form.cross_track_explanation}
                disabled={locked}
                rows={3}
                onChange={(event) => update({ ...form, cross_track_explanation: event.target.value }, false)}
              />
            </label>
            <p className="hint">This is a recommendation. It does not move the project or change its rank.</p>
          </>
        )}
      </section>

      <section className="section">
        <h2>Notes</h2>
        <label>
          Private notes
          <span className="hint">Only you can see these. They are not exported.</span>
          <textarea
            value={form.private_notes}
            disabled={locked}
            rows={4}
            onChange={(event) => update({ ...form, private_notes: event.target.value }, false)}
          />
        </label>
        <label>
          Formal feedback
          <span className="hint">Included when you submit, and visible to organizers.</span>
          <textarea
            value={form.formal_feedback}
            disabled={locked}
            rows={4}
            onChange={(event) => update({ ...form, formal_feedback: event.target.value }, false)}
          />
        </label>
      </section>

      <div className="submit-bar">
        <p className="save-state" aria-live="polite">
          {saveLabel}
        </p>
        {missing.length > 0 && !locked && <p className="hint">Still needed: {missing.join(", ")}</p>}
        <button className="btn primary" type="button" disabled={locked || missing.length > 0 || saveState === "saving"} onClick={() => void submit()}>
          {locked ? "Submitted" : "Submit evaluation"}
        </button>
      </div>
    </div>
  );
}
