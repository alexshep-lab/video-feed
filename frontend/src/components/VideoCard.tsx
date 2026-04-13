import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { VideoItem } from "../api/client";
import { formatDuration, formatFileSize } from "../utils/format";

const FRAME_COUNT = 8;
const FRAME_INTERVAL_MS = 500;

type VideoCardProps = {
  video: VideoItem;
  showPath?: boolean;
  showExtraMeta?: boolean;
  /** Optional query string (without `?`) appended to the watch link. Used by
   * the review flow on HomePage to propagate the current filter so WatchPage
   * can auto-advance to the next matching video. */
  linkQuery?: string;
};

export default function VideoCard({
  video,
  showPath = false,
  showExtraMeta = false,
  linkQuery,
}: VideoCardProps) {
  const [frameIndex, setFrameIndex] = useState(0);
  const [hovered, setHovered] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function handleMouseEnter() {
    setHovered(true);
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
    }
    let nextIndex = 0;
    intervalRef.current = setInterval(() => {
      nextIndex = (nextIndex + 1) % FRAME_COUNT;
      setFrameIndex(nextIndex);
    }, FRAME_INTERVAL_MS);
  }

  function handleMouseLeave() {
    setHovered(false);
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    setFrameIndex(0);
  }

  const previewSrc = `${video.preview_frame_template_url}/${frameIndex}`;

  return (
    <Link
      to={`/watch/${video.id}${linkQuery ? `?${linkQuery}` : ""}`}
      className="group overflow-hidden rounded-3xl border border-white/10 bg-panel/70 shadow-card transition hover:-translate-y-1 hover:border-accent/40"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <div className="relative aspect-video bg-[linear-gradient(135deg,_rgba(255,122,24,0.18),_rgba(255,255,255,0.04))]">
        <img
          src={video.thumbnail_url}
          alt=""
          className="absolute inset-0 h-full w-full object-cover"
          onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
        />
        {hovered && (
          <img
            src={previewSrc}
            alt=""
            className="absolute inset-0 h-full w-full object-cover transition-opacity duration-200 opacity-100"
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
          />
        )}
        {video.favorite && (
          <div className="absolute left-3 top-3 text-accent text-sm">★</div>
        )}
        {!video.confirmed && (
          <div className="absolute left-3 bottom-3 rounded bg-yellow-500/80 px-1.5 py-0.5 text-xs text-black">
            unconfirmed
          </div>
        )}
        <div className="absolute bottom-3 right-3 rounded-full bg-black/65 px-3 py-1 text-xs text-white">
          {formatDuration(video.duration)}
        </div>
      </div>
      <div className="space-y-2 p-4">
        <h2 className="line-clamp-2 text-base font-medium text-white group-hover:text-accentSoft">
          {video.title}
        </h2>
        {showPath ? (
          <div className="truncate text-xs text-white/35" title={video.original_path}>
            {video.original_path}
          </div>
        ) : null}
        <div className="flex items-center justify-between text-sm text-white/55">
          <span>{video.codec ?? "unknown"}</span>
          <span>{formatFileSize(video.file_size)}</span>
        </div>
        {showExtraMeta ? (
          <div className="flex items-center justify-between text-xs text-white/35">
            <span>{formatDuration(video.duration)}</span>
            <span>{video.width && video.height ? `${video.width}x${video.height}` : "unknown size"}</span>
          </div>
        ) : null}
        {video.view_count > 0 && (
          <div className="text-xs text-white/35">
            {video.view_count} view{video.view_count !== 1 ? "s" : ""}
          </div>
        )}
      </div>
    </Link>
  );
}
