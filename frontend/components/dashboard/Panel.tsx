import type { ReactNode } from "react";

// Final Intelligence phase: the established "border border-cs-border
// bg-cs-panel p-5" + mono uppercase micro-label header convention, already
// used ad-hoc throughout LiveMonitor.tsx/IncidentTimeline.tsx, factored out
// for the new report/copilot components so it stays consistent without
// re-typing the same className string in every new file.
export default function Panel({
  label,
  action,
  children,
}: {
  label: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="border border-cs-border bg-cs-panel p-5">
      <div className="mb-4 flex items-center justify-between">
        <p className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">{label}</p>
        {action}
      </div>
      {children}
    </div>
  );
}
