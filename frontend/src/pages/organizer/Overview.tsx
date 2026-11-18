import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { StatusPill } from "../../components/StatusPill";
import { percent } from "../../format";
import type { Criterion, Overview } from "../../types";

export default function Overview() {
  const [data, setData] = useState<Overview | null>(null);
  const [criteria, setCriteria] = useState<Criterion[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const criteriaDirty = useRef(new Set<number>());

  async function load() {
    const overview = await api<Overview>("/api/organizer/overview");
    setData(overview);
    setCriteria((current) => {
      if (criteriaDirty.current.size === 0) return overview.criteria;
      const edited = new Map(current.map((item) => [item.id, item]));
      return overview.criteria.map((item) => (criteriaDirty.current.has(item.id) ? edited.get(item.id) ?? item : item));
    });
  }

  useEffect(() => {
    let stop = false;
    const pull = () => {
      load().catch((err: unknown) => {
        if (!stop) setError(err instanceof Error ? err.message : "Couldn't load overview");
      });
    };
    pull();
    const id = window.setInterval(pull, 8000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, []);

  async function saveCriterion(criterion: Criterion) {
    setError("");
    setNotice("");
    try {
      await api(`/api/organizer/criteria/${criterion.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: criterion.name,
          description: criterion.description,
          min_score: criterion.min_score,
          max_score: criterion.max_score,
        }),
      });
      criteriaDirty.current.delete(criterion.id);
      setNotice(`Saved “${criterion.name}”.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save the rubric");
    }
  }

  function editCriterion(index: number, patch: Partial<Criterion>) {
    setCriteria((current) =>
      current.map((item, itemIndex) => {
        if (itemIndex !== index) return item;
        criteriaDirty.current.add(item.id);
        return { ...item, ...patch };
      }),
    );
  }

  if (!data) {
    return error ? <p className="banner bad">{error}</p> : <p className="page-loading">Loading overview…</p>;
  }
  const counts = data.counts;

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Overview</h1>
          <p className="lede">Drafts are visible here and are not counted as complete.</p>
        </div>
      </header>
      {error && <p className="banner bad">{error}</p>}
      {notice && <p className="banner ok">{notice}</p>}
      <section className="stats">
        <article className="stat">
          <span>Submitted</span>
          <strong>
            {counts.submitted}/{counts.assignments}
          </strong>
          <em>{percent(counts.completion_rate)} complete</em>
        </article>
        <article className="stat">
          <span>Ready</span>
          <strong>{counts.ready_to_submit}</strong>
          <em>Not submitted yet</em>
        </article>
        <article className="stat">
          <span>Drafts</span>
          <strong>{counts.draft_saved}</strong>
          <em>Not complete</em>
        </article>
        <article className="stat">
          <span>Not started</span>
          <strong>{counts.not_started + counts.viewed}</strong>
          <em>
            {counts.viewed} viewed · {counts.projects} projects · {counts.judges} judges
          </em>
        </article>
      </section>

      <section className="split">
        <div className="panel">
          <div className="panel-head">
            <h2>Delays</h2>
            <Link to="/organizer/tracks">Tracks</Link>
          </div>
          {data.tracks.length === 0 && <p className="muted">No tracks yet.</p>}
          <ul className="plain">
            {data.tracks.map((track) => (
              <li key={track.track_id}>
                <strong>{track.track_name}</strong>
                <span className="muted">
                  {" "}
                  {track.finished_projects}/{track.project_count} finished
                </span>
                <p className="muted">
                  {track.current_project_name ? `${track.current_project_name} · ${track.current_detail}` : track.current_detail}
                </p>
              </li>
            ))}
          </ul>
        </div>
        <div className="panel">
          <div className="panel-head">
            <h2>Missing submissions</h2>
            <Link to="/organizer/progress">Live progress</Link>
          </div>
          {data.missing.length === 0 && <p className="muted">Every assignment is submitted.</p>}
          <ul className="plain">
            {data.missing.slice(0, 8).map((project) => (
              <li key={project.project_id}>
                <strong>{project.project_name}</strong>
                <span className="muted"> {project.track_name}</span>
                <div className="wrap">
                  {project.outstanding.map((judge) => (
                    <span key={judge.judge_id} className="inline-status">
                      {judge.judge_name} <StatusPill status={judge.status} />
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="panel">
        <h2>Rubric</h2>
        <p className="hint">Quality scores use this 1–5 scale. Track alignment is collected separately and is not one of these criteria.</p>
        <div className="stack">
          {criteria.map((criterion, index) => (
            <div key={criterion.id} className="rubric-row">
              <label>
                Name
                <input value={criterion.name} onChange={(event) => editCriterion(index, { name: event.target.value })} />
              </label>
              <label>
                Description
                <input
                  value={criterion.description}
                  onChange={(event) => editCriterion(index, { description: event.target.value })}
                />
              </label>
              <label>
                Min
                <input
                  type="number"
                  min={1}
                  max={5}
                  value={criterion.min_score}
                  onChange={(event) => editCriterion(index, { min_score: Number(event.target.value) })}
                />
              </label>
              <label>
                Max
                <input
                  type="number"
                  min={1}
                  max={5}
                  value={criterion.max_score}
                  onChange={(event) => editCriterion(index, { max_score: Number(event.target.value) })}
                />
              </label>
              <button className="btn small" type="button" onClick={() => void saveCriterion(criterion)}>
                Save
              </button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
