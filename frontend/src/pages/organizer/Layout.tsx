import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../../auth";

const LINKS = [
  { to: "/organizer", label: "Overview", end: true },
  { to: "/organizer/projects", label: "Projects", end: false },
  { to: "/organizer/judges", label: "Judges", end: false },
  { to: "/organizer/schedule", label: "Schedule", end: false },
  { to: "/organizer/progress", label: "Live progress", end: false },
  { to: "/organizer/results", label: "Results", end: false },
  { to: "/organizer/cross-track", label: "Cross-track", end: false },
];

export default function OrganizerLayout() {
  const { user, logout } = useAuth();
  return (
    <div className="org-shell">
      <aside className="org-nav">
        <div className="brand-block">
          <p className="wordmark light">Tech@NYU</p>
          <strong>Organizer</strong>
        </div>
        <nav>
          {LINKS.map((link) => (
            <NavLink key={link.to} to={link.to} end={link.end} className={({ isActive }) => (isActive ? "nav-link on" : "nav-link")}>
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="nav-foot">
          <span>{user?.name}</span>
          <button className="btn ghost small" type="button" onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="org-main">
        <Outlet />
      </main>
    </div>
  );
}
