import { Link, useLocation } from "react-router-dom";

const navItems = [
  { path: "/", label: "Dashboard" },
  { path: "/scanner", label: "Scanner" },
  { path: "/orders", label: "Orders" },
  { path: "/monitor", label: "Monitor" },
  { path: "/settings", label: "Settings" },
];

export function Navbar() {
  const location = useLocation();

  return (
    <nav className="border-b border-gray-800 bg-gray-900/95 backdrop-blur-sm">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4">
        <div className="flex items-center gap-8">
          <Link to="/" className="text-lg font-bold tracking-tight text-white">
            Tyche
            <span className="ml-1 text-xs font-normal text-gray-500">
              Options
            </span>
          </Link>

          <div className="flex items-center gap-1">
            {navItems.map((item) => (
              <Link
                key={item.path}
                to={item.path}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                  location.pathname === item.path
                    ? "bg-gray-800 text-white"
                    : "text-gray-400 hover:bg-gray-800/50 hover:text-gray-200"
                }`}
              >
                {item.label}
              </Link>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span className="rounded-full bg-amber-500/20 px-2.5 py-0.5 text-amber-400 border border-amber-500/30">
            SANDBOX
          </span>
          <span className="rounded-full bg-blue-500/20 px-2.5 py-0.5 text-blue-400 border border-blue-500/30">
            PREVIEW ONLY
          </span>
        </div>
      </div>
    </nav>
  );
}
