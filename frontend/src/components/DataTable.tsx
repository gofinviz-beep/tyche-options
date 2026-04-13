import { useState, useMemo, type ReactNode } from "react";
import { ChevronUp, ChevronDown, Search, ChevronsLeft, ChevronsRight, ChevronLeft, ChevronRight, ListFilter, X } from "lucide-react";

export interface ColumnFilterOption {
  value: string;
  label: string;
}

export interface ColumnFilterConfig {
  type: "select" | "multiselect" | "min" | "max" | "range" | "boolean";
  options?: ColumnFilterOption[];
  minOptions?: ColumnFilterOption[];
  maxOptions?: ColumnFilterOption[];
  placeholder?: string;
}

export interface DataTableColumn<T> {
  key: string;
  header: string;
  accessor: (row: T) => string | number | boolean | null | undefined;
  sortable?: boolean;
  align?: "left" | "right" | "center";
  render?: (row: T) => ReactNode;
  width?: string;
  filter?: ColumnFilterConfig;
}

interface DataTableProps<T> {
  data: T[];
  columns: DataTableColumn<T>[];
  searchField?: (row: T) => string;
  defaultPageSize?: number;
  defaultSortKey?: string;
  defaultSortDir?: "asc" | "desc";
  rowKey: (row: T) => string;
  emptyMessage?: string;
  onRowClick?: (row: T) => void;
  expandedRow?: (row: T) => ReactNode;
}

const PAGE_SIZES = [10, 15, 20, 50];

function compareValues(
  a: string | number | boolean | null | undefined,
  b: string | number | boolean | null | undefined,
): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "boolean" && typeof b === "boolean") return a === b ? 0 : a ? -1 : 1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

export function DataTable<T>({
  data,
  columns,
  searchField,
  defaultPageSize = 15,
  defaultSortKey,
  defaultSortDir = "desc",
  rowKey,
  emptyMessage = "No data available.",
  onRowClick,
  expandedRow,
}: DataTableProps<T>) {
  const [search, setSearch] = useState("");
  const [pageSize, setPageSize] = useState(defaultPageSize);
  const [currentPage, setCurrentPage] = useState(1);
  const [sortKey, setSortKey] = useState<string | null>(defaultSortKey ?? null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">(defaultSortDir);
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({});

  const hasAnyFilter = columns.some((c) => c.filter);
  const activeFilterCount = Object.values(columnFilters).filter((v) => v && v !== ":" && v !== ",").length;

  const setColumnFilter = (key: string, value: string) => {
    setColumnFilters((prev) => {
      const next = { ...prev };
      if (value) next[key] = value;
      else delete next[key];
      return next;
    });
    setCurrentPage(1);
  };

  const clearAllFilters = () => {
    setColumnFilters({});
    setCurrentPage(1);
  };

  const filtered = useMemo(() => {
    let rows = data;

    if (search.trim()) {
      const q = search.trim().toLowerCase();
      rows = rows.filter((row) => {
        const field = searchField ? searchField(row) : "";
        return field.toLowerCase().includes(q);
      });
    }

    const filterEntries = Object.entries(columnFilters).filter(([, v]) => v);
    if (filterEntries.length === 0) return rows;

    return rows.filter((row) => {
      for (const [key, filterValue] of filterEntries) {
        const col = columns.find((c) => c.key === key);
        if (!col?.filter) continue;
        const cellValue = col.accessor(row);
        const cfg = col.filter;

        if (cfg.type === "select") {
          if (String(cellValue ?? "") !== filterValue) return false;
        } else if (cfg.type === "multiselect") {
          const selected = filterValue.split(",").filter(Boolean);
          if (selected.length > 0 && !selected.includes(String(cellValue ?? ""))) return false;
        } else if (cfg.type === "min") {
          const threshold = Number(filterValue);
          if (cellValue == null || Number(cellValue) < threshold) return false;
        } else if (cfg.type === "max") {
          const threshold = Number(filterValue);
          if (cellValue == null || Number(cellValue) > threshold) return false;
        } else if (cfg.type === "range") {
          const [minStr, maxStr] = filterValue.split(":");
          if (cellValue == null) return false;
          const num = Number(cellValue);
          if (isNaN(num)) return false;
          if (minStr && num < Number(minStr)) return false;
          if (maxStr && num > Number(maxStr)) return false;
        } else if (cfg.type === "boolean") {
          const want = filterValue === "true";
          const actual = cellValue === true || cellValue === 1 || Number(cellValue) > 0;
          if (actual !== want) return false;
        }
      }
      return true;
    });
  }, [data, search, searchField, columnFilters, columns]);

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    const col = columns.find((c) => c.key === sortKey);
    if (!col) return filtered;
    const arr = [...filtered];
    arr.sort((a, b) => {
      const va = col.accessor(a);
      const vb = col.accessor(b);
      const cmp = compareValues(va, vb);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortKey, sortDir, columns]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(currentPage, totalPages);
  const startIdx = (safePage - 1) * pageSize;
  const pageData = sorted.slice(startIdx, startIdx + pageSize);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
    setCurrentPage(1);
  };

  const toggleExpand = (key: string) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const alignClass = (a?: "left" | "right" | "center") =>
    a === "right" ? "text-right" : a === "center" ? "text-center" : "text-left";

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder="Search ticker..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full rounded-lg border border-gray-300 bg-white py-1.5 pl-9 pr-3 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span>{sorted.length} result{sorted.length !== 1 ? "s" : ""}</span>
          {hasAnyFilter && activeFilterCount > 0 && (
            <>
              <span className="text-gray-300">|</span>
              <button
                onClick={clearAllFilters}
                className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700 hover:bg-blue-100"
              >
                <ListFilter className="h-3 w-3" />
                {activeFilterCount} filter{activeFilterCount > 1 ? "s" : ""}
                <X className="h-3 w-3" />
              </button>
            </>
          )}
          <span className="text-gray-300">|</span>
          <select
            value={pageSize}
            onChange={(e) => {
              setPageSize(Number(e.target.value));
              setCurrentPage(1);
            }}
            className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-700"
          >
            {PAGE_SIZES.map((s) => (
              <option key={s} value={s}>
                {s} / page
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Table */}
      {pageData.length === 0 ? (
        <p className="py-8 text-center text-sm text-gray-400">{emptyMessage}</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                {columns.map((col) => (
                  <th
                    key={col.key}
                    className={`px-3 py-2.5 text-xs font-medium uppercase tracking-wide text-gray-500 ${alignClass(col.align)} ${col.sortable ? "cursor-pointer select-none hover:text-gray-700" : ""}`}
                    style={col.width ? { width: col.width } : undefined}
                    onClick={col.sortable ? () => handleSort(col.key) : undefined}
                  >
                    <span className="inline-flex items-center gap-1">
                      {col.header}
                      {col.sortable && sortKey === col.key && (
                        sortDir === "asc"
                          ? <ChevronUp className="h-3 w-3" />
                          : <ChevronDown className="h-3 w-3" />
                      )}
                    </span>
                  </th>
                ))}
              </tr>
              {hasAnyFilter && (
                <tr className="border-b border-gray-200 bg-gray-50/70">
                  {columns.map((col) => (
                    <th key={`filter-${col.key}`} className={`px-2 py-1.5 ${alignClass(col.align)}`}>
                      {col.filter ? (
                        <ColumnFilterCell
                          config={col.filter}
                          value={columnFilters[col.key] ?? ""}
                          onChange={(v) => setColumnFilter(col.key, v)}
                        />
                      ) : null}
                    </th>
                  ))}
                </tr>
              )}
            </thead>
            <tbody>
              {pageData.map((row) => {
                const key = rowKey(row);
                const isExpanded = expandedKeys.has(key);
                return (
                  <TableRow
                    key={key}
                    row={row}
                    columns={columns}
                    alignClass={alignClass}
                    onClick={
                      expandedRow
                        ? () => toggleExpand(key)
                        : onRowClick
                          ? () => onRowClick(row)
                          : undefined
                    }
                    isExpanded={isExpanded}
                    expandedContent={isExpanded && expandedRow ? expandedRow(row) : null}
                    colCount={columns.length}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-xs text-gray-500">
          <span>
            {startIdx + 1}–{Math.min(startIdx + pageSize, sorted.length)} of{" "}
            {sorted.length}
          </span>
          <div className="flex items-center gap-1">
            <PaginationBtn
              disabled={safePage <= 1}
              onClick={() => setCurrentPage(1)}
              label={<ChevronsLeft className="h-3.5 w-3.5" />}
            />
            <PaginationBtn
              disabled={safePage <= 1}
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              label={<ChevronLeft className="h-3.5 w-3.5" />}
            />
            {pageNumbers(safePage, totalPages).map((p) =>
              p === "..." ? (
                <span key={`dots-${p}`} className="px-1 text-gray-300">
                  ...
                </span>
              ) : (
                <button
                  key={p}
                  onClick={() => setCurrentPage(p as number)}
                  className={`rounded px-2 py-1 ${
                    p === safePage
                      ? "bg-blue-600 text-white"
                      : "text-gray-600 hover:bg-gray-100"
                  }`}
                >
                  {p}
                </button>
              ),
            )}
            <PaginationBtn
              disabled={safePage >= totalPages}
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              label={<ChevronRight className="h-3.5 w-3.5" />}
            />
            <PaginationBtn
              disabled={safePage >= totalPages}
              onClick={() => setCurrentPage(totalPages)}
              label={<ChevronsRight className="h-3.5 w-3.5" />}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function TableRow<T>({
  row,
  columns,
  alignClass,
  onClick,
  isExpanded,
  expandedContent,
  colCount,
}: {
  row: T;
  columns: DataTableColumn<T>[];
  alignClass: (a?: "left" | "right" | "center") => string;
  onClick?: () => void;
  isExpanded: boolean;
  expandedContent: ReactNode;
  colCount: number;
}) {
  return (
    <>
      <tr
        className={`border-b border-gray-100 last:border-0 ${onClick ? "cursor-pointer hover:bg-gray-50" : ""} ${isExpanded ? "bg-blue-50/30" : ""}`}
        onClick={onClick}
      >
        {columns.map((col) => (
          <td
            key={col.key}
            className={`px-3 py-2.5 ${alignClass(col.align)}`}
          >
            {col.render ? col.render(row) : String(col.accessor(row) ?? "—")}
          </td>
        ))}
      </tr>
      {expandedContent && (
        <tr>
          <td colSpan={colCount} className="bg-gray-50/50 px-4 py-3">
            {expandedContent}
          </td>
        </tr>
      )}
    </>
  );
}

function PaginationBtn({
  disabled,
  onClick,
  label,
}: {
  disabled: boolean;
  onClick: () => void;
  label: ReactNode;
}) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className="rounded p-1 text-gray-500 hover:bg-gray-100 disabled:cursor-not-allowed disabled:text-gray-300"
    >
      {label}
    </button>
  );
}

function ColumnFilterCell({
  config,
  value,
  onChange,
}: {
  config: ColumnFilterConfig;
  value: string;
  onChange: (v: string) => void;
}) {
  const placeholder = config.placeholder ?? "All";
  const options = config.options ?? [];

  if (config.type === "select") {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full max-w-[110px] rounded border border-gray-200 bg-white px-1.5 py-1 text-[11px] focus:border-blue-400 focus:outline-none ${value ? "text-blue-700 font-medium border-blue-300 bg-blue-50" : "text-gray-400"}`}
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }

  if (config.type === "multiselect") {
    const selected = new Set(value ? value.split(",") : []);
    const toggle = (val: string) => {
      const next = new Set(selected);
      if (next.has(val)) next.delete(val);
      else next.add(val);
      onChange(Array.from(next).join(","));
    };
    return (
      <div className="flex flex-wrap gap-0.5">
        {options.map((o) => {
          const active = selected.has(o.value);
          return (
            <button
              key={o.value}
              type="button"
              onClick={() => toggle(o.value)}
              className={`rounded px-1.5 py-0.5 text-[10px] font-medium border transition-colors ${
                active
                  ? "bg-blue-100 text-blue-700 border-blue-300"
                  : "bg-white text-gray-400 border-gray-200 hover:border-gray-300 hover:text-gray-500"
              }`}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    );
  }

  if (config.type === "min") {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full max-w-[110px] rounded border border-gray-200 bg-white px-1.5 py-1 text-[11px] focus:border-blue-400 focus:outline-none ${value ? "text-blue-700 font-medium border-blue-300 bg-blue-50" : "text-gray-400"}`}
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            ≥ {o.label}
          </option>
        ))}
      </select>
    );
  }

  if (config.type === "max") {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full max-w-[110px] rounded border border-gray-200 bg-white px-1.5 py-1 text-[11px] focus:border-blue-400 focus:outline-none ${value ? "text-blue-700 font-medium border-blue-300 bg-blue-50" : "text-gray-400"}`}
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            ≤ {o.label}
          </option>
        ))}
      </select>
    );
  }

  if (config.type === "range") {
    const [curMin, curMax] = (value || ":").split(":");
    const minOpts = config.minOptions ?? config.options ?? [];
    const maxOpts = config.maxOptions ?? config.options ?? [];
    const activeStyle = "text-blue-700 font-medium border-blue-300 bg-blue-50";
    const inactiveStyle = "text-gray-400";
    const updateRange = (newMin: string, newMax: string) => {
      if (!newMin && !newMax) onChange("");
      else onChange(`${newMin}:${newMax}`);
    };
    return (
      <div className="flex gap-0.5">
        <select
          value={curMin}
          onChange={(e) => updateRange(e.target.value, curMax)}
          className={`w-full max-w-[64px] rounded-l border border-gray-200 bg-white px-1 py-1 text-[11px] focus:border-blue-400 focus:outline-none ${curMin ? activeStyle : inactiveStyle}`}
        >
          <option value="">Min</option>
          {minOpts.map((o) => (
            <option key={o.value} value={o.value}>≥{o.label}</option>
          ))}
        </select>
        <select
          value={curMax}
          onChange={(e) => updateRange(curMin, e.target.value)}
          className={`w-full max-w-[64px] rounded-r border border-l-0 border-gray-200 bg-white px-1 py-1 text-[11px] focus:border-blue-400 focus:outline-none ${curMax ? activeStyle : inactiveStyle}`}
        >
          <option value="">Max</option>
          {maxOpts.map((o) => (
            <option key={o.value} value={o.value}>≤{o.label}</option>
          ))}
        </select>
      </div>
    );
  }

  if (config.type === "boolean") {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full max-w-[80px] rounded border border-gray-200 bg-white px-1.5 py-1 text-[11px] focus:border-blue-400 focus:outline-none ${value ? "text-blue-700 font-medium border-blue-300 bg-blue-50" : "text-gray-400"}`}
      >
        <option value="">{placeholder}</option>
        <option value="true">{options[0]?.label ?? "Yes"}</option>
        <option value="false">{options[1]?.label ?? "No"}</option>
      </select>
    );
  }

  return null;
}

function pageNumbers(
  current: number,
  total: number,
): (number | "...")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages: (number | "...")[] = [1];
  if (current > 3) pages.push("...");
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  for (let i = start; i <= end; i++) pages.push(i);
  if (current < total - 2) pages.push("...");
  pages.push(total);
  return pages;
}
