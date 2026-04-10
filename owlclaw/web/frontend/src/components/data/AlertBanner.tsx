import { type OverviewAlert } from "@/hooks/useApi";
import { cn } from "@/lib/utils";

type AlertBannerProps = {
  alerts: OverviewAlert[];
};

const LEVEL_STYLE: Record<OverviewAlert["level"], string> = {
  info: "border-sky-400/40 bg-sky-500/10 text-sky-200",
  warning: "border-amber-400/40 bg-amber-500/10 text-amber-200",
  critical: "border-red-400/40 bg-red-500/10 text-red-200",
};

export function AlertBanner({ alerts }: AlertBannerProps) {
  if (alerts.length === 0) {
    return null;
  }

  return (
    <div className="space-y-2">
      {alerts.map((alert, index) => (
        <div key={`${alert.level}-${index}`} className={cn("rounded-lg border px-4 py-3 text-sm", LEVEL_STYLE[alert.level])}>
          {alert.message}
        </div>
      ))}
    </div>
  );
}
