const eur = new Intl.NumberFormat("en-IE", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
});

const eurSigned = new Intl.NumberFormat("en-IE", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
  signDisplay: "always",
});

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return eur.format(value);
}

export function moneySigned(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return eurSigned.format(value);
}

export function compactDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  return d.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function dateOnly(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value).getTime();
  const now = Date.now();
  const diffMs = now - d;
  const abs = Math.abs(diffMs);
  const units: [string, number][] = [
    ["y", 1000 * 60 * 60 * 24 * 365],
    ["mo", 1000 * 60 * 60 * 24 * 30],
    ["d", 1000 * 60 * 60 * 24],
    ["h", 1000 * 60 * 60],
    ["m", 1000 * 60],
  ];
  for (const [label, ms] of units) {
    if (abs >= ms) {
      const n = Math.floor(abs / ms);
      return diffMs >= 0 ? `${n}${label} ago` : `in ${n}${label}`;
    }
  }
  return "just now";
}
