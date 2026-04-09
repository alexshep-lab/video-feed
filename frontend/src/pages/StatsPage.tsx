import { useEffect, useState } from "react";
import { fetchStats, StatsResponse } from "../api/client";
import { formatDuration, formatFileSize } from "../utils/format";

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-4 space-y-1">
      <p className="text-xs uppercase tracking-wider text-white/40">{label}</p>
      <p className="text-xl font-semibold text-white">{value}</p>
    </div>
  );
}

export default function StatsPage() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchStats().then(setStats).catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <p className="text-red-300">{error}</p>;
  if (!stats) return <p className="text-white/60">Loading...</p>;

  const { overview, most_viewed, recent_history, popular_tags, daily_activity } = stats;

  const maxViews = Math.max(...daily_activity.map((d) => d.views), 1);

  return (
    <div className="space-y-8">
      <h1 className="text-3xl font-semibold text-white">Statistics</h1>

      {/* Overview cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatCard label="Videos" value={String(overview.total_videos)} />
        <StatCard label="Total Size" value={formatFileSize(overview.total_size_bytes)} />
        <StatCard label="Total Duration" value={formatDuration(overview.total_duration_seconds)} />
        <StatCard label="Favorites" value={String(overview.total_favorites)} />
        <StatCard label="Total Views" value={String(overview.total_views)} />
        <StatCard label="Watch Time" value={formatDuration(overview.total_watch_time_seconds)} />
      </div>

      {/* Activity chart */}
      <div className="rounded-2xl border border-white/10 bg-white/5 p-5 space-y-3">
        <h2 className="text-sm font-semibold text-white/70 uppercase tracking-wider">Activity (last 30 days)</h2>
        <div className="flex items-end gap-1 h-24">
          {daily_activity.map((day) => {
            const heightPct = Math.max(4, Math.round((day.views / maxViews) * 100));
            return (
              <div
                key={day.date}
                className="flex-1 bg-accent/50 rounded-t hover:bg-accent transition-colors"
                style={{ height: `${heightPct}%` }}
                title={`${day.date}: ${day.views} views`}
              />
            );
          })}
        </div>
        <div className="flex justify-between text-xs text-white/30">
          {daily_activity.length > 0 && (
            <>
              <span>{daily_activity[0].date}</span>
              <span>{daily_activity[daily_activity.length - 1].date}</span>
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Most viewed */}
        <div className="rounded-2xl border border-white/10 bg-white/5 p-5 space-y-3">
          <h2 className="text-sm font-semibold text-white/70 uppercase tracking-wider">Most Viewed</h2>
          <div className="space-y-2">
            {most_viewed.map((v, i) => (
              <div key={v.id} className="flex items-center gap-3">
                <span className="text-xs text-white/30 w-4">{i + 1}</span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-white truncate">{v.title}</p>
                </div>
                <div className="text-right text-xs text-white/50 shrink-0">
                  <span className="text-accent font-medium">{v.view_count}</span>
                  <span className="text-white/30"> views</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Popular tags */}
        <div className="rounded-2xl border border-white/10 bg-white/5 p-5 space-y-3">
          <h2 className="text-sm font-semibold text-white/70 uppercase tracking-wider">Popular Tags</h2>
          <div className="flex flex-wrap gap-2">
            {popular_tags.map((t) => (
              <span
                key={t.name}
                className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-white/70"
                title={`${t.total_views} views, ${t.video_count} videos`}
              >
                {t.name} <span className="text-white/35">{t.total_views}</span>
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* Recent history */}
      <div className="rounded-2xl border border-white/10 bg-white/5 p-5 space-y-3">
        <h2 className="text-sm font-semibold text-white/70 uppercase tracking-wider">Recent History</h2>
        <div className="space-y-1.5 max-h-72 overflow-y-auto">
          {recent_history.map((h, i) => (
            <div key={i} className="flex items-center gap-3 text-sm">
              <span className="text-white/30 text-xs shrink-0 w-32 truncate">
                {h.watched_at ? new Date(h.watched_at).toLocaleDateString() : "—"}
              </span>
              <span className="flex-1 text-white/70 truncate">{h.title}</span>
              <span className="text-white/30 text-xs shrink-0">{formatDuration(h.watch_duration)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
