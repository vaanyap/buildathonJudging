import { STATUS_LABEL } from "../format";
import type { EvalStatus } from "../types";

export function StatusPill({ status }: { status: EvalStatus | string }) {
  const label = STATUS_LABEL[status as EvalStatus] ?? status;
  return <span className={`pill status-${status}`}>{label}</span>;
}
