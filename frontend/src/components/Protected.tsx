import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

/**
 * Route guard. This is a convenience, not a control: the API enforces role and
 * tenancy on every request regardless of what this component renders.
 */
export function Protected({
  children,
  adminOnly = false,
}: {
  children: ReactNode;
  adminOnly?: boolean;
}) {
  const { identity } = useAuth();
  const location = useLocation();

  if (!identity) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (adminOnly && !identity.is_admin) {
    return (
      <div className="card denied" role="alert">
        <h2>Not available to your role</h2>
        <p>
          This area is restricted to administrators. You are signed in as{" "}
          <span className="mono">{identity.role}</span>.
        </p>
      </div>
    );
  }
  return <>{children}</>;
}
