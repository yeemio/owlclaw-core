export function fmtNum(v: number): string {
  return new Intl.NumberFormat().format(v);
}

export function fmtPercent(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

export function fmtCurrency(v: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(v);
}
