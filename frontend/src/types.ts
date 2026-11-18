export type Role = "judge" | "organizer";

export type User = {
  id: number;
  email: string;
  name: string;
  role: Role;
};

export type EvalStatus = "not_started" | "viewed" | "draft_saved" | "ready_to_submit" | "submitted";
export type Alignment = "strong" | "partial" | "weak";

export type TimerState = {
  status: "idle" | "running" | "paused" | "time_up";
  elapsed_seconds: number;
  remaining_seconds: number;
  allowed_seconds: number;
  warning: boolean;
  time_up: boolean;
  actual_started_at: string | null;
  actual_ended_at: string | null;
  scheduled_duration_minutes: number;
  extension_minutes: number;
  starts_at: string | null;
  location: string;
};

export type ScoreValue = {
  key: string;
  name: string;
  description: string;
  min_score: number;
  max_score: number;
  value: number | null;
};

export type AuditEvent = {
  id: number;
  action: string;
  actor_name: string | null;
  changes: Record<string, unknown>;
  created_at: string | null;
};

export type Evaluation = {
  id: number;
  project_id: number;
  judge_id: number;
  status: EvalStatus;
  private_notes?: string;
  formal_feedback: string;
  alignment: Alignment | null;
  alignment_explanation: string;
  cross_track_flag: boolean;
  recommended_track_id: number | null;
  recommended_track_name: string | null;
  cross_track_explanation: string;
  scores: ScoreValue[];
  submitted_at: string | null;
  updated_at: string | null;
  submission_count: number;
  revision: number;
  reopened: boolean;
  judge_name?: string;
  overall?: number | null;
  audits?: AuditEvent[];
};

export type Criterion = {
  id: number;
  key: string;
  name: string;
  description: string;
  min_score: number;
  max_score: number;
  sort_order?: number;
  required: boolean;
};

export type Track = { id: number; name: string; description?: string };

export type ProjectFields = {
  id: number;
  name: string;
  description: string;
  track_id: number;
  track_name: string;
  team_members: string[];
  github_url: string;
  demo_url: string;
  submission_url: string;
  starts_at: string | null;
  duration_minutes: number | null;
  extension_minutes: number;
  location: string;
};

export type JudgeCard = ProjectFields & {
  status: EvalStatus;
  timer: TimerState;
};

export type ProjectDetail = {
  project: ProjectFields;
  timer: TimerState;
  evaluation: Evaluation;
  criteria: Criterion[];
  tracks: Track[];
};

export type AssignmentSummary = {
  assignment_id: number;
  judge_id: number;
  judge_name: string;
  status: EvalStatus;
};

export type AdminProject = ProjectFields & { assignments: AssignmentSummary[] };

export type JudgeAccount = {
  id: number;
  name: string;
  email: string;
  active: boolean;
  assignment_count: number;
  submitted_count: number;
  assignments: {
    assignment_id: number;
    project_id: number;
    project_name: string;
    status: EvalStatus;
  }[];
};

export type JudgeProgress = {
  judge_id: number;
  judge_name: string;
  status: EvalStatus;
};

export type Progress = {
  counts: {
    projects: number;
    assignments: number;
    submitted: number;
    ready_to_submit: number;
    draft_saved: number;
    viewed: number;
    not_started: number;
    completion_rate: number;
  };
  projects: {
    project_id: number;
    project_name: string;
    track_name: string;
    assigned_count: number;
    submitted_count: number;
    judges: JudgeProgress[];
  }[];
};

export type Delay = {
  project_id: number;
  project_name: string;
  track_name: string;
  location: string;
  starts_at: string;
  kind: "missed" | "overrun" | "late_start";
  detail: string;
};

export type Overview = {
  counts: Progress["counts"] & { judges: number };
  delays: Delay[];
  missing: {
    project_id: number;
    project_name: string;
    track_name: string;
    outstanding: JudgeProgress[];
  }[];
  criteria: Criterion[];
};

export type ResultJudge = {
  assignment_id: number;
  judge_id: number;
  judge_name: string;
  status: EvalStatus;
  evaluation_id: number | null;
};

export type ResultProject = {
  project_id: number;
  project_name: string;
  track_id: number;
  track_name: string;
  description: string;
  team_members: string[];
  assigned_count: number;
  submitted_count: number;
  overall: number | null;
  rank: number | null;
  criterion_averages: Record<string, number | null>;
  alignment_counts: { strong: number; partial: number; weak: number };
  discrepancy: boolean;
  discrepancy_reasons: string[];
  judges: ResultJudge[];
  evaluations: Evaluation[];
};

export type ResultsReport = { criteria: Criterion[]; projects: ResultProject[] };

export type ScheduleRow = ProjectFields & {
  delay: Delay | null;
  judges: {
    judge_id: number;
    judge_name: string;
    timer_status: string;
    actual_started_at: string | null;
    actual_ended_at: string | null;
  }[];
};

export type CrossTrackReport = {
  projects: {
    project_id: number;
    project_name: string;
    original_track: string;
    flag_count: number;
    flags: {
      evaluation_id: number;
      judge_id: number;
      judge_name: string;
      recommended_track: string | null;
      explanation: string;
      status: EvalStatus;
      submitted: boolean;
    }[];
  }[];
};

export type EvalForm = {
  scores: Record<string, number | null>;
  private_notes: string;
  formal_feedback: string;
  alignment: Alignment | null;
  alignment_explanation: string;
  cross_track_flag: boolean;
  recommended_track_id: number | null;
  cross_track_explanation: string;
};
