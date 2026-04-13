import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import Hls from "hls.js";
import {
  API_BASE,
  fetchVideo,
  fetchRecommendations,
  fetchNextVideo,
  updateVideo,
  deleteVideo,
  startTranscode,
  startCompress,
  recordWatchEvent,
  updateWatchTime,
  type VideoDetail,
  type VideoFilters,
  type VideoItem,
} from "../api/client";
import { formatDuration, formatFileSize } from "../utils/format";

const NON_NATIVE_EXTENSIONS = [".wmv", ".avi"];

function needsTranscode(video: VideoDetail | VideoItem): boolean {
  // If browser-friendly conversion is done, the raw stream endpoint serves
  // the converted MP4 — no HLS transcoding needed, no matter what the
  // original extension is.
  if (video.convert_status === "completed") return false;
  const filename = video.original_filename;
  const ext = filename.toLowerCase().slice(filename.lastIndexOf("."));
  return NON_NATIVE_EXTENSIONS.includes(ext);
}

function formatWatchTime(seconds: number): string {
  if (!seconds) return "--:--";
  return formatDuration(seconds);
}

function rawMimeType(video: VideoDetail | VideoItem): string {
  // When a successful conversion exists, the raw endpoint streams the
  // converted MP4 regardless of the original file's extension.
  if (video.convert_status === "completed") return "video/mp4";
  const filename = video.original_filename;
  const ext = filename.toLowerCase().slice(filename.lastIndexOf("."));
  if (ext === ".webm") return "video/webm";
  if (ext === ".mov") return "video/quicktime";
  return "video/mp4";
}

export default function WatchPage() {
  const { videoId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  // Review-mode context is encoded in the URL by HomePage when clicking from
  // the unconfirmed list. WatchPage reads it to know whether to auto-advance
  // after Confirm / Hard Delete, and which filter to pass to the next-video API.
  const reviewMode = searchParams.get("review") === "1";
  const reviewFilters: Omit<VideoFilters, "offset" | "limit"> = useMemo(() => {
    const f: Omit<VideoFilters, "offset" | "limit"> = {};
    const confirmedParam = searchParams.get("confirmed");
    if (confirmedParam === "true") f.confirmed = true;
    else if (confirmedParam === "false") f.confirmed = false;
    if (searchParams.get("ready") === "true") f.ready = true;
    const q = searchParams.get("q"); if (q) f.q = q;
    const tag = searchParams.get("tag"); if (tag) f.tag = tag;
    const codec = searchParams.get("codec"); if (codec) f.codec = codec;
    const library = searchParams.get("library"); if (library) f.library = library;
    const sort = searchParams.get("sort"); if (sort) f.sort = sort;
    return f;
  }, [searchParams]);

  async function advanceToNext() {
    if (!videoId) return;
    try {
      const r = await fetchNextVideo(videoId, reviewFilters);
      if (r.next_id) {
        // Preserve review-mode query so the chain keeps working
        navigate(`/watch/${r.next_id}?${searchParams.toString()}`);
      } else {
        // Nothing left — go back to the library
        navigate("/");
      }
    } catch {
      navigate("/");
    }
  }

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
    if (needsTranscode(video) && video.transcode_status === "pending") {
      startTranscode(videoId).then(() => load(videoId)).catch(() => null);
    }
  }, [video?.id, video?.transcode_status, video?.original_filename, video?.convert_status, videoId]);

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
    if (!needsTranscode(video) && video.raw_stream_url) return "raw" as const;
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

  const isNativePlayback = !needsTranscode(video);
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
    // In review mode, a confirm is the "done with this one" gesture — jump
    // straight to the next video matching the same filter so the user doesn't
    // have to navigate back to the library each time.
    if (reviewMode && !video.confirmed && updated.confirmed) {
      await advanceToNext();
    }
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
    if (reviewMode) {
      await advanceToNext();
    } else {
      navigate("/");
    }
  }

  const btnCls = "rounded-xl border border-white/15 bg-white/5 px-4 py-1.5 text-sm text-white/80 hover:bg-white/10 transition";
  const dangerCls = "rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-1.5 text-sm text-red-300 hover:bg-red-500/20 transition";

  return (
    <section className="space-y-6">
      {reviewMode && (
        <div className="flex items-center justify-between gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-amber-200">
          <span>
            <span className="font-semibold">Review mode.</span>
            {" "}Confirm / Hard Delete will jump to the next matching video.
          </span>
          <button
            onClick={() => navigate("/")}
            className="rounded-lg border border-white/15 bg-white/5 px-3 py-1 text-xs text-white/70 hover:bg-white/10 transition"
          >
            Exit review
          </button>
        </div>
      )}
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
                <source src={video.raw_stream_url} type={rawMimeType(video)} />
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
