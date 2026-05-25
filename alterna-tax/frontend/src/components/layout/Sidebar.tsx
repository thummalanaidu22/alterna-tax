import { LayoutDashboard, Search, Grid3X3, ClipboardList, Brain, Map, ShieldCheck } from "lucide-react";
import { NavLink } from "react-router-dom";

const NAV = [
  { to: "/",        icon: <LayoutDashboard className="w-4 h-4" />, label: "Dashboard" },
  { to: "/analyze", icon: <Search className="w-4 h-4" />,          label: "Analyze" },
  { to: "/batch",   icon: <Grid3X3 className="w-4 h-4" />,         label: "Batch" },
  { to: "/jobs",    icon: <ClipboardList className="w-4 h-4" />,    label: "All Jobs" },
  { to: "/map",     icon: <Map className="w-4 h-4" />,              label: "Map" },
  { to: "/review",  icon: <ShieldCheck className="w-4 h-4" />,      label: "Review Queue" },
];

export function Sidebar() {
  return (
    <aside className="w-56 shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col h-screen sticky top-0">
      {/* Logo */}
      <div className="px-5 py-5 border-b border-gray-800">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-blue-600 flex items-center justify-center">
            <Brain className="w-4 h-4 text-white" />
          </div>
          <div>
            <div className="text-sm font-bold text-white leading-tight">PropIntel</div>
            <div className="text-[10px] text-gray-500 leading-tight">AI Due Diligence</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {NAV.map(({ to, icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                isActive
                  ? "bg-blue-600/20 text-blue-400 font-medium"
                  : "text-gray-500 hover:text-gray-200 hover:bg-gray-800"
              }`
            }
          >
            {icon}
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="px-4 py-3 border-t border-gray-800">
        <p className="text-xs text-gray-700">v2.0.0 · MiniCPM-V</p>
      </div>
    </aside>
  );
}
