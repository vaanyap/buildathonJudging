import { FormEvent, useEffect, useState } from "react";
import { api } from "../../api";
import { StatusPill } from "../../components/StatusPill";
import type { JudgeAccount } from "../../types";

function needsForce(message: string): boolean {
  return message.toLowerCase().includes("confirm to");
}

export default function Judges() {
  const [judges, setJudges] = useState<JudgeAccount[]>([]);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    setJudges(await api<JudgeAccount[]>("/api/organizer/judges"));
  }

  useEffect(() => {
    load().catch((err: unknown) => setError(err instanceof Error ? err.message : "Couldn't load judges"));
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    try {
      await api("/api/organizer/judges", { method: "POST", body: JSON.stringify({ name, email, password }) });
      setName("");
      setEmail("");
      setPassword("");
      setNotice("Judge account created.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't create the judge");
    }
  }

  async function toggle(judge: JudgeAccount) {
    setError("");
    try {
      await api(`/api/organizer/judges/${judge.id}`, {
        method: "PATCH",
        body: JSON.stringify({ active: !judge.active }),
      });
      setNotice(judge.active ? `${judge.name} can no longer sign in.` : `${judge.name} can sign in again.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't update the judge");
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

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <h1>Judges</h1>
          <p className="lede">Create accounts, deactivate them, and move assignments. Deactivating keeps submitted scores.</p>
        </div>
      </header>
      {error && <p className="banner bad">{error}</p>}
      {notice && <p className="banner ok">{notice}</p>}
      <form className="panel form-grid" onSubmit={create}>
        <h2>New judge</h2>
        <label>
          Name
          <input value={name} onChange={(event) => setName(event.target.value)} required />
        </label>
        <label>
          Email
          <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
        </label>
        <label>
          Password
          <input type="text" value={password} minLength={8} onChange={(event) => setPassword(event.target.value)} required />
        </label>
        <div className="toolbar">
          <button className="btn primary" type="submit">
            Create account
          </button>
        </div>
      </form>
      <div className="stack">
        {judges.map((judge) => (
          <details key={judge.id} className="panel" open={judge.assignments.length > 0 && judge.assignments.length <= 3}>
            <summary>
              <strong>{judge.name}</strong>
              <span className="muted"> {judge.email}</span>
              {!judge.active && <span className="pill">Inactive</span>}
              <span className="muted">
                {" "}
                {judge.submitted_count}/{judge.assignment_count} submitted
              </span>
            </summary>
            <div className="stack tight">
              <button className="btn small" type="button" onClick={() => void toggle(judge)}>
                {judge.active ? "Deactivate" : "Reactivate"}
              </button>
              <ul className="plain">
                {judge.assignments.map((assignment) => (
                  <li key={assignment.assignment_id} className="assign-row">
                    <span>
                      {assignment.project_name} <StatusPill status={assignment.status} />
                    </span>
                    <select
                      aria-label={`Move ${assignment.project_name}`}
                      defaultValue=""
                      onChange={(event) => {
                        const judgeId = Number(event.target.value);
                        if (judgeId) void reassign(assignment.assignment_id, judgeId);
                        event.target.value = "";
                      }}
                    >
                      <option value="">Move to…</option>
                      {judges
                        .filter((candidate) => candidate.active && candidate.id !== judge.id)
                        .map((candidate) => (
                          <option key={candidate.id} value={candidate.id}>
                            {candidate.name}
                          </option>
                        ))}
                    </select>
                  </li>
                ))}
                {judge.assignments.length === 0 && <li className="muted">No assignments.</li>}
              </ul>
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}
