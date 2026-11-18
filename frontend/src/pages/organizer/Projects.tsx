import { FormEvent, useEffect, useState } from "react";
import { api } from "../../api";
import { StatusPill } from "../../components/StatusPill";
import type { AdminProject, JudgeAccount, Track } from "../../types";

type Draft = {
  id?: number;
  name: string;
  description: string;
  track_ids: string[];
  team_members: string;
  github_url: string;
  demo_url: string;
  submission_url: string;
};

const EMPTY: Draft = {
  name: "",
  description: "",
  track_ids: [],
  team_members: "",
  github_url: "",
  demo_url: "",
  submission_url: "",
};

function needsForce(message: string): boolean {
  return message.toLowerCase().includes("confirm to");
}

export default function Projects() {
  const [projects, setProjects] = useState<AdminProject[]>([]);
  const [judges, setJudges] = useState<JudgeAccount[]>([]);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [trackName, setTrackName] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [assignJudge, setAssignJudge] = useState("");
  const [assignTrack, setAssignTrack] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    const [projectRows, judgeRows, trackRows] = await Promise.all([
      api<AdminProject[]>("/api/organizer/projects"),
      api<JudgeAccount[]>("/api/organizer/judges"),
      api<Track[]>("/api/organizer/tracks"),
    ]);
    setProjects(projectRows);
    setJudges(judgeRows);
    setTracks(trackRows);
  }

  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : "Couldn't load projects"));
  }, []);

  const filtered = projects.filter((project) => project.name.toLowerCase().includes(query.trim().toLowerCase()));
  const current = projects.find((project) => project.id === selected) ?? null;

  function edit(project: AdminProject) {
    setDraft({
      id: project.id,
      name: project.name,
      description: project.description,
      track_ids: project.tracks.map((track) => String(track.id)),
      team_members: project.team_members.join("\n"),
      github_url: project.github_url,
      demo_url: project.demo_url,
      submission_url: project.submission_url,
    });
    setSelected(project.id);
  }

  async function saveProject(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    setError("");
    setNotice("");
    const body = {
      name: draft.name,
      description: draft.description,
      track_ids: draft.track_ids.map(Number),
      team_members: draft.team_members.split("\n").map((line) => line.trim()).filter(Boolean),
      github_url: draft.github_url,
      demo_url: draft.demo_url,
      submission_url: draft.submission_url,
    };
    try {
      const saved = draft.id
        ? await api<AdminProject>(`/api/organizer/projects/${draft.id}`, { method: "PATCH", body: JSON.stringify(body) })
        : await api<AdminProject>("/api/organizer/projects", { method: "POST", body: JSON.stringify(body) });
      setNotice(draft.id ? `Updated ${saved.name}.` : `Created ${saved.name}.`);
      setDraft(null);
      setSelected(saved.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save the project");
    }
  }

  async function addTrack() {
    if (!trackName.trim()) return;
    setError("");
    try {
      const track = await api<Track>("/api/organizer/tracks", {
        method: "POST",
        body: JSON.stringify({ name: trackName, description: "" }),
      });
      setTrackName("");
      setTracks((currentTracks) => [...currentTracks, track].sort((a, b) => a.name.localeCompare(b.name)));
      setDraft((currentDraft) =>
        currentDraft ? { ...currentDraft, track_ids: [...currentDraft.track_ids, String(track.id)] } : currentDraft,
      );
      setNotice(`Added track ${track.name}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add the track");
    }
  }

  async function removeProject(project: AdminProject, force = false) {
    if (!force && !window.confirm(`Delete ${project.name}?`)) return;
    setError("");
    try {
      await api(`/api/organizer/projects/${project.id}?force=${force}`, { method: "DELETE" });
      if (selected === project.id) setSelected(null);
      setNotice(`Deleted ${project.name}.`);
      await load();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Couldn't delete the project";
      if (!force && needsForce(message) && window.confirm(message)) {
        await removeProject(project, true);
        return;
      }
      setError(message);
    }
  }

  async function assign(force = false) {
    if (!current || !assignJudge) return;
    setError("");
    try {
      await api("/api/organizer/assignments", {
        method: "POST",
        body: JSON.stringify({ judge_id: Number(assignJudge), project_id: current.id, track_id: Number(assignTrack), force }),
      });
      setAssignJudge("");
      setNotice("Judge assigned.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't assign that judge");
    }
  }

  async function reassign(assignmentId: number, judgeId: number, force = false) {
    setError("");
    try {
      await api(`/api/organizer/assignments/${assignmentId}/reassign`, {
        method: "POST",
        body: JSON.stringify({ judge_id: judgeId, force }),
      });
      setNotice("Assignment moved.");
      await load();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Couldn't reassign";
      if (!force && needsForce(message) && window.confirm(message)) {
        await reassign(assignmentId, judgeId, true);
        return;
      }
      setError(message);
    }
  }

  async function unassign(assignmentId: number, force = false) {
    setError("");
    try {
      await api(`/api/organizer/assignments/${assignmentId}?force=${force}`, { method: "DELETE" });
      setNotice("Assignment removed.");
      await load();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Couldn't remove the assignment";
      if (!force && needsForce(message) && window.confirm(message)) {
        await unassign(assignmentId, true);
        return;
      }
      setError(message);
    }
  }

  async function importCsv(file: File) {
    setError("");
    setNotice("");
    const body = new FormData();
    body.append("file", file);
    try {
      const result = await api<{
        created: number;
        updated: number;
        tracks: string[];
        skipped: { row: number; message: string }[];
      }>("/api/organizer/projects/import", {
        method: "POST",
        body,
      });
      setNotice(
        `Created ${result.created}, updated ${result.updated}. Tracks: ${result.tracks.join(", ") || "none"}.`,
      );
      if (result.skipped.length) {
        setError(result.skipped.map((item) => `Row ${item.row}: ${item.message}`).join(" "));
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    }
  }

  const available = current
    ? judges.filter((judge) => judge.active && !current.assignments.some((assignment) => assignment.judge_id === judge.id))
    : [];

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Projects</h1>
          <p className="lede">Add projects, import a CSV, and assign judges. Submitted scorecards block deletion.</p>
        </div>
        <div className="toolbar">
          <label className="btn file-label">
            Import CSV
            <input
              className="file-input"
              type="file"
              accept=".csv,text/csv"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void importCsv(file);
                event.target.value = "";
              }}
            />
          </label>
          <button className="btn primary" type="button" onClick={() => setDraft({ ...EMPTY })}>
            Add project
          </button>
        </div>
      </header>
      <p className="hint">CSV columns: name, description, track, team_members, github_url, demo_url, submission_url. Separate teammates with semicolons.</p>
      {error && <p className="banner bad">{error}</p>}
      {notice && <p className="banner ok">{notice}</p>}

      {draft && (
        <form className="panel form-grid" onSubmit={saveProject}>
          <h2>{draft.id ? "Edit project" : "New project"}</h2>
          <label>
            Name
            <input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} required />
          </label>
          <label>
            Prize categories
            <span className="hint">Imported from the submissions file. Change these only to correct a category.</span>
            <div className="stack tight">
              {tracks.map((track) => (
                <label key={track.id} className="check">
                  <input
                    type="checkbox"
                    checked={draft.track_ids.includes(String(track.id))}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        track_ids: event.target.checked
                          ? [...draft.track_ids, String(track.id)]
                          : draft.track_ids.filter((id) => id !== String(track.id)),
                      })
                    }
                  />
                  {track.name}
                </label>
              ))}
            </div>
          </label>
          <label className="span-2">
            Description
            <textarea rows={3} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
          </label>
          <label className="span-2">
            Team, one name per line
            <textarea rows={3} value={draft.team_members} onChange={(event) => setDraft({ ...draft, team_members: event.target.value })} />
          </label>
          <label>
            GitHub
            <input value={draft.github_url} onChange={(event) => setDraft({ ...draft, github_url: event.target.value })} placeholder="https://" />
          </label>
          <label>
            Demo
            <input value={draft.demo_url} onChange={(event) => setDraft({ ...draft, demo_url: event.target.value })} placeholder="https://" />
          </label>
          <label className="span-2">
            Submission
            <input value={draft.submission_url} onChange={(event) => setDraft({ ...draft, submission_url: event.target.value })} placeholder="https://" />
          </label>
          <div className="toolbar">
            <button className="btn primary" type="submit">
              Save project
            </button>
            <button className="btn ghost" type="button" onClick={() => setDraft(null)}>
              Cancel
            </button>
          </div>
          <div className="inline-form">
            <input value={trackName} onChange={(event) => setTrackName(event.target.value)} placeholder="New track name" />
            <button className="btn small" type="button" onClick={() => void addTrack()}>
              Add track
            </button>
          </div>
        </form>
      )}

      <div className="toolbar">
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search projects" aria-label="Search projects" />
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Project</th>
              <th>Track</th>
              <th>Team</th>
              <th>Judges</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((project) => (
              <tr key={project.id} className={selected === project.id ? "selected" : ""}>
                <td>
                  <button className="linkish" type="button" onClick={() => setSelected(project.id)}>
                    {project.name}
                  </button>
                </td>
                <td>{project.tracks.map((track) => track.name).join(", ") || "—"}</td>
                <td>{project.team_members.join(", ")}</td>
                <td>{project.assignments.length}</td>
                <td className="actions">
                  <button className="btn small" type="button" onClick={() => edit(project)}>
                    Edit
                  </button>
                  <button className="btn small danger" type="button" onClick={() => void removeProject(project)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {current && (
        <section className="panel">
          <h2>Assignments for {current.name}</h2>
          <p className="hint">{current.description}</p>
          <ul className="plain">
            {current.assignments.map((assignment) => (
              <li key={assignment.assignment_id} className="assign-row">
                <span>
                  {assignment.judge_name} · {assignment.track_name} <StatusPill status={assignment.status} />
                </span>
                <select
                  aria-label={`Reassign ${assignment.judge_name}`}
                  defaultValue=""
                  onChange={(event) => {
                    const judgeId = Number(event.target.value);
                    if (judgeId) void reassign(assignment.assignment_id, judgeId);
                    event.target.value = "";
                  }}
                >
                  <option value="">Reassign to…</option>
                  {judges
                    .filter((judge) => judge.active && judge.id !== assignment.judge_id && !current.assignments.some((item) => item.judge_id === judge.id))
                    .map((judge) => (
                      <option key={judge.id} value={judge.id}>
                        {judge.name}
                      </option>
                    ))}
                </select>
                <button className="btn small danger" type="button" onClick={() => void unassign(assignment.assignment_id)}>
                  Remove
                </button>
              </li>
            ))}
            {current.assignments.length === 0 && <li className="muted">No judges yet.</li>}
          </ul>
          <div className="inline-form">
            <select aria-label="Track for this assignment" value={assignTrack} onChange={(event) => setAssignTrack(event.target.value)}>
              <option value="">Track</option>
              {current.tracks.map((track) => (
                <option key={track.id} value={track.id}>
                  {track.name}
                </option>
              ))}
            </select>
            <select aria-label="Judge to assign" value={assignJudge} onChange={(event) => setAssignJudge(event.target.value)}>
              <option value="">Assign a judge</option>
              {available.map((judge) => (
                <option key={judge.id} value={judge.id}>
                  {judge.name}
                </option>
              ))}
            </select>
            <button className="btn" type="button" disabled={!assignJudge || !assignTrack} onClick={() => void assign()}>
              Assign
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
