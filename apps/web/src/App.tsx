import { useQuery } from "@tanstack/react-query";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useLocation, useNavigate, NavLink, Route, Routes } from "react-router-dom";
import { BrowserRouter } from "react-router-dom";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
} from "react";
import {
  Activity,
  BookMarked,
  BrainCircuit,
  Copy,
  Database,
  FileCheck2,
  FileText,
  LayoutDashboard,
  Menu,
  MessageSquareText,
  MonitorPlay,
  Search,
  PlusCircle,
  Radio,
  RefreshCw,
  Settings,
  ShieldCheck,
  ShieldAlert,
  ShieldHalf,
} from "lucide-react";
import { control, CONTROL_URL } from "./api.js";
import { useRunSelection } from "./store.js";
import { AuthGate } from "./components/AuthGate.js";

const EvidenceDetail = lazy(() =>
  import("./pages/EvidenceDetail.js").then((module) => ({
    default: module.EvidenceDetail,
  })),
);
const MissionSetup = lazy(() =>
  import("./pages/MissionSetup.js").then((module) => ({
    default: module.MissionSetup,
  })),
);
const RunCenter = lazy(() =>
  import("./pages/RunCenter.js").then((module) => ({
    default: module.RunCenter,
  })),
);
const RunCockpit = lazy(() =>
  import("./pages/RunCockpit.js").then((module) => ({
    default: module.RunCockpit,
  })),
);
const SettingsDiagnostics = lazy(() =>
  import("./pages/SettingsDiagnostics.js").then((module) => ({
    default: module.SettingsDiagnostics,
  })),
);
const WebConsole = lazy(() =>
  import("./pages/WebConsole.js").then((module) => ({
    default: module.WebConsole,
  })),
);
const KnowledgePage = lazy(() =>
  import("./pages/KnowledgePage.js").then((module) => ({
    default: module.KnowledgePage,
  })),
);
const MemoryPage = lazy(() =>
  import("./pages/MemoryPage.js").then((module) => ({
    default: module.MemoryPage,
  })),
);
const AssetsPage = lazy(() =>
  import("./pages/AssetsPage.js").then((module) => ({
    default: module.AssetsPage,
  })),
);
const ReportsPage = lazy(() =>
  import("./pages/ReportsPage.js").then((module) => ({
    default: module.ReportsPage,
  })),
);
const SessionsPage = lazy(() =>
  import("./pages/SessionsPage.js").then((module) => ({
    default: module.SessionsPage,
  })),
);
const VulnerabilitiesPage = lazy(() =>
  import("./pages/VulnerabilitiesPage.js").then((module) => ({
    default: module.VulnerabilitiesPage,
  })),
);
const AcceptancePage = lazy(() =>
  import("./pages/AcceptancePage.js").then((module) => ({
    default: module.AcceptancePage,
  })),
);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 2000,
    },
  },
});

const navGroups = [
  {
    label: "运行",
    items: [
      { to: "/", label: "任务中心", end: true, icon: LayoutDashboard },
      { to: "/setup", label: "新建任务", end: false, icon: PlusCircle },
      { to: "/cockpit", label: "运行控制台", end: false, icon: MonitorPlay },
      { to: "/sessions", label: "会话", end: false, icon: MessageSquareText },
      { to: "/console", label: "Web 流量", end: false, icon: Radio },
    ],
  },
  {
    label: "情报",
    items: [
      { to: "/evidence", label: "证据与发现", end: false, icon: ShieldCheck },
      { to: "/vulnerabilities", label: "漏洞与风险", end: false, icon: ShieldAlert },
      { to: "/knowledge", label: "知识库", end: false, icon: BrainCircuit },
      { to: "/memory", label: "项目记忆", end: false, icon: BookMarked },
      { to: "/assets", label: "资产与组件", end: false, icon: Database },
      { to: "/reports", label: "报告", end: false, icon: FileText },
    ],
  },
  {
    label: "系统",
    items: [
      { to: "/settings", label: "诊断与设置", end: false, icon: Settings },
      { to: "/acceptance", label: "验收", end: false, icon: FileCheck2 },
    ],
  },
];

const routeMeta: Record<
  string,
  { title: string; subtitle: string }
> = {
  "/": { title: "任务中心", subtitle: "运行队列、状态与最近活动" },
  "/setup": { title: "新建任务", subtitle: "配置项目、目标与执行策略" },
  "/cockpit": { title: "运行控制台", subtitle: "事件流、图编排与内存投影" },
  "/sessions": { title: "会话", subtitle: "会话列表与归档管理" },
  "/console": { title: "Web 流量", subtitle: "代理捕获的请求与响应" },
  "/evidence": { title: "证据与发现", subtitle: "发现、证据、审批与人工门禁" },
  "/vulnerabilities": { title: "漏洞与风险", subtitle: "漏洞聚合与风险评分" },
  "/knowledge": { title: "知识库", subtitle: "知识分块、检索与审计" },
  "/memory": { title: "项目记忆", subtitle: "跨会话事实与用户修正管理" },
  "/assets": { title: "资产与组件", subtitle: "工具注册表、技能、MCP 与存储" },
  "/reports": { title: "报告", subtitle: "运行报告与归档下载" },
  "/settings": { title: "诊断与设置", subtitle: "环境与后端状态" },
  "/acceptance": { title: "验收", subtitle: "真实门禁、RAG 基准与 readiness" },
};

function GlobalStatus() {
  const diagnostics = useQuery({
    queryKey: ["diagnostics"],
    queryFn: () => control.requestPublic("/api/v1/diagnostics"),
    refetchInterval: 10000,
    retry: 1,
  });
  const data = diagnostics.data as
    | {
        storage?: Record<string, Record<string, string>> & {
          available?: boolean;
        };
        connectors?: Record<string, { status?: string }>;
      }
    | undefined;
  const storage = data?.storage ?? {};
  const backendEntries = Object.entries(storage).filter(
    ([key]) => key !== "available",
  );
  const backendOk = backendEntries.filter(
    ([, value]) =>
      !String((value as Record<string, string>)?.status ?? "")
        .toLowerCase()
        .includes("error") &&
      !String((value as Record<string, string>)?.status ?? "")
        .toLowerCase()
        .includes("failed") &&
      !String((value as Record<string, string>)?.status ?? "")
        .toLowerCase()
        .includes("not_configured"),
  ).length;
  const connectors = Object.values(data?.connectors ?? {}).filter(
    (value) =>
      ["ok", "connected", "ready"].includes(
        String(value?.status ?? "").toLowerCase(),
      ),
  ).length;
  const totalConnectors = Object.keys(data?.connectors ?? {}).length;
  const online = diagnostics.isSuccess;

  return (
    <>
      <span className="status-chip" title={`控制面 ${CONTROL_URL}`}>
        <span className={`status-dot ${online ? "ok" : "danger"}`} />
        <span className="chip-text">{online ? "控制面在线" : "控制面离线"}</span>
      </span>
      <span
        className="status-chip"
        title="存储后端"
      >
        <Database className="" style={{ width: 13, height: 13 }} />
        <span className="chip-text">
          {diagnostics.isSuccess
            ? storage.available === false
              ? "存储未就绪"
              : `存储 ${backendOk}/${backendEntries.length}`
            : "存储未上报"}
        </span>
      </span>
      <span className="status-chip" title="连接器">
        <Activity className="" style={{ width: 13, height: 13 }} />
        <span className="chip-text">
          {totalConnectors ? `连接器 ${connectors}/${totalConnectors}` : "连接器未上报"}
        </span>
      </span>
    </>
  );
}

function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const selectedRunId = useRunSelection((state) => state.selectedRunId);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  type PaletteItem = {
    id: string;
    label: string;
    group: string;
    icon: ComponentType<{ className?: string }>;
    to?: string;
    action?: () => void;
  };
  const items = useMemo<PaletteItem[]>(() => {
    const navigation: PaletteItem[] = navGroups.flatMap((group) =>
      group.items.map((item) => ({
        id: item.to,
        label: item.label,
        group: group.label,
        icon: item.icon,
        to: item.to,
      })),
    );
    const actions: PaletteItem[] = [
      {
        id: "open-current-run",
        label: selectedRunId
          ? `打开当前运行 ${selectedRunId.slice(0, 16)}`
          : "打开当前运行",
        group: "动作",
        icon: MonitorPlay,
        action: () => {
          navigate(selectedRunId ? "/cockpit" : "/");
        },
      },
      {
        id: "refresh-current",
        label: "刷新当前页",
        group: "动作",
        icon: RefreshCw,
        action: () => window.location.reload(),
      },
      {
        id: "copy-control-url",
        label: "复制控制面地址",
        group: "动作",
        icon: Copy,
        action: () => {
          void navigator.clipboard?.writeText(CONTROL_URL).catch(() => {});
        },
      },
    ];
    return [...navigation, ...actions].filter((item) => {
      const text = query.trim().toLowerCase();
      if (!text) {
        return true;
      }
      return (
        item.label.toLowerCase().includes(text) ||
        String(item.id).toLowerCase().includes(text)
      );
    });
  }, [query, selectedRunId, navigate]);
  useEffect(() => {
    if (!open) {
      setQuery("");
      setActive(0);
    }
  }, [open]);
  useEffect(() => setActive(0), [query]);
  useEffect(() => {
    if (!open) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((current) => Math.min(current + 1, items.length - 1));
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((current) => Math.max(current - 1, 0));
      } else if (event.key === "Enter" && items[active]) {
        const item = items[active];
        if (item.to) {
          navigate(item.to);
        } else {
          item.action?.();
        }
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, items, active, navigate, onClose]);

  if (!open) {
    return null;
  }
  return (
    <div
      className="command-palette"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="命令面板"
    >
      <div
        className="command-palette-box"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="toolbar" style={{ margin: 0 }}>
          <Search className="" style={{ width: 15, height: 15, color: "var(--muted)", marginLeft: 8 }} />
          <input
            autoFocus
            type="text"
            aria-label="搜索页面"
            placeholder="搜索页面..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className="command-list">
          {items.length === 0 ? (
            <div className="status-line" style={{ padding: 14 }}>
              没有匹配的页面
            </div>
          ) : (
            items.map((item, index) => (
              <button
                key={item.id}
                className={`command-item${index === active ? " active" : ""}`}
                onClick={() => {
                  if (item.to) {
                    navigate(item.to);
                  } else {
                    item.action?.();
                  }
                  onClose();
                }}
                onMouseEnter={() => setActive(index)}
              >
                <item.icon className="" />
                <span>{item.label}</span>
                <span className="muted" style={{ marginLeft: "auto", fontSize: 11 }}>
                  {item.group}
                </span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function Shell() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const paletteTriggerRef = useRef<HTMLButtonElement | null>(null);
  const location = useLocation();
  const selectedRunId = useRunSelection((state) => state.selectedRunId);
  const meta = routeMeta[location.pathname] ?? {
    title: "Veridix",
    subtitle: "安全测试 Agent 控制台",
  };
  const closeSidebar = useCallback(() => {
    setSidebarOpen(false);
    menuButtonRef.current?.focus();
  }, []);
  const closePalette = useCallback(() => {
    setPaletteOpen(false);
    paletteTriggerRef.current?.focus();
  }, []);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((current) => !current);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div className="app-shell">
      <aside className={`sidebar${sidebarOpen ? " open" : ""}`}>
        <div className="sidebar-brand">
          <span className="brand-mark">
            <ShieldHalf className="" style={{ width: 17, height: 17 }} />
          </span>
          <span>
            <span className="brand-title">Veridix</span>
            <span className="brand-sub">Veridix 安全测试平台</span>
          </span>
        </div>
        <nav className="sidebar-nav">
          {navGroups.map((group) => (
            <div key={group.label} className="nav-group">
              <div className="nav-group-title">{group.label}</div>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  onClick={closeSidebar}
                  className={({ isActive }) =>
                    `nav-link${isActive ? " active" : ""}`
                  }
                >
                  <item.icon className="" />
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="sidebar-footer-line">控制面 {CONTROL_URL}</div>
          {selectedRunId ? (
            <div className="sidebar-footer-line" title={selectedRunId}>
              当前运行 {selectedRunId.slice(0, 20)}
            </div>
          ) : null}
        </div>
      </aside>
      {sidebarOpen ? (
        <div
          className="sidebar-scrim"
          onClick={closeSidebar}
          role="presentation"
          aria-hidden="true"
        />
      ) : null}
      <div className="main">
        <header className="topbar">
          <div className="topbar-left">
            <button
              ref={menuButtonRef}
              className="btn btn-icon btn-ghost mobile-menu"
              onClick={() => setSidebarOpen(true)}
              aria-label="打开导航"
            >
              <Menu className="" />
            </button>
            <div className="topbar-title">
              <strong>{meta.title}</strong>
              <span>{meta.subtitle}</span>
            </div>
          </div>
          <div className="topbar-right">
            <button
              ref={paletteTriggerRef}
              className="command-trigger"
              onClick={() => setPaletteOpen(true)}
              title="快速导航 (Ctrl+K)"
              aria-haspopup="dialog"
              aria-expanded={paletteOpen}
            >
              <Search className="" style={{ width: 14, height: 14 }} />
              <span>快速导航</span>
              <kbd>Ctrl K</kbd>
            </button>
            <GlobalStatus />
            {selectedRunId ? (
              <span className="status-chip" title={selectedRunId}>
                <MonitorPlay className="" style={{ width: 13, height: 13 }} />
                <span className="chip-text">{selectedRunId.slice(0, 16)}</span>
              </span>
            ) : null}
          </div>
        </header>
        <main className="content">
          <Suspense
            fallback={
              <div className="empty-state">
                <div className="empty-title">加载中...</div>
                <div className="muted">正在装载工作台。</div>
              </div>
            }
          >
            <Routes>
              <Route path="/" element={<RunCenter />} />
              <Route path="/setup" element={<MissionSetup />} />
              <Route path="/cockpit" element={<RunCockpit />} />
              <Route path="/sessions" element={<SessionsPage />} />
              <Route path="/console" element={<WebConsole />} />
              <Route path="/evidence" element={<EvidenceDetail />} />
              <Route path="/vulnerabilities" element={<VulnerabilitiesPage />} />
              <Route path="/knowledge" element={<KnowledgePage />} />
              <Route path="/memory" element={<MemoryPage />} />
              <Route path="/assets" element={<AssetsPage />} />
              <Route path="/reports" element={<ReportsPage />} />
              <Route path="/settings" element={<SettingsDiagnostics />} />
              <Route path="/acceptance" element={<AcceptancePage />} />
            </Routes>
          </Suspense>
        </main>
      </div>
      <CommandPalette open={paletteOpen} onClose={closePalette} />
    </div>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthGate>
          <Shell />
        </AuthGate>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
