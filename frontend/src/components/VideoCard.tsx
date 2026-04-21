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
  /** Set of tag names currently active in the library filter. Used to
   * highlight the chip and flip the toggle direction. */
  activeTags?: Set<string>;
  /** Called when a tag chip is clicked. When provided, the chip becomes a
   * button that toggles the tag in the parent filter instead of letting
   * the click fall through to the watch link. */
  onTagClick?: (tag: string) => void;
};

export default function VideoCard({
  video,
  showPath = false,
  showExtraMeta = false,
  linkQuery,
  activeTags,
  onTagClick,
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
        {video.tag_list && video.tag_list.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {video.tag_list.slice(0, 3).map((tag) => {
              const active = activeTags?.has(tag);
              const base = "truncate rounded px-1.5 py-0.5 text-[10px]";
              const cls = active
                ? `${base} bg-accent/30 text-accent`
                : `${base} bg-white/5 text-white/60`;
              if (onTagClick) {
                return (
                  <button
                    key={tag}
                    onClick={(e) => {
                      // Don't navigate to the watch page when toggling a tag.
                      e.preventDefault();
                      e.stopPropagation();
                      onTagClick(tag);
                    }}
                    className={`${cls} cursor-pointer transition hover:bg-accent/20 hover:text-accent`}
                    title={active ? `Remove "${tag}" from filter` : `Filter by "${tag}"`}
                  >
                    {tag}
                  </button>
                );
              }
              return (
                <span key={tag} className={cls} title={tag}>
                  {tag}
                </span>
              );
            })}
            {video.tag_list.length > 3 && (
              <span
                className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-white/40"
                title={video.tag_list.slice(3).join(", ")}
              >
                +{video.tag_list.length - 3}
              </span>
            )}
          </div>
        )}
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
