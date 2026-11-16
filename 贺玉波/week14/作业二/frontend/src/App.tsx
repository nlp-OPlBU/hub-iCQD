import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/", label: "上传文档", end: true },
  { to: "/documents", label: "文档列表" },
  { to: "/search", label: "检索调试" },
  { to: "/chat", label: "智能问答" },
  { to: "/trace", label: "链路追踪" },
  { to: "/metrics", label: "指标监控" },
];

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">RAG</span>
          <div>
            <div className="brand-title">多模态工作台</div>
            <div className="brand-sub">Week14</div>
          </div>
        </div>
        <nav>
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? "nav-item active" : "nav-item")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <a href="/health" target="_blank" rel="noreferrer">
            健康检查
          </a>
          <a href="/ready" target="_blank" rel="noreferrer">
            依赖体检
          </a>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
