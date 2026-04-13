import type { LucideIcon } from "lucide-react";
import {
  TrendingUp,
  BookOpen,
  LayoutDashboard,
  Search,
  BarChart3,
  FileCheck,
  ShoppingCart,
  Activity,
  Settings,
  LineChart,
  Clock,
  Compass,
  Brain,
  Newspaper,
  FileText,
  Users,
} from "lucide-react";

export interface ModulePage {
  path: string;
  label: string;
  icon: LucideIcon;
}

export interface AppModule {
  id: string;
  label: string;
  icon: LucideIcon;
  basePath: string;
  pages: ModulePage[];
}

export const modules: AppModule[] = [
  {
    id: "options",
    label: "Options",
    icon: TrendingUp,
    basePath: "/options",
    pages: [
      { path: "", label: "Dashboard", icon: LayoutDashboard },
      { path: "/scanner", label: "Scanner", icon: Search },
      { path: "/conviction", label: "Conviction", icon: BarChart3 },
      { path: "/explore", label: "Explore", icon: Compass },
      { path: "/intents", label: "Intents", icon: FileCheck },
      { path: "/orders", label: "Orders", icon: ShoppingCart },
      { path: "/monitor", label: "Monitor", icon: Activity },
    ],
  },
  {
    id: "stocks",
    label: "Stocks",
    icon: LineChart,
    basePath: "/stocks",
    pages: [
      { path: "", label: "Dashboard", icon: LayoutDashboard },
      { path: "/conviction", label: "Conviction", icon: BarChart3 },
      { path: "/history", label: "History", icon: Clock },
    ],
  },
  {
    id: "intelligence",
    label: "Intelligence",
    icon: Brain,
    basePath: "/intelligence",
    pages: [
      { path: "", label: "Dashboard", icon: LayoutDashboard },
      { path: "/news", label: "News", icon: Newspaper },
      { path: "/filings", label: "SEC Filings", icon: FileText },
      { path: "/insider", label: "Insider Activity", icon: Users },
    ],
  },
  {
    id: "research",
    label: "Research",
    icon: BookOpen,
    basePath: "/research",
    pages: [{ path: "", label: "Companies", icon: BookOpen }],
  },
];

export const globalPages = [
  { path: "/settings", label: "Settings", icon: Settings },
] as const;
