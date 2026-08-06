import { useEffect, useState, type ReactNode } from "react";
import { ShieldCheck } from "lucide-react";
import { CONTROL_URL, setControlToken } from "../api.js";
import { Loading } from "./Status.js";
import { Notice } from "./ui.js";

const TOKEN_KEY = "veridix_control_token";

export function AuthGate({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() =>
    typeof localStorage === "undefined"
      ? null
      : localStorage.getItem(TOKEN_KEY),
  );
  const [requiresAuth, setRequiresAuth] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setControlToken(token);
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setChecking(true);
      setError("");
      try {
        const response = await fetch(`${CONTROL_URL}/api/v1/auth/status`, {
          signal: AbortSignal.timeout(8000),
        });
        const payload = (await response.json()) as {
          requires_auth?: boolean;
        };
        if (cancelled) {
          return;
        }
        setRequiresAuth(Boolean(payload.requires_auth));
        if (!payload.requires_auth) {
          setToken(null);
          localStorage.removeItem(TOKEN_KEY);
          setChecking(false);
          return;
        }
        if (token) {
          const login = await fetch(`${CONTROL_URL}/api/v1/auth/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token }),
            signal: AbortSignal.timeout(8000),
          });
          if (login.ok) {
            setChecking(false);
            return;
          }
          localStorage.removeItem(TOKEN_KEY);
          setToken(null);
        }
        setChecking(false);
      } catch {
        if (!cancelled) {
          setError("无法连接控制面，请确认服务已启动。");
          setChecking(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const login = async () => {
    setError("");
    if (!token) {
      setError("请输入 API Token");
      return;
    }
    try {
      const response = await fetch(`${CONTROL_URL}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
        signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) {
        throw new Error("Token 无效");
      }
      localStorage.setItem(TOKEN_KEY, token);
      setControlToken(token);
      setChecking(false);
    } catch (err) {
      setError(String(err));
    }
  };

  if (checking) {
    return <Loading label="检查身份" />;
  }
  if (!requiresAuth) {
    return <>{children}</>;
  }

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 24,
      }}
    >
      <div
        className="panel"
        style={{ width: "min(420px, 100%)", padding: 20 }}
      >
        <div className="panel-head" style={{ marginBottom: 12 }}>
          <div className="card-title" style={{ margin: 0 }}>
            <ShieldCheck className="" />
            Veridix
          </div>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          控制面已启用访问控制，请输入 API Token 后继续。
        </p>
        <label className="field">
          API Token
          <input
            type="password"
            value={token ?? ""}
            onChange={(event) => setToken(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                void login();
              }
            }}
            placeholder="输入控制面 Token"
          />
        </label>
        <button
          className="btn btn-primary"
          style={{ marginTop: 12 }}
          onClick={() => void login()}
        >
          登录
        </button>
        {error ? (
          <div style={{ marginTop: 10 }}>
            <Notice tone="error">{error}</Notice>
          </div>
        ) : null}
      </div>
    </main>
  );
}
