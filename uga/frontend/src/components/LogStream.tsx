import { useEffect, useRef } from "react";
import type { PipelineEvent } from "../types";

const LABEL: Record<string, string> = {
  stage: "›",
  action: "run",
  observation: "out",
  message: "msg",
  info: "info",
  success: "ok",
  error: "err",
  done: "done",
};

function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function LogStream({ events }: { events: PipelineEvent[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [events]);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  return (
    <div className="terminal" ref={ref} onScroll={onScroll}>
      {events.length === 0 && <div className="line observation">waiting for activity…</div>}
      {events.map((event, index) => (
        <div className="line" key={index}>
          <span className="t">{clock(event.ts)}</span>
          <span className={event.kind}>
            <span className="t">{LABEL[event.kind] ?? event.kind}</span>{" "}
            {event.kind === "action" || event.kind === "observation" ? (
              <>
                <b>{event.tool}</b> {event.text}
                {event.detail ? `\n${event.detail}` : ""}
              </>
            ) : (
              event.text
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
