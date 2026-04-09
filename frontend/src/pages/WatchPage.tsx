import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import Hls from "hls.js";
import {
  API_BASE,
  fetchVideo,
  fetchRecommendations,
  updateVideo,
  deleteVideo,
  startTranscode,
  startCompress,
  recordWatchEvent,
  updateWatchTime,
  type VideoDetail,
  type VideoItem,
} from "../api/client";
import { formatDuration, formatFileSize } from "../utils/format";

const NON_NATIVE_EXTENSIONS = [".wmv", ".avi"];

function needsTranscode(filename: string): boolean {
  const ext = filename.toLowerCase().slice(filename.lastIndexOf("."));
  return NON_NATIVE_EXTENSIONS.includes(ext);
}

function formatWatchTime(seconds: number): string {
  if (!seconds) return "--:--";
  return formatDuration(seconds);
}

function rawMimeType(filename: string): string {
  const ext = filename.toLowerCase().slice(filename.lastIndexOf("."));
  if (ext === ".webm") return "video/webm";
  if (ext === ".mov") return "video/quicktime";
  return "video/mp4";
}

export default function WatchPage() {
  const { videoId } = useParams();
  const navigate = useNavigate();
  const [video, setVideo] = useState<VideoDetail | null>(null);
  const [recommendations, setRecommendations] = useState<VideoItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [playerError, setPlayerError] = useState<string | null>(null);
  const [tagsInput, setTagsInput] = useState("");
  const [saving, setSaving] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const watchTimeRef = useRef(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const framePaletteUrl = videoId ? `${API_BASE}/stream/${videoId}/contact-sheet` : "";

  const load = (id: string) =>
    fetchVideo(id).then((v) => {
      setVideo(v);
      setTagsInput(v.tag_list.join(", "));
      setError(null);
      return v;
    });

  // Initial load
  useEffect(() => {
    if (!videoId) return;
    load(videoId).catch((e: Error) => setError(e.message));
  }, [videoId]);

  // Record watch event on mount
  useEffect(() => {
    if (!videoId) return;
    recordWatchEvent(videoId, 0).catch(() => null);
  }, [videoId]);

  // Load recommendations
  useEffect(() => {
    if (!videoId) return;
    fetchRecommendations(videoId, 8).then(setRecommendations).catch(() => null);
  }, [videoId]);

  // Track watch time and report every 10s
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const onTime = () => { watchTimeRef.current = video.currentTime; };
    video.addEventListener("timeupdate", onTime);
    const interval = setInterval(() => {
      if (videoId && watchTimeRef.current > 0) {
        updateWatchTime(videoId, watchTimeRef.current).catch(() => null);
      }
    }, 10000);
    return () => { video.removeEventListener("timeupdate", onTime); clearInterval(interval); };
  }, [video, videoId]);

  // Auto-start transcoding for non-native formats
  useEffect(() => {
    if (!video || !videoId) return;
    if (needsTranscode(video.original_filename) && video.transcode_status === "pending") {
      startTranscode(videoId).then(() => load(videoId)).catch(() => null);
    }
  }, [video?.id, video?.transcode_status, video?.original_filename, videoId]);

  // Poll while transcoding
  useEffect(() => {
    if (!videoId) return;
    if (video?.transcode_status === "processing") {
      pollRef.current = setInterval(() => {
        fetchVideo(videoId).then((updated) => {
          setVideo(updated);
          if (updated.transcode_status !== "processing" && pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }).catch(() => null);
      }, 2000);
    } else {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [video?.transcode_status, videoId]);

  const playerMode = useMemo(() => {
    if (!video) return "none" as const;
    if (!needsTranscode(video.original_filename) && video.raw_stream_url) return "raw" as const;
    if (video.transcode_status === "completed" && video.hls_stream_url) return "hls" as const;
    if (video.transcode_status === "processing") return "processing" as const;
    if (video.transcode_status === "failed") return "failed" as const;
    return "preparing" as const;
  }, [video]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el || !video) return;

    setPlayerError(null);
    watchTimeRef.current = 0;
    hlsRef.current?.destroy();
    hlsRef.current = null;

    const onError = () => {
      const mediaError = el.error;
      if (!mediaError) {
        setPlayerError("Player could not start the file.");
        return;
      }

      const message = {
        1: "Playback was aborted.",
        2: "Network error while loading video.",
        3: "The browser could not decode this stream.",
        4: "This video format is not supported by the browser.",
      }[mediaError.code] ?? "Unknown player error.";

      setPlayerError(message);
    };
    const tryAutoplay = () => {
      el.play().catch(() => null);
    };

    el.pause();
    el.removeAttribute("src");
    el.load();
    el.addEventListener("error", onError);
    el.addEventListener("loadeddata", tryAutoplay);

    if (playerMode === "hls" && video.hls_stream_url) {
      if (Hls.isSupported()) {
        const hls = new Hls();
        hls.on(Hls.Events.ERROR, (_event, data) => {
          if (data.fatal) {
            setPlayerError(`HLS error: ${data.details}`);
          }
        });
        hls.loadSource(video.hls_stream_url);
        hls.attachMedia(el);
        hlsRef.current = hls;
      } else if (el.canPlayType("application/vnd.apple.mpegurl")) {
        el.src = video.hls_stream_url;
        el.load();
      } else {
        setPlayerError("HLS is not supported in this browser.");
      }
    } else if (playerMode === "raw" && video.raw_stream_url) {
      el.src = video.raw_stream_url;
      el.load();
    }

    return () => {
      el.removeEventListener("error", onError);
      el.removeEventListener("loadeddata", tryAutoplay);
      hlsRef.current?.destroy();
      hlsRef.current = null;
    };
  }, [playerMode, video]);

  if (error) return <p className="text-red-300">{error}</p>;
  if (!video) return <p className="text-white/60">Loading player...</p>;

  const isNativePlayback = !needsTranscode(video.original_filename);
  const isTranscoding = video.transcode_status === "processing";

  async function toggleFavorite() {
    if (!video || !videoId) return;
    const updated = await updateVideo(videoId, { favorite: !video.favorite });
    setVideo(updated);
  }

  async function toggleConfirm() {
    if (!video || !videoId) return;
    const updated = await updateVideo(videoId, { confirmed: !video.confirmed });
    setVideo(updated);
  }

  async function saveTags() {
    if (!videoId) return;
    setSaving(true);
    const tags = tagsInput.split(",").map((t) => t.trim()).filter(Boolean);
    const updated = await updateVideo(videoId, { tag_list: tags });
    setVideo(updated);
    setSaving(false);
  }

  async function handleCompress() {
    if (!videoId) return;
    await startCompress(videoId);
    load(videoId);
  }

  async function handleTranscode() {
    if (!videoId) return;
    setPlayerError(null);
    await startTranscode(videoId);
    load(videoId);
  }

  async function handleTrash() {
    if (!videoId) return;
    if (!confirm("Move to trash?")) return;
    await deleteVideo(videoId, false);
    navigate("/");
  }

  async function handleHardDelete() {
    if (!videoId) return;
    if (!confirm("Permanently delete file from disk?")) return;
    await deleteVideo(videoId, true);
    navigate("/");
  }

  const btnCls = "rounded-xl border border-white/15 bg-white/5 px-4 py-1.5 text-sm text-white/80 hover:bg-white/10 transition";
  const dangerCls = "rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-1.5 text-sm text-red-300 hover:bg-red-500/20 transition";

  return (
    <section className="space-y-6">
      <div className="overflow-hidden rounded-[2rem] border border-white/10 bg-black shadow-card">
        {playerMode === "raw" || playerMode === "hls" ? (
          <div className="space-y-3 p-3">
            <video
              key={`${video.id}-${playerMode}-${video.raw_stream_url}-${video.hls_stream_url ?? ""}`}
              ref={videoRef}
              controls
              autoPlay
              playsInline
              preload="metadata"
              className="max-h-[75vh] w-full rounded-[1.5rem] bg-black"
            >
              {playerMode === "raw" && video.raw_stream_url ? (
                <source src={video.raw_stream_url} type={rawMimeType(video.original_filename)} />
              ) : null}
            </video>
            {playerError ? (
              <div className="rounded-2xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                {playerError}
                {!isNativePlayback && playerMode !== "hls" ? (
                  <button
                    onClick={handleTranscode}
                    className="ml-3 rounded-lg border border-red-400/40 px-3 py-1 text-xs text-red-100 hover:bg-red-500/10"
                  >
                    Try HLS fallback
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : isTranscoding && !isNativePlayback ? (
          <div className="flex flex-col items-center justify-center gap-4 p-16 text-white/60">
            <p className="text-lg">Transcoding... {Math.round(video.transcode_progress)}%</p>
            <div className="h-2 w-64 overflow-hidden rounded-full bg-white/10">
              <div className="h-full rounded-full bg-white/60 transition-all" style={{ width: `${video.transcode_progress}%` }} />
            </div>
          </div>
        ) : playerMode === "failed" && !isNativePlayback ? (
          <div className="flex flex-col items-center justify-center gap-4 p-16 text-red-300">
            <div>Transcoding failed.</div>
            <button onClick={handleTranscode} className={btnCls}>Retry Transcode</button>
          </div>
        ) : !isNativePlayback ? (
          <div className="flex flex-col items-center justify-center gap-4 p-16 text-white/40">
            <div>Preparing video...</div>
            <button onClick={handleTranscode} className={btnCls}>Generate Stream</button>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center gap-4 p-16 text-red-300">
            <div>Native stream is unavailable.</div>
          </div>
        )}
      </div>

      <div className="rounded-[2rem] border border-white/10 bg-white/5 p-6 space-y-4">
        <div className="flex items-start gap-3">
          <button
            onClick={toggleFavorite}
            className={`text-2xl mt-0.5 transition ${video.favorite ? "text-accent" : "text-white/30 hover:text-white/60"}`}
            title="Favorite"
          >
            {video.favorite ? "★" : "☆"}
          </button>
          <div className="flex-1">
            <h1 className="text-2xl font-semibold text-white">{video.title}</h1>
            <p className="text-sm text-white/50">{video.original_filename}</p>
            {video.original_path && (
              <p className="text-xs text-white/30 mt-0.5 break-all">{video.original_path}</p>
            )}
          </div>
          <div className="text-right text-sm text-white/50 space-y-1">
            <div>Duration: {formatDuration(video.duration)}</div>
            <div>Size: {formatFileSize(video.file_size)}</div>
            <div>Resolution: {video.width ?? "?"}x{video.height ?? "?"}</div>
            <div>Codec: {video.codec ?? "unknown"}</div>
          </div>
        </div>

        <div className="text-sm text-white/45">
          Views: {video.view_count} - Total watched: {formatWatchTime(video.total_watch_time)}
        </div>

        <div className="flex flex-wrap gap-2">
          <a
            href={`${API_BASE}/stream/${video.id}/contact-sheet`}
            target="_blank"
            rel="noreferrer"
            className={btnCls}
          >
            Contact Sheet
          </a>
          <button
            onClick={toggleConfirm}
            className={
              video.confirmed
                ? "rounded-xl border border-green-500/40 bg-green-500/10 px-4 py-1.5 text-sm text-green-300 hover:bg-green-500/20 transition"
                : btnCls
            }
          >
            {video.confirmed ? "Confirmed" : "Confirm"}
          </button>
          <button
            onClick={handleCompress}
            disabled={video.compress_status === "processing"}
            className={btnCls}
          >
            {video.compress_status === "processing"
              ? `Compressing ${Math.round(video.compress_progress)}%`
              : "Compress"}
          </button>
          {!isNativePlayback ? (
            <button onClick={handleTranscode} disabled={isTranscoding} className={btnCls}>
              {isTranscoding ? "Transcoding..." : "Generate Stream"}
            </button>
          ) : null}
          <button onClick={handleTrash} className={dangerCls}>Trash</button>
          <button onClick={handleHardDelete} className={dangerCls}>Hard Delete</button>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-white/45">Tags (comma-separated):</label>
          <input
            type="text"
            value={tagsInput}
            onChange={(e) => setTagsInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") saveTags(); }}
            className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-sm text-white focus:outline-none focus:border-accent/60"
          />
          <p className="text-xs text-white/30">Press Enter to save tags{saving ? " - saving..." : ""}</p>
        </div>
      </div>

      <div className="rounded-[2rem] border border-white/10 bg-white/5 p-6 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-white/75">Frame Palette</h2>
          <span className="text-xs text-white/35">16 frames</span>
        </div>

        {video.confirmed ? (
          <details className="rounded-2xl border border-white/10 bg-black/20 p-3">
            <summary className="cursor-pointer list-none text-sm text-white/65">
              Show palette for confirmed video
            </summary>
            <div className="mt-3 overflow-hidden rounded-2xl border border-white/10 bg-black">
              <img
                src={framePaletteUrl}
                alt="Video frame palette"
                className="w-full object-cover"
              />
            </div>
          </details>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-white/10 bg-black">
            <img
              src={framePaletteUrl}
              alt="Video frame palette"
              className="w-full object-cover"
            />
          </div>
        )}
      </div>

      {recommendations.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-lg font-semibold text-white/70">Related</h2>
          <div className="flex gap-3 overflow-x-auto pb-2">
            {recommendations.map((rec) => (
              <button
                key={rec.id}
                onClick={() => navigate(`/watch/${rec.id}`)}
                className="shrink-0 w-44 text-left rounded-2xl border border-white/10 bg-white/5 overflow-hidden hover:border-accent/40 hover:-translate-y-0.5 transition"
              >
                <div className="relative aspect-video bg-white/5">
                  <img
                    src={rec.thumbnail_url}
                    alt=""
                    className="absolute inset-0 h-full w-full object-cover"
                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
                  />
                  <div className="absolute bottom-1 right-1 rounded-full bg-black/65 px-2 py-0.5 text-xs text-white">
                    {rec.duration ? `${Math.floor(rec.duration / 60)}:${String(Math.floor(rec.duration % 60)).padStart(2, "0")}` : "--:--"}
                  </div>
                </div>
                <div className="p-2">
                  <p className="text-xs text-white line-clamp-2">{rec.title}</p>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
