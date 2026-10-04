const MINUS = "−";

/** 146.472 -> "2:26.472" */
export function lapTime(s: number | null | undefined): string {
  if (s == null) return "—";
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return m > 0 ? `${m}:${rest.toFixed(3).padStart(6, "0")}` : rest.toFixed(3);
}

/** Signed number with a real minus sign: +0.532, −2.0. */
export function signed(v: number | null | undefined, places = 3): string {
  if (v == null) return "—";
  const s = Math.abs(v).toFixed(places);
  if (Number(s) === 0) return s;
  return (v > 0 ? "+" : MINUS) + s;
}

/** "20250723-202727" -> "23 Jul 2025, 20:27"; other ids pass through. */
export function sessionDate(id: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(id);
  if (!m) return id;
  const [, y, mo, d, h, mi] = m;
  const month = new Date(Number(y), Number(mo) - 1, 1).toLocaleString("en-GB", { month: "short" });
  return `${Number(d)} ${month} ${y}, ${h}:${mi}`;
}

export function metres(m: number): string {
  return `${Math.round(m).toLocaleString("en-US")} m`;
}

/** Time gained (blue) or lost (orange) in a corner, with neutral grey for |d| < 0.05 s. */
export function deltaColor(d: number | null | undefined): string {
  if (d == null) return "var(--muted)";
  if (d >= 0.2) return "var(--loss-strong)";
  if (d >= 0.05) return "var(--loss)";
  if (d <= -0.2) return "var(--gain-strong)";
  if (d <= -0.05) return "var(--gain)";
  return "var(--neutral)";
}
