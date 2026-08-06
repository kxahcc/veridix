import { AlertCircle, Info, Loader2, PackageOpen } from "lucide-react";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="status-line" role="status">
      <Loader2 className="spin" style={{ verticalAlign: "-2px", marginRight: 6 }} />
      {label}...
    </div>
  );
}

export function Empty({ label }: { label: string }) {
  return (
    <div className="empty-state">
      <PackageOpen className="" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="banner banner-error" role="alert">
      <AlertCircle className="" />
      <span>{message}</span>
    </div>
  );
}

export function OfflineBanner() {
  if (navigator.onLine) {
    return null;
  }
  return (
    <div className="banner banner-warn">
      <Info className="" />
      <span>Offline: reconnect to continue updating from the event cursor.</span>
    </div>
  );
}
