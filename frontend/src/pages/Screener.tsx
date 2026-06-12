/**
 * Stock Screener — filter A-share stocks by fundamental metrics.
 *
 * Single page with filter panel + paginated results table.
 * Data source: AKShare via backend GET /screener/ashare.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Filter, Loader2, RotateCcw, ChevronLeft, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { api, type ScreenerStock, type ScreenerResponse } from "@/lib/api";

/* ---------- Types ---------- */

interface FilterState {
  market: string;
  mcap_min: string;
  mcap_max: string;
  pe_min: string;
  pe_max: string;
  pb_min: string;
  pb_max: string;
  volume_min: string;
  exclude_st: boolean;
  sort_by: string;
  sort_order: string;
}

const DEFAULT_FILTERS: FilterState = {
  market: "all",
  mcap_min: "",
  mcap_max: "",
  pe_min: "",
  pe_max: "",
  pb_min: "",
  pb_max: "",
  volume_min: "",
  exclude_st: true,
  sort_by: "market_cap",
  sort_order: "desc",
};

const SORT_OPTIONS = [
  "market_cap",
  "pe",
  "pb",
  "volume",
  "turnover_rate",
  "pct_change",
] as const;

/* ---------- Helpers ---------- */

function fmt(val: number | null, decimals = 2): string {
  if (val === null || val === undefined) return "—";
  return val.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtMarketCap(val: number | null): string {
  if (val === null || val === undefined) return "—";
  if (val >= 10000) return fmt(val / 10000, 1) + "万";
  return fmt(val, 1);
}

/* ---------- Components ---------- */

function FilterPanel({
  filters,
  onChange,
  onScreen,
  onReset,
  loading,
}: {
  filters: FilterState;
  onChange: (patch: Partial<FilterState>) => void;
  onScreen: () => void;
  onReset: () => void;
  loading: boolean;
}) {
  const { t } = useTranslation();

  return (
    <div className="rounded-xl border bg-card p-4 space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {/* Market board */}
        <label className="space-y-1">
          <span className="text-xs text-muted-foreground">{t("screener.market")}</span>
          <select
            className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
            value={filters.market}
            onChange={(e) => onChange({ market: e.target.value })}
          >
            <option value="all">{t("screener.marketAll")}</option>
            <option value="main">{t("screener.marketMain")}</option>
            <option value="gem">{t("screener.marketGem")}</option>
            <option value="star">{t("screener.marketStar")}</option>
            <option value="beijing">{t("screener.marketBeijing")}</option>
          </select>
        </label>

        {/* Market cap range */}
        <label className="space-y-1">
          <span className="text-xs text-muted-foreground">{t("screener.marketCapRange")}</span>
          <div className="flex items-center gap-1">
            <input
              type="number"
              placeholder={t("screener.marketCapMin")}
              className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              value={filters.mcap_min}
              onChange={(e) => onChange({ mcap_min: e.target.value })}
            />
            <span className="text-muted-foreground">-</span>
            <input
              type="number"
              placeholder={t("screener.marketCapMax")}
              className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              value={filters.mcap_max}
              onChange={(e) => onChange({ mcap_max: e.target.value })}
            />
          </div>
        </label>

        {/* PE range */}
        <label className="space-y-1">
          <span className="text-xs text-muted-foreground">{t("screener.peRange")}</span>
          <div className="flex items-center gap-1">
            <input
              type="number"
              placeholder={t("screener.peMin")}
              className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              value={filters.pe_min}
              onChange={(e) => onChange({ pe_min: e.target.value })}
            />
            <span className="text-muted-foreground">-</span>
            <input
              type="number"
              placeholder={t("screener.peMax")}
              className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              value={filters.pe_max}
              onChange={(e) => onChange({ pe_max: e.target.value })}
            />
          </div>
        </label>

        {/* PB range */}
        <label className="space-y-1">
          <span className="text-xs text-muted-foreground">{t("screener.pbRange")}</span>
          <div className="flex items-center gap-1">
            <input
              type="number"
              placeholder={t("screener.pbMin")}
              className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              value={filters.pb_min}
              onChange={(e) => onChange({ pb_min: e.target.value })}
            />
            <span className="text-muted-foreground">-</span>
            <input
              type="number"
              placeholder={t("screener.pbMax")}
              className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              value={filters.pb_max}
              onChange={(e) => onChange({ pb_max: e.target.value })}
            />
          </div>
        </label>

        {/* Volume min */}
        <label className="space-y-1">
          <span className="text-xs text-muted-foreground">{t("screener.volumeMin")}</span>
          <input
            type="number"
            className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
            value={filters.volume_min}
            onChange={(e) => onChange({ volume_min: e.target.value })}
          />
        </label>

        {/* Sort */}
        <label className="space-y-1">
          <span className="text-xs text-muted-foreground">{t("screener.sortBy")}</span>
          <div className="flex gap-1">
            <select
              className="flex-1 rounded-md border bg-background px-2 py-1.5 text-sm"
              value={filters.sort_by}
              onChange={(e) => onChange({ sort_by: e.target.value })}
            >
              {SORT_OPTIONS.map((key) => (
                <option key={key} value={key}>
                  {t(`screener.sort${key.charAt(0).toUpperCase()}${key.slice(1)}`)}
                </option>
              ))}
            </select>
            <button
              className="rounded-md border px-2 py-1.5 text-sm hover:bg-muted/30"
              onClick={() =>
                onChange({ sort_order: filters.sort_order === "desc" ? "asc" : "desc" })
              }
              title={filters.sort_order === "desc" ? t("screener.sortDesc") : t("screener.sortAsc")}
            >
              {filters.sort_order === "desc" ? "↓" : "↑"}
            </button>
          </div>
        </label>

        {/* Exclude ST checkbox */}
        <label className="flex items-center gap-2 self-end py-1.5">
          <input
            type="checkbox"
            checked={filters.exclude_st}
            onChange={(e) => onChange({ exclude_st: e.target.checked })}
            className="rounded"
          />
          <span className="text-sm">{t("screener.excludeST")}</span>
        </label>
      </div>

      {/* Action buttons */}
      <div className="flex gap-2">
        <button
          onClick={onScreen}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Filter className="h-4 w-4" />
          )}
          {loading ? t("screener.screening") : t("screener.runScreener")}
        </button>
        <button
          onClick={onReset}
          className="inline-flex items-center gap-1.5 rounded-lg border px-4 py-2 text-sm hover:bg-muted/30"
        >
          <RotateCcw className="h-4 w-4" />
          {t("screener.resetFilters")}
        </button>
      </div>
    </div>
  );
}

function ResultsTable({
  stocks,
  sort_by,
  sort_order,
  onSort,
}: {
  stocks: ScreenerStock[];
  sort_by: string;
  sort_order: string;
  onSort: (col: string) => void;
}) {
  const { t } = useTranslation();

  const columns: { key: string; label: string; align?: string; render: (s: ScreenerStock) => React.ReactNode }[] = [
    { key: "code", label: t("screener.stockCode"), render: (s) => <span className="font-mono text-xs">{s.code}</span> },
    { key: "name", label: t("screener.stockName"), render: (s) => s.name },
    { key: "close", label: t("screener.close"), align: "right", render: (s) => fmt(s.close) },
    {
      key: "pct_change",
      label: t("screener.changePct"),
      align: "right",
      render: (s) => {
        if (s.pct_change === null) return "—";
        const positive = s.pct_change >= 0;
        return (
          <span className={cn(positive ? "text-red-600 dark:text-red-400" : "text-green-600 dark:text-green-400")}>
            {positive ? "+" : ""}{fmt(s.pct_change)}%
          </span>
        );
      },
    },
    { key: "market_cap", label: t("screener.marketCap"), align: "right", render: (s) => fmtMarketCap(s.market_cap) },
    { key: "pe", label: t("screener.pe"), align: "right", render: (s) => fmt(s.pe) },
    { key: "pb", label: t("screener.pb"), align: "right", render: (s) => fmt(s.pb) },
    { key: "turnover_rate", label: t("screener.turnover"), align: "right", render: (s) => s.turnover_rate !== null ? fmt(s.turnover_rate) + "%" : "—" },
  ];

  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/30 text-left">
            {columns.map((col) => (
              <th
                key={col.key}
                className={cn("px-3 py-2 font-medium cursor-pointer select-none hover:bg-muted/50", col.align === "right" && "text-right")}
                onClick={() => onSort(col.key)}
              >
                {col.label}
                {sort_by === col.key && (
                  <span className="ml-1">{sort_order === "desc" ? "↓" : "↑"}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {stocks.map((stock) => (
            <tr key={stock.code} className="border-b last:border-0 hover:bg-muted/10">
              {columns.map((col) => (
                <td key={col.key} className={cn("px-3 py-2", col.align === "right" && "text-right")}>
                  {col.render(stock)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PaginationBar({
  page,
  totalPages,
  onPrev,
  onNext,
}: {
  page: number;
  totalPages: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  const { t } = useTranslation();

  return (
    <div className="flex items-center justify-center gap-4 py-2 text-sm text-muted-foreground">
      <button
        onClick={onPrev}
        disabled={page <= 1}
        className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 hover:bg-muted/30 disabled:opacity-30"
      >
        <ChevronLeft className="h-4 w-4" />
        {t("screener.prev")}
      </button>
      <span>{t("screener.page", { page, total: totalPages })}</span>
      <button
        onClick={onNext}
        disabled={page >= totalPages}
        className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 hover:bg-muted/30 disabled:opacity-30"
      >
        {t("screener.next")}
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}

/* ---------- Page ---------- */

export function Screener() {
  const { t } = useTranslation();
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [results, setResults] = useState<ScreenerStock[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(30);
  const [loading, setLoading] = useState(false);
  const [initialLoad, setInitialLoad] = useState(false);

  const buildParams = useCallback(
    (p: number) => ({
      market: filters.market === "all" ? undefined : filters.market,
      mcap_min: filters.mcap_min ? Number(filters.mcap_min) : undefined,
      mcap_max: filters.mcap_max ? Number(filters.mcap_max) : undefined,
      pe_min: filters.pe_min ? Number(filters.pe_min) : undefined,
      pe_max: filters.pe_max ? Number(filters.pe_max) : undefined,
      pb_min: filters.pb_min ? Number(filters.pb_min) : undefined,
      pb_max: filters.pb_max ? Number(filters.pb_max) : undefined,
      volume_min: filters.volume_min ? Number(filters.volume_min) : undefined,
      exclude_st: filters.exclude_st,
      sort_by: filters.sort_by,
      sort_order: filters.sort_order,
      page: p,
      page_size: pageSize,
    }),
    [filters, pageSize],
  );

  const doScreen = useCallback(
    async (p: number) => {
      setLoading(true);
      try {
        const res: ScreenerResponse = await api.screenAShares(buildParams(p));
        setResults(res.stocks);
        setTotal(res.total);
        setPage(p);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Failed to screen stocks");
      } finally {
        setLoading(false);
      }
    },
    [buildParams],
  );

  // Initial load
  useEffect(() => {
    doScreen(1).then(() => setInitialLoad(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleScreen = () => doScreen(1);

  const handleSort = (col: string) => {
    const newOrder = filters.sort_by === col && filters.sort_order === "desc" ? "asc" : "desc";
    setFilters((f) => ({ ...f, sort_by: col, sort_order: newOrder }));
    // Trigger search with new sort
    const updated = { ...filters, sort_by: col, sort_order: newOrder };
    const params = {
      market: updated.market === "all" ? undefined : updated.market,
      mcap_min: updated.mcap_min ? Number(updated.mcap_min) : undefined,
      mcap_max: updated.mcap_max ? Number(updated.mcap_max) : undefined,
      pe_min: updated.pe_min ? Number(updated.pe_min) : undefined,
      pe_max: updated.pe_max ? Number(updated.pe_max) : undefined,
      pb_min: updated.pb_min ? Number(updated.pb_min) : undefined,
      pb_max: updated.pb_max ? Number(updated.pb_max) : undefined,
      volume_min: updated.volume_min ? Number(updated.volume_min) : undefined,
      exclude_st: updated.exclude_st,
      sort_by: col,
      sort_order: newOrder,
      page: 1,
      page_size: pageSize,
    };
    setLoading(true);
    api
      .screenAShares(params)
      .then((res) => {
        setResults(res.stocks);
        setTotal(res.total);
        setPage(1);
      })
      .catch((err) => toast.error(err instanceof Error ? err.message : "Failed to screen stocks"))
      .finally(() => setLoading(false));
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-4 lg:p-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">{t("screener.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("screener.subtitle")}</p>
      </div>

      {/* Filter panel */}
      <FilterPanel
        filters={filters}
        onChange={(patch) => setFilters((f) => ({ ...f, ...patch }))}
        onScreen={handleScreen}
        onReset={() => {
          setFilters(DEFAULT_FILTERS);
          doScreen(1);
        }}
        loading={loading}
      />

      {/* Status bar */}
      {initialLoad && total > 0 && (
        <div className="text-sm text-muted-foreground">
          {t("screener.total", { count: total })}
        </div>
      )}

      {/* Results */}
      {loading && !results.length ? (
        <div className="flex h-40 items-center justify-center text-muted-foreground">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          {t("screener.loadingStocks")}
        </div>
      ) : results.length > 0 ? (
        <>
          <ResultsTable
            stocks={results}
            sort_by={filters.sort_by}
            sort_order={filters.sort_order}
            onSort={handleSort}
          />
          {totalPages > 1 && (
            <PaginationBar
              page={page}
              totalPages={totalPages}
              onPrev={() => page > 1 && doScreen(page - 1)}
              onNext={() => page < totalPages && doScreen(page + 1)}
            />
          )}
        </>
      ) : initialLoad ? (
        <div className="flex h-40 items-center justify-center text-muted-foreground">
          {t("screener.noResults")}
        </div>
      ) : null}
    </div>
  );
}
