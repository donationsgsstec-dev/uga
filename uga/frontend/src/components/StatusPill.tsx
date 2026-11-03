import type { AppStatus } from "../types";

export default function StatusPill({ status, stage }: { status: AppStatus; stage?: string }) {
  return (
    <span className={`pill ${status}`} title={stage}>
      <span className="dot" />
      {status}
    </span>
  );
}
