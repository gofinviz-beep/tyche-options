/**
 * Print-to-PDF helper.
 *
 * The browser's own print pipeline is used instead of a canvas rasteriser so the
 * exported PDF keeps selectable text and vector charts (Recharts renders SVG).
 * Chrome/Edge/Safari derive the default "Save as PDF" filename from
 * `document.title`, so it is swapped for the duration of the print job.
 */

function sanitizeFilename(name: string): string {
  return name.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "");
}

/**
 * Marks the document while it is laid out for print. Charts measure their
 * container in pixels, so `index.css` uses this class to pin the report to the
 * printable page width before the print snapshot is taken.
 */
const PREPARING_CLASS = "print-preparing";

/** Two frames plus a short settle window is enough for chart re-measure + re-render. */
const RELAYOUT_SETTLE_MS = 120;

function nextFrame(): Promise<void> {
  return new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
}

export interface PrintReportOptions {
  /** Base filename (no extension) offered in the print dialog. */
  filename: string;
}

export async function printReport({ filename }: PrintReportOptions): Promise<void> {
  const previousTitle = document.title;
  const safe = sanitizeFilename(filename);
  if (safe) document.title = safe;
  document.body.classList.add(PREPARING_CLASS);

  let restored = false;
  const restore = () => {
    if (restored) return;
    restored = true;
    document.title = previousTitle;
    document.body.classList.remove(PREPARING_CLASS);
    window.removeEventListener("afterprint", restore);
  };

  window.addEventListener("afterprint", restore);
  // Safari does not always fire `afterprint`; restore on a timer as a backstop.
  window.setTimeout(restore, 60_000);

  try {
    await nextFrame();
    await nextFrame();
    await new Promise((resolve) => window.setTimeout(resolve, RELAYOUT_SETTLE_MS));
    window.print();
  } catch {
    restore();
  }
}

/** `2026-08-13` — used to date-stamp exported filenames. */
export function isoToday(): string {
  return new Date().toISOString().slice(0, 10);
}
