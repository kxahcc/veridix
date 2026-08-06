import type { ComponentType, ReactNode } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Info,
  Loader2,
  PackageOpen,
} from "lucide-react";

export function Chip({
  children,
  tone,
}: {
  children: ReactNode;
  tone?: "ok" | "warn" | "danger" | "info" | "violet";
}) {
  return (
    <span
      className={`badge${tone ? ` badge-${tone === "violet" ? "method" : tone}` : ""}`}
    >
      {children}
    </span>
  );
}

export function Progress({
  value,
  label,
  tone = "ok",
}: {
  value: number;
  label?: string;
  tone?: "ok" | "warn" | "danger" | "info";
}) {
  const width = Math.max(0, Math.min(100, Math.round(value * 100)));
  return (
    <div className="metric-row">
      {label ? <span>{label}</span> : null}
      <div className="metric-bar" style={{ gridColumn: label ? undefined : "1 / -1" }}>
        <div
          style={{
            width: `${width}%`,
            background:
              tone === "warn"
                ? "linear-gradient(90deg, #d97706, #fbbf24)"
                : tone === "danger"
                  ? "linear-gradient(90deg, #dc2626, #f87171)"
                  : tone === "info"
                    ? "linear-gradient(90deg, #0284c7, #38bdf8)"
                    : undefined,
          }}
        />
      </div>
      <span>{width}%</span>
    </div>
  );
}

export function LiveDot({
  tone = "ok",
  pulse = true,
}: {
  tone?: "ok" | "warn" | "danger";
  pulse?: boolean;
}) {
  return <span className={`status-dot ${tone}${pulse ? " pulse" : ""}`} />;
}

export function SyncStamp({
  dataUpdatedAt,
  label = "最后同步",
}: {
  dataUpdatedAt?: number;
  label?: string;
}) {
  if (!dataUpdatedAt) {
    return <span className="sync-stamp">{label} --:--:--</span>;
  }
  const date = new Date(dataUpdatedAt);
  const time = date.toLocaleTimeString("zh-CN", { hour12: false });
  return (
    <span
      className="sync-stamp"
      title={date.toISOString()}
    >
      {label} {time}
    </span>
  );
}

export function Badge({
  value,
  className,
  children,
}: {
  value?: string;
  className?: string;
  children?: ReactNode;
}) {
  const text = children ?? String(value ?? "unknown");
  return (
    <span className={`badge ${className ?? ""}${value ? ` badge-${value}` : ""}`}>
      {text}
    </span>
  );
}

export function Kpi({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: ReactNode;
  note?: string;
  tone?: "ok" | "warn" | "danger" | "info";
}) {
  return (
    <div className={`kpi${tone ? ` ${tone}` : ""}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {note ? <span className="kpi-note">{note}</span> : null}
    </div>
  );
}

export function Panel({
  title,
  icon: Icon,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  icon?: ComponentType<{ className?: string }>;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel${className ? ` ${className}` : ""}`}>
      {title !== undefined ? (
        <div className="panel-head">
          <h3 className="panel-title">
            {Icon ? <Icon className="" /> : null}
            {title}
          </h3>
          {actions ? <div className="panel-actions">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function LoadingState({ label = "加载中" }: { label?: string }) {
  return (
    <div className="status-line" role="status">
      <Loader2 className="spin" style={{ verticalAlign: "-2px", marginRight: 6 }} />
      {label}...
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div>
        <PackageOpen className="" />
        <strong>{title}</strong>
        {description ? <p>{description}</p> : null}
        {action}
      </div>
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="banner banner-error" role="alert">
      <AlertCircle className="" />
      <span>{message}</span>
    </div>
  );
}

export function Notice({
  tone,
  children,
}: {
  tone: "ok" | "warn" | "error" | "info";
  children: ReactNode;
}) {
  const Icon =
    tone === "ok"
      ? CheckCircle2
      : tone === "warn" || tone === "info"
        ? Info
        : AlertCircle;
  return (
    <div className={`banner banner-${tone}`}>
      <Icon className="" />
      <span>{children}</span>
    </div>
  );
}

export function JsonView({ value, className }: { value: unknown; className?: string }) {
  return <pre className={`result-box${className ? ` ${className}` : ""}`}>{JSON.stringify(value, null, 2)}</pre>;
}

export function TableRowLink({ onClick }: { onClick?: () => void }) {
  return <ChevronRight className="row-link" style={{ width: 13, height: 13 }} onClick={onClick} />;
}
