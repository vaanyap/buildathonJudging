import type { EvalStatus } from "./types";

export const STATUS_LABEL: Record<EvalStatus, string> = {
  not_started: "Not started",
  viewed: "Viewed",
  draft_saved: "Draft saved",
  ready_to_submit: "Ready to submit",
  submitted: "Submitted",
};

export const DELAY_LABEL: Record<string, string> = {
  missed: "Missed start",
  overrun: "Running long",
  late_start: "Late start",
  qa_late: "Q&A not started",
};

export function timerStatusLabel(status: string | undefined): string {
  if (status === "running") return "Running";
  if (status === "paused") return "Paused";
  if (status === "time_up") return "Time is up";
  return "Not started";
}

const ET: Intl.DateTimeFormatOptions = { timeZone: "America/New_York" };

export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "Unscheduled";
  return new Intl.DateTimeFormat("en-US", {
    ...ET,
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function formatStamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-US", {
    ...ET,
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(iso));
}

export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

export function formatScore(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(2);
}

export function toEtInput(iso: string | null): string {
  if (!iso) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    ...ET,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

export function percent(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}
