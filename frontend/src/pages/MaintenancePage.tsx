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
  fetchConvertCandidates,
  fetchConvertStatus,
  fetchEncoderInfo,
  convertOne,
  convertAllPending,
  convertBatch,
  stopConvert,
  stopCompress,
  fetchPaletteStatus,
  fetchPaletteMissingCount,
  generateAllPalettes,
  stopPalettes,
  PaletteStatus,
  ConvertStatus,
  ConvertSort,
  CompressCandidate,
  VideoItem,
} from "../api/client";
import VideoCard from "../components/VideoCard";
import { formatDuration, formatFileSize } from "../utils/format";

type DupMode = "exact" | "perceptual";

function SpoilerToggle({
  open,
  onClick,
  label,
  count,
}: {
  open: boolean;
  onClick: () => void;
  label: string;
  count?: number | null;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-sm text-white/75 hover:bg-white/10 transition cursor-pointer"
    >
      <span className={`inline-block transition-transform ${open ? "rotate-90" : ""}`}>&#x25B6;</span>
      <span>{open ? "Hide" : "Show"} {label}</span>
      {count != null && <span className="text-xs text-white/40">({count})</span>}
    </button>
  );
}

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

  // Conversion (WMV / AVI -> browser-friendly MP4)
  const [convertItems, setConvertItems] = useState<VideoItem[]>([]);
  const [convertEligible, setConvertEligible] = useState(0);
  const [convertStatus, setConvertStatus] = useState<ConvertStatus | null>(null);
  const [convertResult, setConvertResult] = useState<string | null>(null);
  const [convertSelected, setConvertSelected] = useState<Set<string>>(new Set());
  const [convertingId, setConvertingId] = useState<string | null>(null);
  const [encoderInfo, setEncoderInfo] = useState<{ effective: string; nvenc_available: boolean } | null>(null);
  const convertPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Convert pagination + sort
  const CONVERT_PAGE_SIZE = 20;
  const [convertPage, setConvertPage] = useState(0);
  const [convertSort, setConvertSort] = useState<ConvertSort>("h264_first");
  const [convertLoading, setConvertLoading] = useState(false);

  // Collapsible spoilers — start collapsed for big lists so the page loads light
  const [showDupResults, setShowDupResults] = useState(false);
  const [showCompressList, setShowCompressList] = useState(false);
  const [showConvertList, setShowConvertList] = useState(false);

  // Palette (contact sheet) batch generation
  const [paletteStatus, setPaletteStatus] = useState<PaletteStatus | null>(null);
  const [paletteMissing, setPaletteMissing] = useState<number | null>(null);
  const [paletteResult, setPaletteResult] = useState<string | null>(null);
  const palettePollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load lightweight things on mount. The big candidate lists are NOT fetched
  // here — they load lazily when the user expands their respective spoilers.
  useEffect(() => {
    loadCompressStatus();
    loadConvertStatus();
    loadPaletteStatus();
    loadPaletteMissingCount();
    fetchEncoderInfo().then(setEncoderInfo).catch(() => null);
  }, []);

  async function loadPaletteStatus() {
    try {
      const s = await fetchPaletteStatus();
      setPaletteStatus(s);
    } catch {
      return null;
    }
  }

  async function loadPaletteMissingCount() {
    try {
      const r = await fetchPaletteMissingCount();
      setPaletteMissing(r.missing);
    } catch {
      return null;
    }
  }

  // Poll palette status while worker is active so the progress bar moves live
  useEffect(() => {
    if (paletteStatus?.worker_running && (paletteStatus.queue_size > 0 || paletteStatus.current_video_id)) {
      palettePollRef.current = setInterval(() => {
        loadPaletteStatus();
      }, 3000);
    } else {
      if (palettePollRef.current) { clearInterval(palettePollRef.current); palettePollRef.current = null; }
    }
    return () => { if (palettePollRef.current) clearInterval(palettePollRef.current); };
  }, [paletteStatus?.worker_running, paletteStatus?.queue_size, paletteStatus?.current_video_id]);

  async function handlePaletteGenerateAll() {
    setPaletteResult("Queuing...");
    try {
      const r = await generateAllPalettes();
      setPaletteResult(`Queued ${r.count} missing palettes`);
      await loadPaletteStatus();
      await loadPaletteMissingCount();
    } catch {
      setPaletteResult("Failed to queue");
    }
  }

  async function handlePaletteStop() {
    if (!confirm("Stop palette generation — drop queue?")) return;
    setPaletteResult("Stopping...");
    try {
      const r = await stopPalettes();
      setPaletteResult(`Stopped: dropped ${r.dropped_queued} queued`);
      await loadPaletteStatus();
      await loadPaletteMissingCount();
    } catch {
      setPaletteResult("Failed to stop");
    }
  }

  async function loadConvertStatus() {
    try {
      const s = await fetchConvertStatus();
      setConvertStatus(s);
    } catch {
      return null;
    }
  }

  async function loadConvertCandidates(page = convertPage, sort = convertSort) {
    setConvertLoading(true);
    try {
      const r = await fetchConvertCandidates(CONVERT_PAGE_SIZE, page * CONVERT_PAGE_SIZE, sort);
      setConvertItems(r.items);
      setConvertEligible(r.total);
    } catch {
      return null;
    } finally {
      setConvertLoading(false);
    }
  }

  // Auto-fetch convert candidates when the section opens or page/sort changes.
  useEffect(() => {
    if (!showConvertList) return;
    loadConvertCandidates(convertPage, convertSort);
  }, [showConvertList, convertPage, convertSort]);

  // Auto-fetch compress candidates only when its spoiler is open. Initial counter
  // (used by status panel) is fetched separately on mount via the existing
  // [minHeight, forceCompress] effect.

  useEffect(() => {
    if (convertStatus?.worker_running && (convertStatus.queue_size > 0 || convertStatus.current_video_id)) {
      convertPollRef.current = setInterval(() => {
        loadConvertStatus();
        loadConvertCandidates();
      }, 5000);
    } else {
      if (convertPollRef.current) { clearInterval(convertPollRef.current); convertPollRef.current = null; }
    }
    return () => { if (convertPollRef.current) clearInterval(convertPollRef.current); };
  }, [convertStatus?.worker_running, convertStatus?.queue_size, convertStatus?.current_video_id]);

  function toggleConvertSelected(id: string) {
    setConvertSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAllConvert() {
    setConvertSelected(new Set(convertItems.map((v) => v.id)));
  }

  function clearConvertSelection() {
    setConvertSelected(new Set());
  }

  async function handleConvertOne(video: VideoItem) {
    setConvertingId(video.id);
    setConvertResult(`Queuing ${video.original_filename}...`);
    try {
      const r = await convertOne(video.id);
      if (r.status === "queued") {
        setConvertResult(`Queued ${video.original_filename}`);
      } else {
        setConvertResult(`Skipped ${video.original_filename}: ${r.reason ?? r.status}`);
      }
      await loadConvertStatus();
    } catch {
      setConvertResult(`Failed to queue ${video.original_filename}`);
    } finally {
      setConvertingId(null);
    }
  }

  async function handleConvertSelected() {
    if (convertSelected.size === 0) return;
    const ids = Array.from(convertSelected);
    setConvertResult(`Queuing ${ids.length} selected...`);
    try {
      const r = await convertBatch(ids);
      setConvertResult(`Queued ${r.queued} of ${ids.length} selected (skipped ${r.skipped.length})`);
      clearConvertSelection();
      await loadConvertStatus();
      await loadConvertCandidates();
    } catch {
      setConvertResult(`Failed to queue selected videos`);
    }
  }

  async function handleConvertAll() {
    setConvertResult(`Queuing all candidates...`);
    try {
      const r = await convertAllPending();
      setConvertResult(`Queued ${r.count} videos`);
      await loadConvertStatus();
      await loadConvertCandidates();
    } catch {
      setConvertResult(`Failed to queue all`);
    }
  }

  async function handleConvertStop() {
    if (!confirm("Stop conversion — drop queue and kill the running ffmpeg?")) return;
    setConvertResult(`Stopping...`);
    try {
      const r = await stopConvert();
      setConvertResult(
        `Stopped: dropped ${r.dropped_queued} queued, ${r.killed_current ? "killed current" : "no active job"}`
      );
      await loadConvertStatus();
      await loadConvertCandidates();
    } catch {
      setConvertResult(`Failed to stop`);
    }
  }

  async function handleCompressStop() {
    if (!confirm("Stop compression — drop queue and kill the running ffmpeg?")) return;
    setCompressResult(`Stopping...`);
    try {
      const r = await stopCompress();
      setCompressResult(
        `Stopped: dropped ${r.dropped_queued} queued, ${r.killed_current ? "killed current" : "no active job"}`
      );
      await loadCompressStatus();
      await loadCompressCandidates();
    } catch {
      setCompressResult(`Failed to stop`);
    }
  }

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
    if (!showCompressList) return;
    loadCompressCandidates();
  }, [showCompressList, minHeight, forceCompress]);

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
    setShowDupResults(true);
    try {
      const result = dupMode === "exact"
        ? await fetchExactDuplicates()
        : await fetchPerceptualDuplicates(threshold);
      setDupGroups(result.groups);
    } finally {
      setDupLoading(false);
    }
  }

  // Note: duplicates are NOT auto-fetched on mount — the Find Duplicates click
  // is what triggers the (potentially slow) scan. Mode change just clears the
  // current results so the user re-runs explicitly.
  useEffect(() => {
    setDupGroups([]);
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

        {!dupLoading && dupGroups.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <p className="text-sm text-white/40">{dupGroups.length} group{dupGroups.length !== 1 ? "s" : ""} found</p>
              <SpoilerToggle
                open={showDupResults}
                onClick={() => setShowDupResults((v) => !v)}
                label="duplicate groups"
                count={dupGroups.length}
              />
            </div>
            {showDupResults && (
              <div className="space-y-4">
                {dupGroups.map((group, i) => (
                  <DuplicateGroup key={i} group={group} onTrash={handleTrash} onRecycle={handleRecycle} />
                ))}
              </div>
            )}
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
          {compressStatus && (compressStatus.queue_size > 0 || compressStatus.current_video_id) && (
            <button
              onClick={handleCompressStop}
              className="rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300 hover:bg-red-500/20 transition"
            >
              Stop
            </button>
          )}
        </div>

        <p className="text-xs text-white/40">Eligible right now: {compressCandidates} (rule: video height &gt; {minHeight})</p>
        {compressResult && <p className="text-xs text-white/40">{compressResult}</p>}

        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-medium text-white/70">Queue One By One</h3>
            <SpoilerToggle
              open={showCompressList}
              onClick={() => setShowCompressList((v) => !v)}
              label="candidate list"
              count={compressCandidates}
            />
          </div>

          {showCompressList && compressItems.length === 0 ? (
            <p className="text-sm text-white/40">No eligible videos for single-item compression.</p>
          ) : showCompressList ? (
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
          ) : null}
        </div>
      </section>

      <hr className="border-white/10" />

      {/* Browser-friendly conversion (WMV / AVI -> MP4) */}
      <section className="space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <h2 className="text-xl font-semibold text-white/80">Browser Conversion (WMV / AVI &rarr; MP4)</h2>
          {encoderInfo && (
            <span className="text-xs rounded-full border border-white/10 bg-white/5 px-3 py-1 text-white/60">
              encoder: <span className="text-accent">{encoderInfo.effective}</span>
              {encoderInfo.nvenc_available ? " (NVENC ready)" : " (CPU only)"}
            </span>
          )}
        </div>

        {/* Status panel */}
        {convertStatus && (
          <div className="rounded-2xl border border-white/10 bg-white/5 p-4 space-y-3 text-sm">
            <div className="flex flex-wrap gap-6">
              <div>
                <span className="text-white/40">Queue: </span>
                <span className="text-white">{convertStatus.queue_size}</span>
              </div>
              <div>
                <span className="text-white/40">Worker: </span>
                <span className={convertStatus.worker_running ? "text-green-400" : "text-white/50"}>
                  {convertStatus.worker_running ? "Running" : "Idle"}
                </span>
              </div>
              <div>
                <span className="text-white/40">Done: </span>
                <span className="text-white">{convertStatus.batch_completed_jobs}/{convertStatus.batch_total_jobs || 0}</span>
              </div>
              <div>
                <span className="text-white/40">Failed: </span>
                <span className="text-white">{convertStatus.batch_failed_jobs}</span>
              </div>
            </div>

            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs text-white/45">
                <span>Overall progress</span>
                <span>{Math.round(convertStatus.overall_progress)}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full rounded-full bg-accent transition-all"
                  style={{ width: `${convertStatus.overall_progress}%` }}
                />
              </div>
            </div>

            {convertStatus.current_video_id && (
              <div className="space-y-1">
                <div className="text-xs text-white/40">Processing now</div>
                <div className="text-sm text-white/80">{convertStatus.current_video_title ?? convertStatus.current_video_id}</div>
                <div className="flex items-center justify-between text-xs text-white/45">
                  <span>Current file</span>
                  <span>{Math.round(convertStatus.current_progress)}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full rounded-full bg-green-400 transition-all"
                    style={{ width: `${convertStatus.current_progress}%` }}
                  />
                </div>
              </div>
            )}
          </div>
        )}

        {/* Action row */}
        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={handleConvertAll} className={btnCls}>
            Convert All ({convertEligible})
          </button>
          <button
            onClick={handleConvertSelected}
            disabled={convertSelected.size === 0}
            className="rounded-xl border border-accent/30 bg-accent/10 px-4 py-2 text-sm text-accent hover:bg-accent/20 transition disabled:cursor-not-allowed disabled:opacity-50"
          >
            Convert Selected ({convertSelected.size})
          </button>
          <button onClick={selectAllConvert} className={btnCls}>Select All</button>
          <button onClick={clearConvertSelection} className={btnCls}>Clear Selection</button>
          <button onClick={() => { loadConvertStatus(); loadConvertCandidates(); }} className={btnCls}>
            Refresh
          </button>
          {convertStatus && (convertStatus.queue_size > 0 || convertStatus.current_video_id) && (
            <button
              onClick={handleConvertStop}
              className="rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300 hover:bg-red-500/20 transition"
            >
              Stop
            </button>
          )}
        </div>

        <p className="text-xs text-white/40">
          Eligible candidates: {convertEligible} (file extension is .wmv or .avi)
        </p>
        {convertResult && <p className="text-xs text-white/40">{convertResult}</p>}

        {/* Sort + spoiler toggle row */}
        <div className="flex items-center gap-3 flex-wrap">
          <SpoilerToggle
            open={showConvertList}
            onClick={() => setShowConvertList((v) => !v)}
            label="candidate list"
            count={convertEligible}
          />
          {showConvertList && (
            <>
              <label className="text-xs text-white/45">Sort by:</label>
              <select
                value={convertSort}
                onChange={(e) => { setConvertSort(e.target.value as ConvertSort); setConvertPage(0); }}
                className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-white focus:outline-none focus:border-accent/50"
              >
                <option value="h264_first">H.264 first (cheap remux)</option>
                <option value="size_asc">Smallest first</option>
                <option value="size_desc">Largest first</option>
                <option value="name">Name</option>
              </select>
              {convertLoading && <span className="text-xs text-white/40">Loading...</span>}
            </>
          )}
        </div>

        {/* Candidate list */}
        {showConvertList && convertItems.length === 0 && !convertLoading && (
          <p className="text-sm text-white/40">No videos waiting for conversion.</p>
        )}
        {showConvertList && convertItems.length > 0 && (
          <div className="space-y-3">
            {convertItems.map((video) => {
              const isSelected = convertSelected.has(video.id);
              return (
                <div
                  key={video.id}
                  className={`rounded-2xl border p-4 transition ${
                    isSelected ? "border-accent/50 bg-accent/[0.06]" : "border-white/10 bg-white/[0.04]"
                  }`}
                >
                  <div className="flex flex-col gap-4 lg:flex-row">
                    <div className="flex shrink-0 items-start gap-3">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleConvertSelected(video.id)}
                        className="mt-1 h-5 w-5 accent-amber-500 cursor-pointer"
                      />
                      <div className="relative h-28 w-full overflow-hidden rounded-2xl border border-white/10 bg-white/5 lg:w-52">
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
                    </div>

                    <div className="flex min-w-0 flex-1 flex-col gap-3">
                      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                        <div className="min-w-0 space-y-1">
                          <p className="truncate text-sm font-medium text-white">{video.original_filename}</p>
                          <p className="truncate text-xs text-white/35">{video.original_path}</p>
                        </div>

                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-white/45 lg:justify-end">
                          <span>{formatFileSize(video.file_size)}</span>
                          <span>{video.width ?? "?"}x{video.height ?? "?"}</span>
                          <span className={video.codec === "h264" ? "text-green-400" : ""}>
                            {video.codec ?? "unknown"}
                            {video.codec === "h264" && " (remux fast-path)"}
                          </span>
                          <span className={
                            video.convert_status === "failed" ? "text-red-400"
                            : video.convert_status === "pending" ? "text-amber-400"
                            : video.convert_status === "processing" ? "text-blue-400"
                            : "text-white/40"
                          }>
                            {video.convert_status}
                            {video.convert_status === "processing" && ` ${Math.round(video.convert_progress)}%`}
                          </span>
                        </div>
                      </div>

                      <div className="flex flex-wrap justify-end gap-2">
                        <button
                          onClick={() => handleConvertOne(video)}
                          disabled={convertingId === video.id}
                          className="rounded-xl border border-accent/30 bg-accent/10 px-4 py-2 text-sm text-accent hover:bg-accent/20 transition disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {convertingId === video.id ? "Queuing..." : "Convert This Video"}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Pagination footer */}
        {showConvertList && convertEligible > CONVERT_PAGE_SIZE && (
          <div className="flex items-center justify-between gap-3 pt-2">
            <button
              onClick={() => setConvertPage((p) => Math.max(0, p - 1))}
              disabled={convertPage === 0 || convertLoading}
              className={btnCls + " disabled:opacity-40 disabled:cursor-not-allowed"}
            >
              &larr; Prev
            </button>
            <span className="text-xs text-white/45">
              Page {convertPage + 1} / {Math.max(1, Math.ceil(convertEligible / CONVERT_PAGE_SIZE))}
              {" • "}
              Showing {convertPage * CONVERT_PAGE_SIZE + 1}-
              {Math.min((convertPage + 1) * CONVERT_PAGE_SIZE, convertEligible)} of {convertEligible}
            </span>
            <button
              onClick={() => setConvertPage((p) => p + 1)}
              disabled={(convertPage + 1) * CONVERT_PAGE_SIZE >= convertEligible || convertLoading}
              className={btnCls + " disabled:opacity-40 disabled:cursor-not-allowed"}
            >
              Next &rarr;
            </button>
          </div>
        )}
      </section>

      <hr className="border-white/10" />

      {/* Video palettes (contact sheets) — batch generation */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-white/80">Video Palettes</h2>

        {paletteStatus && (
          <div className="rounded-2xl border border-white/10 bg-white/5 p-4 space-y-3 text-sm">
            <div className="flex flex-wrap gap-6">
              <div>
                <span className="text-white/40">Queue: </span>
                <span className="text-white">{paletteStatus.queue_size}</span>
              </div>
              <div>
                <span className="text-white/40">Worker: </span>
                <span className={paletteStatus.worker_running ? "text-green-400" : "text-white/50"}>
                  {paletteStatus.worker_running ? "Running" : "Idle"}
                </span>
              </div>
              <div>
                <span className="text-white/40">Done: </span>
                <span className="text-white">{paletteStatus.batch_completed_jobs}/{paletteStatus.batch_total_jobs || 0}</span>
              </div>
              <div>
                <span className="text-white/40">Failed: </span>
                <span className="text-white">{paletteStatus.batch_failed_jobs}</span>
              </div>
              <div>
                <span className="text-white/40">Missing on disk: </span>
                <span className="text-white">{paletteMissing ?? "?"}</span>
              </div>
            </div>

            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs text-white/45">
                <span>Overall progress</span>
                <span>{Math.round(paletteStatus.overall_progress)}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full rounded-full bg-accent transition-all"
                  style={{ width: `${paletteStatus.overall_progress}%` }}
                />
              </div>
            </div>

            {paletteStatus.current_video_id && (
              <div className="space-y-1">
                <div className="text-xs text-white/40">Processing now</div>
                <div className="text-sm text-white/80">{paletteStatus.current_video_title ?? paletteStatus.current_video_id}</div>
              </div>
            )}
          </div>
        )}

        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={handlePaletteGenerateAll} className={btnCls}>
            Generate All Missing ({paletteMissing ?? "?"})
          </button>
          <button onClick={() => { loadPaletteStatus(); loadPaletteMissingCount(); }} className={btnCls}>
            Refresh
          </button>
          {paletteStatus && (paletteStatus.queue_size > 0 || paletteStatus.current_video_id) && (
            <button
              onClick={handlePaletteStop}
              className="rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300 hover:bg-red-500/20 transition"
            >
              Stop
            </button>
          )}
        </div>

        <p className="text-xs text-white/40">
          Generates 16-frame contact sheets so videos become reviewable without playback.
          Uses NVDEC decode when available, otherwise CPU.
        </p>
        {paletteResult && <p className="text-xs text-white/40">{paletteResult}</p>}
      </section>
    </div>
  );
}
