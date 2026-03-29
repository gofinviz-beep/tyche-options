import { useState, useEffect, useCallback } from "react";
import { Link, useLocation } from "react-router-dom";
import { PanelLeftClose, PanelLeft, ChevronDown, ChevronRight } from "lucide-react";
import { modules } from "@/config/modules";
import type { AppModule } from "@/config/modules";

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();

  const getActiveModuleId = useCallback(() => {
    for (const mod of modules) {
      if (location.pathname.startsWith(mod.basePath)) return mod.id;
    }
    return null;
  }, [location.pathname]);

  const [expandedSections, setExpandedSections] = useState<Set<string>>(() => {
    const active = modules.find((m) => location.pathname.startsWith(m.basePath));
    return new Set(active ? [active.id] : [modules[0].id]);
  });

  useEffect(() => {
    const activeId = getActiveModuleId();
    if (activeId) {
      setExpandedSections((prev) => {
        if (prev.has(activeId)) return prev;
        const next = new Set<string>();
        next.add(activeId);
        return next;
      });
    }
  }, [getActiveModuleId]);

  const toggleSection = (moduleId: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(moduleId)) {
        next.delete(moduleId);
      } else {
        next.add(moduleId);
      }
      return next;
    });
  };

  const isActive = (mod: AppModule, pagePath: string) => {
    const fullPath = mod.basePath + pagePath;
    if (pagePath === "") {
      return location.pathname === mod.basePath || location.pathname === mod.basePath + "/";
    }
    return location.pathname.startsWith(fullPath);
  };

  const isModuleActive = (mod: AppModule) =>
    location.pathname.startsWith(mod.basePath);

  return (
    <aside
      className={`flex h-screen flex-col border-r border-gray-200 bg-white transition-all duration-200 ${
        collapsed ? "w-14" : "w-60"
      }`}
    >
      {/* Brand */}
      <div className="flex h-14 items-center border-b border-gray-200 px-3">
        <Link to="/" className="flex items-center gap-2 overflow-hidden">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 text-sm font-bold text-white">
            T
          </span>
          {!collapsed && (
            <>
              <span className="text-lg font-bold tracking-tight text-gray-900">
                Tyche
              </span>
              <span className="rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-blue-600">
                Beta
              </span>
            </>
          )}
        </Link>
      </div>

      {/* Module sections */}
      <nav className="flex-1 overflow-y-auto px-2 py-3">
        {modules.map((mod) => (
          <ModuleSection
            key={mod.id}
            module={mod}
            collapsed={collapsed}
            expanded={expandedSections.has(mod.id)}
            onToggle={() => toggleSection(mod.id)}
            isActive={isActive}
            isModuleActive={isModuleActive(mod)}
          />
        ))}
      </nav>

      {/* Bottom: collapse toggle only */}
      <div className="border-t border-gray-200 px-2 py-3">
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-sm text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <PanelLeft className="h-4 w-4 shrink-0" />
          ) : (
            <>
              <PanelLeftClose className="h-4 w-4 shrink-0" />
              <span>Collapse</span>
            </>
          )}
        </button>
      </div>
    </aside>
  );
}

function ModuleSection({
  module: mod,
  collapsed,
  expanded,
  onToggle,
  isActive,
  isModuleActive,
}: {
  module: AppModule;
  collapsed: boolean;
  expanded: boolean;
  onToggle: () => void;
  isActive: (mod: AppModule, pagePath: string) => boolean;
  isModuleActive: boolean;
}) {
  if (collapsed) {
    return (
      <div className="mb-1">
        <Link
          to={mod.basePath}
          className={`flex items-center justify-center rounded-lg p-2.5 transition-colors ${
            isModuleActive
              ? "bg-blue-50 text-blue-700"
              : "text-gray-400 hover:bg-gray-50 hover:text-gray-900"
          }`}
          title={mod.label}
        >
          <mod.icon className="h-4 w-4" />
        </Link>
      </div>
    );
  }

  const Chevron = expanded ? ChevronDown : ChevronRight;

  return (
    <div className="mb-2">
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 transition-colors hover:bg-gray-50"
      >
        <div className="flex items-center gap-2">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-gray-400">
            {mod.label}
          </h3>
          {mod.id === "options" && (
            <span className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[9px] font-medium text-amber-600">
              TRADIER
            </span>
          )}
        </div>
        <Chevron className="h-3 w-3 text-gray-400" />
      </button>

      {expanded && (
        <div className="mt-0.5 space-y-0.5">
          {mod.pages.map((page) => {
            const fullPath = mod.basePath + page.path;
            const active = isActive(mod, page.path);
            return (
              <Link
                key={fullPath}
                to={fullPath}
                className={`flex items-center gap-3 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors ${
                  active
                    ? "bg-blue-50 text-blue-700"
                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                }`}
              >
                <page.icon className="h-4 w-4 shrink-0" />
                <span>{page.label}</span>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
