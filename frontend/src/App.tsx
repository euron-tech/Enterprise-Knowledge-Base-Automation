import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { Protected } from "./components/Protected";
import { Landing } from "./pages/Landing";
import { Login } from "./pages/Login";
import { AppShell } from "./pages/AppShell";
import { Access } from "./pages/Access";
import { Chat } from "./features/chat/Chat";
import { Search } from "./features/chat/Search";
import { Documents } from "./features/documents/Documents";
import { Admin } from "./features/admin/Admin";

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Login />} />

          <Route
            path="/app"
            element={
              <Protected>
                <AppShell />
              </Protected>
            }
          >
            <Route index element={<Navigate to="/app/chat" replace />} />
            <Route path="chat" element={<Chat />} />
            <Route path="search" element={<Search />} />
            <Route path="documents" element={<Documents />} />
            <Route path="access" element={<Access />} />
            {/* adminOnly is a courtesy; /admin/metrics returns 403 regardless. */}
            <Route
              path="admin"
              element={
                <Protected adminOnly>
                  <Admin />
                </Protected>
              }
            />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
