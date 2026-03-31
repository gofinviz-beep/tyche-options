import { useState, useMemo, type ReactNode } from "react";
import { ChevronUp, ChevronDown, Search, ChevronsLeft, ChevronsRight, ChevronLeft, ChevronRight } from "lucide-react";

export interface DataTableColumn<T> {
  key: string;
  header: string;
  accessor: (row: T) => string | number | boolean | null | undefined;
  sortable?: boolean;
  align?: "left" | "right" | "center";
  render?: (row: T) => ReactNode;
  width?: string;
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

  const filtered = useMemo(() => {
    if (!search.trim()) return data;
    const q = search.trim().toLowerCase();
    return data.filter((row) => {
      const field = searchField ? searchField(row) : "";
      return field.toLowerCase().includes(q);
    });
  }, [data, search, searchField]);

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
