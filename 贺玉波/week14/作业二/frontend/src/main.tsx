import React from "react";
import ReactDOM from "react-dom/client";
import { createHashRouter, RouterProvider } from "react-router-dom";

import App from "./App";
import ChatPage from "./pages/Chat";
import DocumentDetailPage from "./pages/DocumentDetail";
import DocumentsPage from "./pages/Documents";
import MetricsPage from "./pages/Metrics";
import SearchPage from "./pages/Search";
import TracePage from "./pages/Trace";
import UploadPage from "./pages/Upload";
import "./styles.css";

// 用 hash 路由：由 FastAPI 托管 dist 时不需要额外的 SPA 回退规则
const router = createHashRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <UploadPage /> },
      { path: "documents", element: <DocumentsPage /> },
      { path: "documents/:id", element: <DocumentDetailPage /> },
      { path: "search", element: <SearchPage /> },
      { path: "chat", element: <ChatPage /> },
      { path: "trace", element: <TracePage /> },
      { path: "metrics", element: <MetricsPage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
