import { useEffect, useRef, useState } from "react";
import {
  fetchExactDuplicates,
  fetchPerceptualDuplicates,
  computeHashes,
  compressOversized,
  fetchCompressCandidateList,
  fetchCompressCandidates,
  fetchCompressStatus,
  ignoreCompress,
  deleteVideo,
  startCompress,
  CompressCandidate,
  VideoItem,
} from "../api/client";
import VideoCard from "../components/VideoCard";
import { formatDuration, formatFileSize } from "../utils/format";

type DupMode = "exact" | "perceptual";

function DuplicateGroup({
  group,
  onTrash,
  onRecycle,
}: {
  group: VideoItem[];
  onTrash: (id: string) => void;
  onRecycle: (id: string) => void;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-4 space-y-3">
      <p className="text-xs text-white/40">{group.length} files - {formatFileSize(group[0].file_size)} each</p>
      <div
        className="grid gap-4"
        style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}
      >
        {group.map((v) => (
          <div key={v.id} className="space-y-1">
            <VideoCard video={v} showPath showExtraMeta />
            <div className="flex gap-2">
              <button
                onClick={() => onTrash(v.id)}
                className="flex-1 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-xs text-amber-200 hover:bg-amber-500/20 transition"
              >
                Hide
              </button>
              <button
                onClick={() => onRecycle(v.id)}
                className="flex-1 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1 text-xs text-red-300 hover:bg-red-500/20 transition"
              >
                Recycle Bin
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function MaintenancePage() {
  // Duplicates
  const [dupMode, setDupMode] = useState<DupMode>("exact");
  const [threshold, setThreshold] = useState(5);
  const [dupGroups, setDupGroups] = useState<VideoItem[][]>([]);
  const [dupLoading, setDupLoading] = useState(false);
  const [hashResult, setHashResult] = useState<string | null>(null);

  // Compression
  const [minHeight, setMinHeight] = useState(1440);
  const [forceCompress, setForceCompress] = useState(false);
  const [compressResult, setCompressResult] = useState<string | null>(null);
  const [compressStatus, setCompressStatus] = useState<{
    queue_size: number;
    current_video_id: string | null;
    current_video_title: string | null;
    current_progress: number;
    worker_running: boolean;
    batch_total_jobs: number;
    batch_completed_jobs: number;
    batch_failed_jobs: number;
    overall_progress: number;
  } | null>(null);
  const [compressCandidates, setCompressCandidates] = useState(0);
  const [compressItems, setCompressItems] = useState<CompressCandidate[]>([]);
  const [queueingId, setQueueingId] = useState<string | null>(null);
  const [ignoringId, setIgnoringId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load compression status on mount and poll if worker running
  useEffect(() => {
    loadCompressStatus();
  }, []);

  async function loadCompressCandidates() {
    try {
      const [countResult, listResult] = await Promise.all([
        fetchCompressCandidates(minHeight, forceCompress),
        fetchCompressCandidateList(minHeight, forceCompress),
      ]);
      setCompressCandidates(countResult.eligible);
      setCompressItems(listResult.items);
    } catch {
      return null;
    }
  }

  useEffect(() => {
    loadCompressCandidates();
  }, [minHeight, forceCompress]);

  useEffect(() => {
    if (compressStatus?.worker_running) {
      pollRef.current = setInterval(loadCompressStatus, 5000);
    } else {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [compressStatus?.worker_running]);

  async function loadCompressStatus() {
    fetchCompressStatus().then(setCompressStatus).catch(() => null);
  }

  async function loadDuplicates() {
    setDupLoading(true);
    try {
      const result = dupMode === "exact"
        ? await fetchExactDuplicates()
        : await fetchPerceptualDuplicates(threshold);
      setDupGroups(result.groups);
    } finally {
      setDupLoading(false);
    }
  }

  useEffect(() => {
    loadDuplicates();
  }, [dupMode]);

  async function handleComputeHashes() {
    setHashResult("Computing...");
    try {
      const r = await computeHashes();
      setHashResult(`Done: ${r.computed} hashes computed`);
    } catch {
      setHashResult("Failed");
    }
  }

  async function handleTrash(videoId: string) {
    await deleteVideo(videoId, false);
    setDupGroups((prev) =>
      prev
        .map((g) => g.filter((v) => v.id !== videoId))
        .filter((g) => g.length > 1)
    );
  }

  async function handleRecycle(videoId: string) {
    await deleteVideo(videoId, false, true);
    setDupGroups((prev) =>
      prev
        .map((g) => g.filter((v) => v.id !== videoId))
        .filter((g) => g.length > 1)
    );
  }

  async function handleCompressOversized() {
    setCompressResult("Queuing...");
    try {
      const r = await compressOversized(minHeight, forceCompress);
      setCompressResult(`Queued ${r.count} of ${r.eligible} candidate videos (${r.min_height}px+, force=${r.force ? "on" : "off"})`);
      await loadCompressCandidates();
      loadCompressStatus();
    } catch {
      setCompressResult("Failed to queue compression");
    }
  }

  async function handleCompressOne(video: CompressCandidate) {
    setQueueingId(video.id);
    setCompressResult(`Queuing ${video.original_filename} -> ${video.target_filename}...`);
    try {
      await startCompress(video.id);
      await loadCompressCandidates();
      loadCompressStatus();
      setCompressResult(`Queued ${video.original_filename} -> ${video.target_filename}`);
    } catch {
      setCompressResult(`Failed to queue ${video.original_filename}`);
    } finally {
      setQueueingId(null);
    }
  }

  async function handleIgnoreCompress(video: CompressCandidate) {
    setIgnoringId(video.id);
    setCompressResult(`Ignoring ${video.original_filename}...`);
    try {
      await ignoreCompress(video.id);
      await loadCompressCandidates();
      setCompressResult(`Ignored ${video.original_filename}`);
    } catch {
      setCompressResult(`Failed to ignore ${video.original_filename}`);
    } finally {
      setIgnoringId(null);
    }
  }

  const tabCls = (active: boolean) =>
    `px-4 py-1.5 rounded-lg text-sm transition cursor-pointer ${active ? "bg-accent/20 text-accent border border-accent/30" : "text-white/60 hover:text-white hover:bg-white/5"}`;

  const btnCls = "rounded-xl border border-white/15 bg-white/5 px-4 py-2 text-sm text-white/80 hover:bg-white/10 transition";

  return (
    <div className="space-y-10">
      <h1 className="text-3xl font-semibold text-white">Maintenance</h1>

      {/* Duplicates */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-white/80">Duplicates</h2>

        <div className="flex flex-wrap items-center gap-3">
          <button className={tabCls(dupMode === "exact")} onClick={() => setDupMode("exact")}>Exact</button>
          <button className={tabCls(dupMode === "perceptual")} onClick={() => setDupMode("perceptual")}>Perceptual</button>

          {dupMode === "perceptual" && (
            <div className="flex items-center gap-2">
              <label className="text-xs text-white/50">Threshold:</label>
              <input
                type="range"
                min={1}
                max={20}
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
                className="w-28 accent-amber-500"
              />
              <span className="text-sm text-white/60 w-4">{threshold}</span>
              <button onClick={handleComputeHashes} className={btnCls}>
                Compute Hashes
              </button>
              {hashResult && <span className="text-xs text-white/40">{hashResult}</span>}
            </div>
          )}

          <button onClick={loadDuplicates} disabled={dupLoading} className={btnCls}>
            {dupLoading ? "Loading..." : "Find Duplicates"}
          </button>
        </div>

        {!dupLoading && dupGroups.length === 0 && (
          <p className="text-white/40 text-sm">No duplicates found.</p>
        )}

        {!dupLoading && dupGroups.length > 0 && (
          <div className="space-y-4">
            <p className="text-sm text-white/40">{dupGroups.length} group{dupGroups.length !== 1 ? "s" : ""} found</p>
            {dupGroups.map((group, i) => (
              <DuplicateGroup key={i} group={group} onTrash={handleTrash} onRecycle={handleRecycle} />
            ))}
          </div>
        )}
      </section>

      <hr className="border-white/10" />

      {/* Compression */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-white/80">Compression</h2>

        {/* Status */}
        {compressStatus && (
          <div className="rounded-2xl border border-white/10 bg-white/5 p-4 space-y-3 text-sm">
            <div className="flex flex-wrap gap-6">
              <div>
                <span className="text-white/40">Queue: </span>
                <span className="text-white">{compressStatus.queue_size}</span>
              </div>
              <div>
                <span className="text-white/40">Worker: </span>
                <span className={compressStatus.worker_running ? "text-green-400" : "text-white/50"}>
                  {compressStatus.worker_running ? "Running" : "Idle"}
                </span>
              </div>
              <div>
                <span className="text-white/40">Done: </span>
                <span className="text-white">{compressStatus.batch_completed_jobs}/{compressStatus.batch_total_jobs || 0}</span>
              </div>
              <div>
                <span className="text-white/40">Failed: </span>
                <span className="text-white">{compressStatus.batch_failed_jobs}</span>
              </div>
            </div>

            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs text-white/45">
                <span>Overall progress</span>
                <span>{Math.round(compressStatus.overall_progress)}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full rounded-full bg-accent transition-all"
                  style={{ width: `${compressStatus.overall_progress}%` }}
                />
              </div>
            </div>

            {compressStatus.current_video_id && (
              <div className="space-y-1">
                <div className="text-xs text-white/40">Processing now</div>
                <div className="text-sm text-white/80">{compressStatus.current_video_title ?? compressStatus.current_video_id}</div>
                <div className="flex items-center justify-between text-xs text-white/45">
                  <span>Current file</span>
                  <span>{Math.round(compressStatus.current_progress)}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full rounded-full bg-green-400 transition-all"
                    style={{ width: `${compressStatus.current_progress}%` }}
                  />
                </div>
              </div>
            )}
          </div>
        )}

        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-sm text-white/50">Min height (px):</label>
            <input
              type="number"
              value={minHeight}
              onChange={(e) => setMinHeight(Number(e.target.value))}
              className="w-24 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-white focus:outline-none focus:border-accent/50"
              min={720}
              step={1}
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-white/60">
            <input
              type="checkbox"
              checked={forceCompress}
              onChange={(e) => setForceCompress(e.target.checked)}
              className="accent-amber-500"
            />
            Force requeue
          </label>
          <button onClick={handleCompressOversized} className={btnCls}>
            Compress All over {minHeight}px
          </button>
          <button onClick={loadCompressStatus} className={btnCls}>
            Refresh Status
          </button>
        </div>

        <p className="text-xs text-white/40">Eligible right now: {compressCandidates} (rule: video height &gt; {minHeight})</p>
        {compressResult && <p className="text-xs text-white/40">{compressResult}</p>}

        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-medium text-white/70">Queue One By One</h3>
            <p className="text-xs text-white/35">Showing up to {compressItems.length} candidates</p>
          </div>

          {compressItems.length === 0 ? (
            <p className="text-sm text-white/40">No eligible videos for single-item compression.</p>
          ) : (
            <div className="space-y-3">
              {compressItems.map((video) => (
                <div
                  key={video.id}
                  className="rounded-2xl border border-white/10 bg-white/[0.04] p-4"
                >
                  <div className="flex flex-col gap-4 lg:flex-row">
                    <div className="relative h-28 w-full shrink-0 overflow-hidden rounded-2xl border border-white/10 bg-white/5 lg:w-52">
                      <img
                        src={video.thumbnail_url}
                        alt=""
                        className="h-full w-full object-cover"
                        onError={(e) => {
                          (e.currentTarget as HTMLImageElement).style.display = "none";
                        }}
                      />
                      <div className="absolute bottom-2 right-2 rounded-full bg-black/65 px-2.5 py-1 text-xs text-white">
                        {formatDuration(video.duration)}
                      </div>
                    </div>

                    <div className="flex min-w-0 flex-1 flex-col gap-3">
                      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                        <div className="min-w-0 space-y-2">
                          <div>
                            <p className="text-xs uppercase tracking-[0.18em] text-white/30">Before</p>
                            <p className="truncate text-sm font-medium text-white">{video.original_filename}</p>
                          </div>
                          <div>
                            <p className="text-xs uppercase tracking-[0.18em] text-white/30">After</p>
                            <p className="truncate text-sm font-medium text-accent">{video.target_filename}</p>
                          </div>
                          <p className="truncate text-xs text-white/35">{video.original_path}</p>
                        </div>

                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-white/45 lg:justify-end">
                          <span>{formatFileSize(video.file_size)}</span>
                          <span>{video.width ?? "?"}x{video.height ?? "?"}</span>
                          <span>{video.codec ?? "unknown codec"}</span>
                          <span>{formatDuration(video.duration)}</span>
                        </div>
                      </div>

                      <div className="flex flex-wrap justify-end gap-2">
                        <button
                          onClick={() => handleIgnoreCompress(video)}
                          disabled={ignoringId === video.id}
                          className="rounded-xl border border-white/15 bg-white/5 px-4 py-2 text-sm text-white/75 hover:bg-white/10 transition disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {ignoringId === video.id ? "Ignoring..." : "Ignore"}
                        </button>
                        <button
                          onClick={() => handleCompressOne(video)}
                          disabled={queueingId === video.id}
                          className="rounded-xl border border-accent/30 bg-accent/10 px-4 py-2 text-sm text-accent hover:bg-accent/20 transition disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {queueingId === video.id ? "Queuing..." : "Compress This Video"}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
