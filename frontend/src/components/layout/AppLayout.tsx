import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { useConvictionVersion } from "@/hooks/useApi";

export function AppLayout() {
  useConvictionVersion();

  return (
    <div className="flex h-screen bg-gray-50 text-gray-900" data-print-shell>
      <Sidebar />
      <main className="flex-1 overflow-y-auto" data-print-main>
        <div className="mx-auto max-w-7xl px-6 py-6" data-print-content>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
