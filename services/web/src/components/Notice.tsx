import type { ReactNode } from "react";

/** A notice (§4): one line on `sheet` with a hairline, the bold label first and the link at the right;
 * at most two lines on a phone. No colour edge: it says what happened in words. */
export function Notice({ label, children, action, role = "note", className }: {
  label: ReactNode;
  children?: ReactNode;
  /** the way on, at the right: "Read the release →" */
  action?: ReactNode;
  role?: "note" | "status" | "alert";
  className?: string;
}) {
  return (
    <div className={className ? `notice ${className}` : "notice"} role={role}>
      <b className="notice-label">{label}</b>
      {children && <span className="notice-text">{children}</span>}
      {action && <span className="notice-act">{action}</span>}
    </div>
  );
}
