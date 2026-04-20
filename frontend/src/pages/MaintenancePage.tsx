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
  fetchConvertedOriginals,
  replaceConvertedOriginals,
  ConvertedOriginal,
  fetchCompressArchive,
  purgeCompressArchive,
  ArchiveItem,
  fetchTagNormalizePreview,
  applyTagNormalize,
  TagNormalizePlan,
  fetchScreenFolders,
  purgeScreenFolders,
  ScreenFolderItem,
  fetchSimilarTags,
  mergeTags,
  TagCluster,
  fetchExtractPreview,
  applyTagExtract,
  ExtractPreview,
  convertOne,
  convertAllPending,
  convertBatch,
  stopConvert,
  stopCompress,
  fetchPaletteStatus,
  fetchPaletteMissingCount,
  fetchPaletteCandidates,
  generateAllPalettes,
  generatePaletteOne,
  generatePalettesBatch,
  stopPalettes,
  PaletteStatus,
  PaletteSort,
  fetchOrphans,
  retryOrphan,
  retryAllOrphans,
  OrphanItem,
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

  // Replace-converted-originals (delete WMV/AVI after successful MP4 conversion,
  // move MP4 into the library at the original path's location).
  const [replaceItems, setReplaceItems] = useState<ConvertedOriginal[]>([]);
  const [replaceReclaimable, setReplaceReclaimable] = useState(0);
  const [replaceLoading, setReplaceLoading] = useState(false);
  const [replaceRunning, setReplaceRunning] = useState(false);
  const [replaceResult, setReplaceResult] = useState<string | null>(null);
  const [showReplaceList, setShowReplaceList] = useState(false);

  // Compress archive (big_archive_dir) — post-compression originals that can
  // be recycled to actually free disk space on a single-drive setup.
  const [archiveInfo, setArchiveInfo] = useState<{
    path: string; exists: boolean; total_size: number; file_count: number;
  } | null>(null);
  const [archiveItems, setArchiveItems] = useState<ArchiveItem[]>([]);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [archiveRunning, setArchiveRunning] = useState(false);
  const [archiveResult, setArchiveResult] = useState<string | null>(null);
  const [showArchiveList, setShowArchiveList] = useState(false);
  const [archiveOlderDays, setArchiveOlderDays] = useState<string>("30");
  const [archiveSelected, setArchiveSelected] = useState<Set<string>>(new Set());

  // Tag normalizer — fold count/site suffixes, drop service folders.
  const [tagPlan, setTagPlan] = useState<TagNormalizePlan | null>(null);
  const [tagPlanLoading, setTagPlanLoading] = useState(false);
  const [tagApplying, setTagApplying] = useState(false);
  const [tagResult, setTagResult] = useState<string | null>(null);
  const [showTagPlan, setShowTagPlan] = useState(false);

  // Tag dedup — similar / fuzzy clusters + manual merge controls.
  const [fingerprintClusters, setFingerprintClusters] = useState<TagCluster[]>([]);
  const [fuzzyClusters, setFuzzyClusters] = useState<TagCluster[]>([]);
  const [similarLoading, setSimilarLoading] = useState(false);
  const [similarResult, setSimilarResult] = useState<string | null>(null);
  const [showSimilar, setShowSimilar] = useState(false);
  // Per-cluster UI state: canonical name (editable), set of source names to include.
  const [clusterState, setClusterState] = useState<Record<string, { canonical: string; include: Set<string> }>>({});

  // Tag extraction from filenames.
  const [extractPreview, setExtractPreview] = useState<ExtractPreview | null>(null);
  const [extractLoading, setExtractLoading] = useState(false);
  const [extractApplying, setExtractApplying] = useState(false);
  const [extractResult, setExtractResult] = useState<string | null>(null);
  const [extractUnchecked, setExtractUnchecked] = useState<Set<string>>(new Set());
  const [showExtractList, setShowExtractList] = useState(false);

  // Screenshot / pack folder cleanup (physical delete in library roots).
  const [screenFolders, setScreenFolders] = useState<ScreenFolderItem[]>([]);
  const [screenTotalSize, setScreenTotalSize] = useState(0);
  const [screenLoading, setScreenLoading] = useState(false);
  const [screenRunning, setScreenRunning] = useState(false);
  const [screenResult, setScreenResult] = useState<string | null>(null);
  const [screenSelected, setScreenSelected] = useState<Set<string>>(new Set());
  const [showScreenList, setShowScreenList] = useState(false);

  // Collapsible spoilers — start collapsed for big lists so the page loads light
  const [showDupResults, setShowDupResults] = useState(false);
  const [showCompressList, setShowCompressList] = useState(false);
  const [showConvertList, setShowConvertList] = useState(false);

  // Palette (contact sheet) batch generation
  const [paletteStatus, setPaletteStatus] = useState<PaletteStatus | null>(null);
  const [paletteMissing, setPaletteMissing] = useState<number | null>(null);
  const [paletteResult, setPaletteResult] = useState<string | null>(null);
  const palettePollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [paletteItems, setPaletteItems] = useState<VideoItem[]>([]);
  const [paletteTotal, setPaletteTotal] = useState(0);
  const [paletteSelected, setPaletteSelected] = useState<Set<string>>(new Set());
  const [paletteGeneratingId, setPaletteGeneratingId] = useState<string | null>(null);
  const [showPaletteList, setShowPaletteList] = useState(false);
  const PALETTE_PAGE_SIZE = 20;
  const [palettePage, setPalettePage] = useState(0);
  const [paletteSort, setPaletteSort] = useState<PaletteSort>("name");
  const [paletteLoading, setPaletteLoading] = useState(false);

  // Locked / orphan files
  const [orphans, setOrphans] = useState<OrphanItem[]>([]);
  const [orphanLoading, setOrphanLoading] = useState(false);
  const [orphanResult, setOrphanResult] = useState<string | null>(null);
  const [retryingOrphanId, setRetryingOrphanId] = useState<string | null>(null);

  async function loadOrphans() {
    setOrphanLoading(true);
    try {
      const r = await fetchOrphans();
      setOrphans(r.items);
    } catch {
      return null;
    } finally {
      setOrphanLoading(false);
    }
  }

  async function handleRetryOrphan(id: string) {
    setRetryingOrphanId(id);
    try {
      const r = await retryOrphan(id);
      setOrphanResult(`${id.slice(0, 8)}: ${r.status}${r.error ? " — " + r.error : ""}`);
      if (r.status === "recycled" || r.status === "purged_no_file") {
        setOrphans((prev) => prev.filter((v) => v.id !== id));
      }
    } catch {
      setOrphanResult(`Failed`);
    } finally {
      setRetryingOrphanId(null);
    }
  }

  async function handleRetryAllOrphans() {
    if (!confirm("Retry recycle-to-bin for every locked file?")) return;
    setOrphanResult("Retrying...");
    try {
      const r = await retryAllOrphans();
      setOrphanResult(`Recycled ${r.recycled}, still locked ${r.still_locked}, purged ${r.purged_no_file}`);
      await loadOrphans();
    } catch {
      setOrphanResult("Failed");
    }
  }

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

  async function loadPaletteCandidates(page = palettePage, sort = paletteSort) {
    setPaletteLoading(true);
    try {
      const r = await fetchPaletteCandidates(PALETTE_PAGE_SIZE, page * PALETTE_PAGE_SIZE, sort);
      setPaletteItems(r.items);
      setPaletteTotal(r.total);
    } catch {
      return null;
    } finally {
      setPaletteLoading(false);
    }
  }

  useEffect(() => {
    if (!showPaletteList) return;
    loadPaletteCandidates(palettePage, paletteSort);
  }, [showPaletteList, palettePage, paletteSort]);

  function togglePaletteSelected(id: string) {
    setPaletteSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function selectAllPalette() {
    setPaletteSelected(new Set(paletteItems.map((v) => v.id)));
  }
  function clearPaletteSelection() {
    setPaletteSelected(new Set());
  }

  async function handlePaletteOne(video: VideoItem) {
    setPaletteGeneratingId(video.id);
    setPaletteResult(`Queuing ${video.original_filename}...`);
    try {
      await generatePaletteOne(video.id);
      setPaletteResult(`Queued ${video.original_filename}`);
      setPaletteItems((prev) => prev.filter((v) => v.id !== video.id));
      setPaletteTotal((t) => Math.max(0, t - 1));
      setPaletteMissing((m) => (m == null ? m : Math.max(0, m - 1)));
      await loadPaletteStatus();
    } catch {
      setPaletteResult(`Failed to queue ${video.original_filename}`);
    } finally {
      setPaletteGeneratingId(null);
    }
  }

  async function handlePaletteSelected() {
    if (paletteSelected.size === 0) return;
    const ids = Array.from(paletteSelected);
    setPaletteResult(`Queuing ${ids.length} selected...`);
    try {
      const r = await generatePalettesBatch(ids);
      setPaletteResult(`Queued ${r.queued} of ${ids.length} selected`);
      const queuedSet = new Set(ids);
      setPaletteItems((prev) => prev.filter((v) => !queuedSet.has(v.id)));
      setPaletteTotal((t) => Math.max(0, t - r.queued));
      setPaletteMissing((m) => (m == null ? m : Math.max(0, m - r.queued)));
      clearPaletteSelection();
      await loadPaletteStatus();
    } catch {
      setPaletteResult(`Failed to queue selected`);
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

  async function loadReplaceCandidates() {
    setReplaceLoading(true);
    try {
      const r = await fetchConvertedOriginals();
      setReplaceItems(r.items);
      setReplaceReclaimable(r.reclaimable_bytes);
    } catch {
      return null;
    } finally {
      setReplaceLoading(false);
    }
  }

  async function handleReplaceOriginals() {
    if (!confirm(`Move ${replaceItems.length} MP4s into library and Recycle-bin the ${replaceItems.length} WMV/AVI originals?`)) return;
    setReplaceRunning(true);
    setReplaceResult(null);
    try {
      const r = await replaceConvertedOriginals();
      const parts = [`Replaced ${r.replaced}`];
      if (r.skipped_collision) parts.push(`skipped ${r.skipped_collision} (target exists)`);
      if (r.move_failed) parts.push(`move failed ${r.move_failed}`);
      if (r.recycle_failed) parts.push(`recycle failed ${r.recycle_failed}`);
      setReplaceResult(parts.join(", "));
      await loadReplaceCandidates();
    } catch (e) {
      setReplaceResult(String(e));
    } finally {
      setReplaceRunning(false);
    }
  }

  useEffect(() => {
    if (showReplaceList) loadReplaceCandidates();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showReplaceList]);

  async function loadArchive() {
    setArchiveLoading(true);
    try {
      const r = await fetchCompressArchive();
      setArchiveInfo({
        path: r.path,
        exists: r.exists,
        total_size: r.total_size,
        file_count: r.file_count,
      });
      setArchiveItems(r.items);
      setArchiveSelected(new Set());
    } catch {
      return null;
    } finally {
      setArchiveLoading(false);
    }
  }

  function toggleArchiveSelected(path: string) {
    setArchiveSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  }

  async function handleArchivePurge(mode: "all" | "older" | "selected") {
    if (!archiveInfo) return;
    let body: { older_than_days?: number | null; paths?: string[] | null } = {};
    let confirmMsg = "";

    if (mode === "selected") {
      if (archiveSelected.size === 0) return;
      body = { paths: Array.from(archiveSelected) };
      confirmMsg = `Переместить ${archiveSelected.size} выбранных файлов в Корзину?`;
    } else if (mode === "older") {
      const days = parseInt(archiveOlderDays, 10);
      if (!Number.isFinite(days) || days < 0) return;
      body = { older_than_days: days };
      const count = archiveItems.filter((it) => it.age_days >= days).length;
      const size = archiveItems
        .filter((it) => it.age_days >= days)
        .reduce((s, it) => s + it.size, 0);
      confirmMsg = `Переместить в Корзину ${count} файлов старше ${days} дн. (${formatFileSize(size)})?`;
    } else {
      confirmMsg = `Переместить в Корзину ВСЕ ${archiveInfo.file_count} файлов архива (${formatFileSize(archiveInfo.total_size)})?`;
    }

    if (!confirm(confirmMsg)) return;
    setArchiveRunning(true);
    setArchiveResult(null);
    try {
      const r = await purgeCompressArchive(body);
      const parts = [`Recycled ${r.recycled}`, `freed ${formatFileSize(r.total_bytes_freed)}`];
      if (r.failed) parts.push(`failed ${r.failed}`);
      setArchiveResult(parts.join(", "));
      await loadArchive();
    } catch (e) {
      setArchiveResult(String(e));
    } finally {
      setArchiveRunning(false);
    }
  }

  useEffect(() => {
    if (showArchiveList) loadArchive();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showArchiveList]);

  async function loadTagPlan() {
    setTagPlanLoading(true);
    try {
      const p = await fetchTagNormalizePreview();
      setTagPlan(p);
    } catch (e) {
      setTagResult(String(e));
    } finally {
      setTagPlanLoading(false);
    }
  }

  function clusterKey(c: TagCluster): string {
    // Stable key for state lookup — a fingerprint cluster's members are
    // identical modulo spacing, so joining sorted member names is safe.
    return c.kind + ":" + c.members.map((m) => m.name).sort().join("|");
  }

  async function loadSimilarTags() {
    setSimilarLoading(true);
    try {
      const r = await fetchSimilarTags();
      setFingerprintClusters(r.fingerprint_clusters);
      setFuzzyClusters(r.fuzzy_clusters);
      // Initialize per-cluster state: canonical = suggested; include = all members.
      const init: Record<string, { canonical: string; include: Set<string> }> = {};
      for (const c of [...r.fingerprint_clusters, ...r.fuzzy_clusters]) {
        init[clusterKey(c)] = {
          canonical: c.suggested_canonical,
          include: new Set(c.members.map((m) => m.name)),
        };
      }
      setClusterState(init);
    } catch (e) {
      setSimilarResult(String(e));
    } finally {
      setSimilarLoading(false);
    }
  }

  function updateClusterCanonical(key: string, value: string) {
    setClusterState((prev) => ({
      ...prev,
      [key]: { ...(prev[key] ?? { canonical: "", include: new Set() }), canonical: value },
    }));
  }

  function toggleClusterMember(key: string, name: string) {
    setClusterState((prev) => {
      const state = prev[key] ?? { canonical: name, include: new Set<string>() };
      const nextInclude = new Set(state.include);
      if (nextInclude.has(name)) nextInclude.delete(name); else nextInclude.add(name);
      return { ...prev, [key]: { canonical: state.canonical, include: nextInclude } };
    });
  }

  async function handleMergeCluster(c: TagCluster) {
    const key = clusterKey(c);
    const state = clusterState[key];
    if (!state) return;
    const canonical = state.canonical.trim().toLowerCase();
    if (!canonical) {
      alert("Canonical name is empty — укажи каноничное имя.");
      return;
    }
    const sources = Array.from(state.include).filter((n) => n !== canonical);
    if (sources.length === 0) {
      alert("Нет источников для merge — выбери хотя бы один тег.");
      return;
    }
    if (!confirm(`Merge ${sources.length} tag(s) → "${canonical}"?\n\n  ${sources.join("\n  ")}`)) return;
    try {
      const r = await mergeTags(canonical, sources);
      if (r.error) {
        setSimilarResult(`Error: ${r.error}`);
        return;
      }
      setSimilarResult(`Merged ${r.merged} → "${r.canonical}" (${r.links_remapped} links).`);
      await loadSimilarTags();
    } catch (e) {
      setSimilarResult(String(e));
    }
  }

  async function loadExtractPreview() {
    setExtractLoading(true);
    try {
      const r = await fetchExtractPreview();
      setExtractPreview(r);
      setExtractUnchecked(new Set());
    } catch (e) {
      setExtractResult(String(e));
    } finally {
      setExtractLoading(false);
    }
  }

  function toggleExtractTag(tag: string) {
    setExtractUnchecked((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag); else next.add(tag);
      return next;
    });
  }

  async function handleExtractApply() {
    if (!extractPreview) return;
    const allowed = extractPreview.proposed_tags
      .filter((p) => !extractUnchecked.has(p.tag))
      .map((p) => p.tag);
    if (allowed.length === 0) {
      alert("Все предложенные теги сняты — нечего применять.");
      return;
    }
    const totalAdditions = extractPreview.proposed_tags
      .filter((p) => !extractUnchecked.has(p.tag))
      .reduce((s, p) => s + p.videos, 0);
    if (!confirm(`Добавить ${allowed.length} тегов к ${totalAdditions} видео-ссылкам?`)) return;
    setExtractApplying(true);
    setExtractResult(null);
    try {
      const r = await applyTagExtract(allowed);
      setExtractResult(
        `Applied ${r.applied} links across ${r.videos_touched} videos ` +
        `(${r.tags_created} new tags created).`
      );
      await loadExtractPreview();
    } catch (e) {
      setExtractResult(String(e));
    } finally {
      setExtractApplying(false);
    }
  }

  async function loadScreenFolders() {
    setScreenLoading(true);
    try {
      const r = await fetchScreenFolders();
      setScreenFolders(r.items);
      setScreenTotalSize(r.total_size);
      // Select everything by default — that's the common case ("nuke all").
      setScreenSelected(new Set(r.items.map((it) => it.path)));
    } catch (e) {
      setScreenResult(String(e));
    } finally {
      setScreenLoading(false);
    }
  }

  function toggleScreenSelected(path: string) {
    setScreenSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  }

  async function handleScreenPurge() {
    if (screenSelected.size === 0) return;
    const selectedSize = screenFolders
      .filter((it) => screenSelected.has(it.path))
      .reduce((s, it) => s + it.size, 0);
    if (!confirm(
      `Переместить в Корзину ${screenSelected.size} папок (${formatFileSize(selectedSize)})?\n\n` +
      `Это папки Screens/, _SCREENSHOTS/, *_scr/ — приложение их не использует.`
    )) return;
    setScreenRunning(true);
    setScreenResult(null);
    try {
      const r = await purgeScreenFolders(Array.from(screenSelected));
      const parts = [`Recycled ${r.recycled}`, `freed ${formatFileSize(r.total_bytes_freed)}`];
      if (r.failed) parts.push(`failed ${r.failed}`);
      setScreenResult(parts.join(", "));
      await loadScreenFolders();
    } catch (e) {
      setScreenResult(String(e));
    } finally {
      setScreenRunning(false);
    }
  }

  async function handleTagApply() {
    if (!tagPlan) return;
    const totalChanges = tagPlan.deletes.length + tagPlan.merges.length + tagPlan.renames.length;
    if (totalChanges === 0) {
      setTagResult("Ничего менять не нужно — все теги уже нормализованы.");
      return;
    }
    if (!confirm(
      `Применить:\n  • Rename ${tagPlan.renames.length}\n  • Merge ${tagPlan.merges.length}\n  • Delete ${tagPlan.deletes.length}\n\n` +
      `Итого тегов: ${tagPlan.total_tags_before} → ${tagPlan.total_tags_after}. Продолжить?`
    )) return;
    setTagApplying(true);
    setTagResult(null);
    try {
      const r = await applyTagNormalize();
      setTagResult(
        `Renamed ${r.renamed}, merged ${r.merged_tags} (remapped ${r.links_remapped} links), ` +
        `deleted ${r.deleted_tags} (dropped ${r.links_dropped} links)`
      );
      await loadTagPlan();
    } catch (e) {
      setTagResult(String(e));
    } finally {
      setTagApplying(false);
    }
  }

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

        <p className="text-xs text-white/40">Eligible right now: {compressCandidates} (rule: shorter side &gt; {minHeight}px — orientation-aware)</p>
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

      {/* Replace converted originals — delete WMV/AVI, move MP4 into library */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-white/80">Replace Converted Originals</h2>
        <p className="text-xs text-white/40">
          Для видео, которые уже успешно сконвертированы в MP4: перенести
          MP4 из <code className="text-white/60">converted/</code> на место
          исходного WMV/AVI (с расширением <code className="text-white/60">.mp4</code>),
          а оригинал отправить в Корзину. Строка в БД обновляется, а место
          под <code className="text-white/60">.wmv/.avi</code> освобождается.
        </p>

        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={loadReplaceCandidates} disabled={replaceLoading} className={btnCls}>
            {replaceLoading ? "Scanning..." : "Refresh Candidates"}
          </button>
          <button
            onClick={handleReplaceOriginals}
            disabled={replaceRunning || replaceItems.length === 0}
            className="rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300 hover:bg-red-500/20 transition disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {replaceRunning ? "Replacing..." : `Replace ${replaceItems.length} & Recycle`}
          </button>
          {replaceItems.length > 0 && (
            <span className="text-xs text-white/50">
              Reclaimable: {formatFileSize(replaceReclaimable)}
            </span>
          )}
          {replaceResult && <span className="text-xs text-white/60">{replaceResult}</span>}
        </div>

        <SpoilerToggle
          open={showReplaceList}
          onClick={() => setShowReplaceList((v) => !v)}
          label="candidate list"
          count={replaceItems.length}
        />

        {showReplaceList && replaceItems.length === 0 && !replaceLoading && (
          <p className="text-sm text-white/40">Нет кандидатов. Либо нет завершённых конверсий, либо MP4/оригинал уже отсутствуют на диске.</p>
        )}
        {showReplaceList && replaceItems.length > 0 && (
          <div className="space-y-1.5 max-h-96 overflow-y-auto">
            {replaceItems.map((v) => (
              <div
                key={v.id}
                className="rounded-lg border border-white/10 bg-white/[0.03] p-2 text-xs"
              >
                <p className="truncate text-white/80">{v.title}</p>
                <p className="truncate text-white/40">{v.original_path}</p>
                <p className="text-white/30">{formatFileSize(v.original_size)}</p>
              </div>
            ))}
          </div>
        )}
      </section>

      <hr className="border-white/10" />

      {/* Compress archive — post-compression originals in big_archive_dir */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-white/80">Compress Archive</h2>
        <p className="text-xs text-white/40">
          После сжатия оригинал перемещается в <code className="text-white/60">big_archive_dir</code>
          (по умолчанию <code className="text-white/60">L:\Prvt\big</code>). Если архив на том же диске,
          что и библиотека, место не освобождается. Здесь можно отправить оригиналы в Корзину,
          когда сжатые копии проверены и устраивают.
        </p>

        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={loadArchive} disabled={archiveLoading} className={btnCls}>
            {archiveLoading ? "Scanning..." : "Refresh Archive"}
          </button>
          {archiveInfo && (
            <span className="text-xs text-white/60">
              <code className="text-white/40">{archiveInfo.path}</code>
              {" — "}
              {archiveInfo.exists
                ? `${archiveInfo.file_count} files, ${formatFileSize(archiveInfo.total_size)}`
                : "папки нет"}
            </span>
          )}
          {archiveResult && <span className="text-xs text-white/60">{archiveResult}</span>}
        </div>

        {archiveInfo?.exists && archiveInfo.file_count > 0 && (
          <div className="flex items-center gap-3 flex-wrap text-sm">
            <label className="flex items-center gap-2 text-xs text-white/50">
              Older than
              <input
                type="number"
                min={0}
                value={archiveOlderDays}
                onChange={(e) => setArchiveOlderDays(e.target.value)}
                className="w-16 rounded-md border border-white/10 bg-white/5 px-2 py-1 text-white/80"
              />
              days
            </label>
            <button
              onClick={() => handleArchivePurge("older")}
              disabled={archiveRunning}
              className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-200 hover:bg-amber-500/20 transition disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Recycle older than {archiveOlderDays}d
            </button>
            <button
              onClick={() => handleArchivePurge("selected")}
              disabled={archiveRunning || archiveSelected.size === 0}
              className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-200 hover:bg-amber-500/20 transition disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Recycle {archiveSelected.size} selected
            </button>
            <button
              onClick={() => handleArchivePurge("all")}
              disabled={archiveRunning}
              className="rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300 hover:bg-red-500/20 transition disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {archiveRunning ? "Recycling..." : `Recycle ALL (${formatFileSize(archiveInfo.total_size)})`}
            </button>
          </div>
        )}

        <SpoilerToggle
          open={showArchiveList}
          onClick={() => setShowArchiveList((v) => !v)}
          label="archive file list"
          count={archiveInfo?.file_count ?? null}
        />

        {showArchiveList && archiveInfo && !archiveInfo.exists && (
          <p className="text-sm text-white/40">Папки архива не существует — компрессия ещё не запускалась.</p>
        )}
        {showArchiveList && archiveInfo?.exists && archiveInfo.file_count === 0 && !archiveLoading && (
          <p className="text-sm text-white/40">Архив пуст.</p>
        )}
        {showArchiveList && archiveItems.length > 0 && (
          <div className="space-y-1.5 max-h-96 overflow-y-auto">
            {archiveItems.map((it) => (
              <label
                key={it.path}
                className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] p-2 text-xs cursor-pointer hover:bg-white/[0.06]"
              >
                <input
                  type="checkbox"
                  checked={archiveSelected.has(it.path)}
                  onChange={() => toggleArchiveSelected(it.path)}
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-white/80">{it.name}</p>
                  <p className="truncate text-white/40">{it.path}</p>
                </div>
                <div className="text-right text-white/30 shrink-0">
                  <p>{formatFileSize(it.size)}</p>
                  <p>{it.age_days}d old</p>
                </div>
              </label>
            ))}
          </div>
        )}
      </section>

      <hr className="border-white/10" />

      {/* Screenshot / pack folder cleanup — physical delete from disk */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-white/80">Screenshot / Pack Folders</h2>
        <p className="text-xs text-white/40">
          Папки <code className="text-white/60">Screens/</code>,{" "}
          <code className="text-white/60">_SCREENSHOTS/</code>,{" "}
          <code className="text-white/60">*_scr/</code>,{" "}
          <code className="text-white/60">*Pack_scr/</code> внутри библиотеки. Приложением не
          используются — это остаток от того, как контент скачивался. Папки уходят в Корзину
          вместе со всем содержимым, можно откатить через Windows до её очистки.
        </p>

        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={loadScreenFolders} disabled={screenLoading} className={btnCls}>
            {screenLoading ? "Scanning..." : "Find Screen Folders"}
          </button>
          {screenFolders.length > 0 && (
            <>
              <button
                onClick={handleScreenPurge}
                disabled={screenRunning || screenSelected.size === 0}
                className="rounded-xl border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300 hover:bg-red-500/20 transition disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {screenRunning
                  ? "Recycling..."
                  : `Recycle ${screenSelected.size} selected`}
              </button>
              <span className="text-xs text-white/60">
                Found {screenFolders.length} folders, total {formatFileSize(screenTotalSize)}
              </span>
            </>
          )}
          {screenResult && <span className="text-xs text-white/60">{screenResult}</span>}
        </div>

        <SpoilerToggle
          open={showScreenList}
          onClick={() => setShowScreenList((v) => !v)}
          label="folder list"
          count={screenFolders.length}
        />

        {showScreenList && screenFolders.length === 0 && !screenLoading && (
          <p className="text-sm text-white/40">
            Запусти "Find Screen Folders". Если ничего не найдено — чисто.
          </p>
        )}
        {showScreenList && screenFolders.length > 0 && (
          <div className="space-y-1.5 max-h-[32rem] overflow-y-auto">
            {screenFolders.map((it) => (
              <label
                key={it.path}
                className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] p-2 text-xs cursor-pointer hover:bg-white/[0.06]"
              >
                <input
                  type="checkbox"
                  checked={screenSelected.has(it.path)}
                  onChange={() => toggleScreenSelected(it.path)}
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-white/80">{it.name}</p>
                  <p className="truncate text-white/40">{it.path}</p>
                </div>
                <div className="text-right text-white/30 shrink-0">
                  <p>{formatFileSize(it.size)}</p>
                  <p>{it.file_count} files</p>
                </div>
              </label>
            ))}
          </div>
        )}
      </section>

      <hr className="border-white/10" />

      {/* Tag normalizer — fold count/site suffixes, drop service folders */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-white/80">Tag Normalizer</h2>
        <p className="text-xs text-white/40">
          Свести теги, построенные из имён папок, к каноничному виду: убрать
          <code className="text-white/60"> (66)</code>, <code className="text-white/60">.com</code>,
          <code className="text-white/60"> _scr</code>, <code className="text-white/60">Pack_scr</code>
          , схлопнуть эквиваленты (<code className="text-white/60">GuysForMatures</code>,{" "}
          <code className="text-white/60">GuysForMatures.com</code>,{" "}
          <code className="text-white/60">GuysForMatures (140)</code> → один тег), удалить
          служебные (<code className="text-white/60">screens</code>,{" "}
          <code className="text-white/60">incoming</code>, <code className="text-white/60">squized</code>, ...).
          Сначала смотрим план — потом применяем.
        </p>

        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={loadTagPlan} disabled={tagPlanLoading} className={btnCls}>
            {tagPlanLoading ? "Computing..." : "Preview Plan"}
          </button>
          {tagPlan && (
            <button
              onClick={handleTagApply}
              disabled={tagApplying}
              className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-200 hover:bg-amber-500/20 transition disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {tagApplying ? "Applying..." : "Apply Normalization"}
            </button>
          )}
          {tagPlan && (
            <span className="text-xs text-white/60">
              {tagPlan.total_tags_before} → {tagPlan.total_tags_after} tags
              {" ("}
              rename {tagPlan.renames.length}, merge {tagPlan.merges.length},
              delete {tagPlan.deletes.length}
              {")"}
            </span>
          )}
          {tagResult && <span className="text-xs text-white/60">{tagResult}</span>}
        </div>

        <SpoilerToggle
          open={showTagPlan}
          onClick={() => setShowTagPlan((v) => !v)}
          label="plan details"
          count={tagPlan ? tagPlan.deletes.length + tagPlan.merges.length + tagPlan.renames.length : null}
        />

        {showTagPlan && tagPlan && (
          <div className="space-y-4 text-xs">
            {tagPlan.merges.length > 0 && (
              <div>
                <h3 className="text-sm text-white/70 mb-1">Merges ({tagPlan.merges.length})</h3>
                <div className="space-y-1 max-h-64 overflow-y-auto">
                  {tagPlan.merges.map((m) => (
                    <div key={m.canonical} className="rounded border border-white/10 bg-white/[0.03] p-2">
                      <p className="text-white/80">
                        <span className="text-amber-300">{m.canonical}</span>
                        {" ← "}
                        <span className="text-white/60">{m.sources.join(", ")}</span>
                      </p>
                      <p className="text-white/40">{m.videos_affected} videos affected</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {tagPlan.renames.length > 0 && (
              <div>
                <h3 className="text-sm text-white/70 mb-1">Renames ({tagPlan.renames.length})</h3>
                <div className="space-y-1 max-h-64 overflow-y-auto">
                  {tagPlan.renames.map((r) => (
                    <div key={r.from} className="rounded border border-white/10 bg-white/[0.03] p-2">
                      <p className="text-white/80">
                        <span className="text-white/50 line-through">{r.from}</span>
                        {" → "}
                        <span className="text-green-300">{r.to}</span>
                        <span className="text-white/30 ml-2">({r.videos} videos)</span>
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {tagPlan.deletes.length > 0 && (
              <div>
                <h3 className="text-sm text-white/70 mb-1">Deletes ({tagPlan.deletes.length})</h3>
                <div className="space-y-1 max-h-64 overflow-y-auto">
                  {tagPlan.deletes.map((d) => (
                    <div key={d.name} className="rounded border border-white/10 bg-white/[0.03] p-2">
                      <p className="text-white/80">
                        <span className="text-red-300 line-through">{d.name}</span>
                        <span className="text-white/30 ml-2">({d.videos} videos lose this tag)</span>
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {tagPlan.merges.length === 0 && tagPlan.renames.length === 0 && tagPlan.deletes.length === 0 && (
              <p className="text-sm text-white/40">План пуст — теги уже нормализованы.</p>
            )}
          </div>
        )}
      </section>

      <hr className="border-white/10" />

      {/* Similar Tags — fingerprint + fuzzy clusters, manual merge */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-white/80">Similar Tags (dedup)</h2>
        <p className="text-xs text-white/40">
          То, что нормализатор схлопнуть не смог: разные написания одного имени
          (<code className="text-white/60">valentina nappi</code> /{" "}
          <code className="text-white/60">valentinanappi</code>), опечатки
          (<code className="text-white/60">Nappi</code> /{" "}
          <code className="text-white/60">Napi</code>). Для каждой группы укажи
          каноничное имя (можно ввести новое — будет создано), сними галки с
          тегов, которые на самом деле <b>не</b> эквивалентны, и нажми Merge.
        </p>

        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={loadSimilarTags} disabled={similarLoading} className={btnCls}>
            {similarLoading ? "Scanning..." : "Find Similar Tags"}
          </button>
          {(fingerprintClusters.length > 0 || fuzzyClusters.length > 0) && (
            <span className="text-xs text-white/60">
              {fingerprintClusters.length} exact-equivalent, {fuzzyClusters.length} fuzzy
            </span>
          )}
          {similarResult && <span className="text-xs text-white/60">{similarResult}</span>}
        </div>

        <SpoilerToggle
          open={showSimilar}
          onClick={() => setShowSimilar((v) => !v)}
          label="clusters"
          count={fingerprintClusters.length + fuzzyClusters.length}
        />

        {showSimilar && (
          <div className="space-y-6">
            {[...fingerprintClusters.map((c) => [c, "high" as const] as const),
              ...fuzzyClusters.map((c) => [c, "medium" as const] as const)
            ].length === 0 && !similarLoading && (
              <p className="text-sm text-white/40">Ничего похожего — чисто.</p>
            )}

            {fingerprintClusters.length > 0 && (
              <div>
                <h3 className="text-sm text-white/70 mb-2">
                  Exact-equivalent ({fingerprintClusters.length}) — высокая уверенность
                </h3>
                <div className="space-y-2">
                  {fingerprintClusters.map((c) => {
                    const key = clusterKey(c);
                    const state = clusterState[key];
                    return (
                      <div key={key} className="rounded border border-white/10 bg-white/[0.03] p-3 text-xs space-y-2">
                        <div className="flex items-center gap-2 flex-wrap">
                          <label className="text-white/50">Canonical:</label>
                          <input
                            type="text"
                            value={state?.canonical ?? c.suggested_canonical}
                            onChange={(e) => updateClusterCanonical(key, e.target.value)}
                            className="rounded bg-white/10 border border-white/10 px-2 py-1 text-white/80 min-w-[200px]"
                          />
                          <button
                            onClick={() => handleMergeCluster(c)}
                            className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1 text-amber-200 hover:bg-amber-500/20 transition"
                          >
                            Merge {Array.from(state?.include ?? []).filter((n) => n !== (state?.canonical ?? "")).length}
                          </button>
                          <span className="text-white/30">
                            {c.total_videos} videos total
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {c.members.map((m) => (
                            <label
                              key={m.name}
                              className={`rounded px-2 py-1 cursor-pointer transition ${
                                state?.include.has(m.name)
                                  ? "bg-amber-500/20 border border-amber-500/40 text-amber-100"
                                  : "bg-white/5 border border-white/10 text-white/40 line-through"
                              }`}
                            >
                              <input
                                type="checkbox"
                                className="hidden"
                                checked={state?.include.has(m.name) ?? false}
                                onChange={() => toggleClusterMember(key, m.name)}
                              />
                              {m.name} <span className="text-white/40">({m.videos})</span>
                            </label>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {fuzzyClusters.length > 0 && (
              <div>
                <h3 className="text-sm text-white/70 mb-2">
                  Fuzzy matches ({fuzzyClusters.length}) — проверь визуально
                </h3>
                <div className="space-y-2">
                  {fuzzyClusters.map((c) => {
                    const key = clusterKey(c);
                    const state = clusterState[key];
                    return (
                      <div key={key} className="rounded border border-white/10 bg-white/[0.03] p-3 text-xs space-y-2">
                        <div className="flex items-center gap-2 flex-wrap">
                          <label className="text-white/50">Canonical:</label>
                          <input
                            type="text"
                            value={state?.canonical ?? c.suggested_canonical}
                            onChange={(e) => updateClusterCanonical(key, e.target.value)}
                            className="rounded bg-white/10 border border-white/10 px-2 py-1 text-white/80 min-w-[200px]"
                          />
                          <button
                            onClick={() => handleMergeCluster(c)}
                            className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1 text-amber-200 hover:bg-amber-500/20 transition"
                          >
                            Merge {Array.from(state?.include ?? []).filter((n) => n !== (state?.canonical ?? "")).length}
                          </button>
                          <span className="text-white/30">
                            {c.total_videos} videos total
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {c.members.map((m) => (
                            <label
                              key={m.name}
                              className={`rounded px-2 py-1 cursor-pointer transition ${
                                state?.include.has(m.name)
                                  ? "bg-amber-500/20 border border-amber-500/40 text-amber-100"
                                  : "bg-white/5 border border-white/10 text-white/40 line-through"
                              }`}
                            >
                              <input
                                type="checkbox"
                                className="hidden"
                                checked={state?.include.has(m.name) ?? false}
                                onChange={() => toggleClusterMember(key, m.name)}
                              />
                              {m.name} <span className="text-white/40">({m.videos})</span>
                            </label>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      <hr className="border-white/10" />

      {/* Tag extraction from filenames */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-white/80">Extract Tags from Filenames</h2>
        <p className="text-xs text-white/40">
          Сканирует активные имена файлов и предлагает добавить теги:
          <br />
          {" • "}<b>студия-префикс</b> из начала имени до первого{" "}
          <code className="text-white/60">_</code>:{" "}
          <code className="text-white/60">stunningmatures_g603_…</code> →{" "}
          <code className="text-white/60">stunningmatures</code>.
          {" "}Короткие аббревиатуры (<code className="text-white/60">stm</code>,{" "}
          <code className="text-white/60">gfm</code>) автоматически разворачиваются
          в полное имя родительской папки, если префикс является{" "}
          <i>letter-ordered contraction</i> первого слова папки (т.е.{" "}
          <code className="text-white/60">stm</code> ⊂{" "}
          <code className="text-white/60">stunningmatures</code>,{" "}
          но <code className="text-white/60">fsm</code> ⊄{" "}
          <code className="text-white/60">femaleagent</code>). Если папка не
          раскрывает — фолбэк на ручной мап{" "}
          <code className="text-white/60">STUDIO_ABBREVIATIONS</code> в{" "}
          <code className="text-white/60">backend/services/tag_extract.py</code>.
          <br />
          {" • "}<b>имена через &amp;</b>:{" "}
          <code className="text-white/60">Emilia&amp;Arthur</code> →{" "}
          <code className="text-white/60">emilia</code>, <code className="text-white/60">arthur</code>.
          Каждое имя должно быть с заглавной (иначе ловим мусор типа{" "}
          <code className="text-white/60">clip&amp;scene</code>).
          <br />
          {" • "}сайты <code className="text-white/60">[SomeSite.com]</code>, acronyms{" "}
          <code className="text-white/60">[OF] → onlyfans</code>, качество{" "}
          <code className="text-white/60">4k/1080p</code>, кодек{" "}
          <code className="text-white/60">hevc/h264</code>.
          <br />
          Превью показывает только <b>новые</b> связи — уже проставленные не дублируются.
        </p>

        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={loadExtractPreview} disabled={extractLoading} className={btnCls}>
            {extractLoading ? "Scanning..." : "Preview Extracted Tags"}
          </button>
          {extractPreview && (
            <>
              <button
                onClick={handleExtractApply}
                disabled={extractApplying || extractPreview.total_tags === 0}
                className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-200 hover:bg-amber-500/20 transition disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {extractApplying ? "Applying..." : `Apply (${extractPreview.proposed_tags.filter((p) => !extractUnchecked.has(p.tag)).length} tags)`}
              </button>
              <span className="text-xs text-white/60">
                {extractPreview.total_tags} proposed tags, {extractPreview.total_additions} new links
                {" · "}
                {extractPreview.videos_touched} videos affected
              </span>
            </>
          )}
          {extractResult && <span className="text-xs text-white/60">{extractResult}</span>}
        </div>

        <SpoilerToggle
          open={showExtractList}
          onClick={() => setShowExtractList((v) => !v)}
          label="proposed tags"
          count={extractPreview?.proposed_tags.length ?? null}
        />

        {showExtractList && extractPreview && extractPreview.proposed_tags.length === 0 && !extractLoading && (
          <p className="text-sm text-white/40">Ничего не нашлось — все возможные теги уже проставлены.</p>
        )}
        {showExtractList && extractPreview && extractPreview.proposed_tags.length > 0 && (
          <div className="space-y-2 max-h-[36rem] overflow-y-auto">
            {extractPreview.proposed_tags.map((p) => (
              <div key={p.tag} className="rounded border border-white/10 bg-white/[0.03] p-2 text-xs">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={!extractUnchecked.has(p.tag)}
                    onChange={() => toggleExtractTag(p.tag)}
                  />
                  <span className={extractUnchecked.has(p.tag) ? "text-white/40 line-through" : "text-white/80"}>
                    <span className="font-mono text-amber-300">{p.tag}</span>
                    <span className="text-white/40 ml-2">→ {p.videos} videos</span>
                  </span>
                </label>
                {p.sample_videos.length > 0 && (
                  <div className="mt-1 pl-6 space-y-0.5">
                    {p.sample_videos.map((v) => (
                      <p key={v.id} className="truncate text-white/40">{v.filename}</p>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <hr className="border-white/10" />

      {/* Locked / orphan files — soft-deleted DB row, file still on disk */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-white/80">Locked / Orphan Files</h2>
        <p className="text-xs text-white/40">
          Видео, у которых строка в БД помечена удалённой, но файл остался на диске
          (обычно потому, что во время удаления он был залочен стримом).
        </p>
        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={loadOrphans} disabled={orphanLoading} className={btnCls}>
            {orphanLoading ? "Scanning..." : `Find Locked (${orphans.length})`}
          </button>
          {orphans.length > 0 && (
            <button onClick={handleRetryAllOrphans} className={btnCls}>
              Retry All to Recycle Bin
            </button>
          )}
          {orphanResult && <span className="text-xs text-white/40">{orphanResult}</span>}
        </div>

        {orphans.length > 0 && (
          <div className="space-y-2">
            {orphans.map((v) => (
              <div
                key={v.id}
                className="rounded-2xl border border-white/10 bg-white/[0.04] p-3 flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between"
              >
                <div className="min-w-0 space-y-1">
                  <p className="truncate text-sm text-white">{v.original_filename}</p>
                  <p className="truncate text-xs text-white/40">{v.original_path}</p>
                  <p className="text-xs text-white/30">
                    {formatFileSize(v.file_size)} • soft-deleted {v.deleted_at?.slice(0, 19).replace("T", " ")}
                  </p>
                </div>
                <div className="flex gap-2 shrink-0">
                  <button
                    onClick={() => handleRetryOrphan(v.id)}
                    disabled={retryingOrphanId === v.id}
                    className="rounded-xl border border-accent/30 bg-accent/10 px-4 py-2 text-sm text-accent hover:bg-accent/20 transition disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {retryingOrphanId === v.id ? "Trying..." : "Retry Recycle"}
                  </button>
                </div>
              </div>
            ))}
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
          <button
            onClick={handlePaletteSelected}
            disabled={paletteSelected.size === 0}
            className="rounded-xl border border-accent/30 bg-accent/10 px-4 py-2 text-sm text-accent hover:bg-accent/20 transition disabled:cursor-not-allowed disabled:opacity-50"
          >
            Generate Selected First ({paletteSelected.size})
          </button>
          <button onClick={selectAllPalette} className={btnCls}>Select All</button>
          <button onClick={clearPaletteSelection} className={btnCls}>Clear Selection</button>
          <button onClick={() => { loadPaletteStatus(); loadPaletteMissingCount(); loadPaletteCandidates(); }} className={btnCls}>
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

        {/* Sort + spoiler toggle row */}
        <div className="flex items-center gap-3 flex-wrap">
          <SpoilerToggle
            open={showPaletteList}
            onClick={() => setShowPaletteList((v) => !v)}
            label="candidate list"
            count={paletteTotal || paletteMissing || 0}
          />
          {showPaletteList && (
            <>
              <label className="text-xs text-white/45">Sort by:</label>
              <select
                value={paletteSort}
                onChange={(e) => { setPaletteSort(e.target.value as PaletteSort); setPalettePage(0); }}
                className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-white focus:outline-none focus:border-accent/50"
              >
                <option value="name">Name</option>
                <option value="size_desc">Largest first</option>
                <option value="size_asc">Smallest first</option>
                <option value="duration_desc">Longest first</option>
                <option value="duration_asc">Shortest first</option>
              </select>
              {paletteLoading && <span className="text-xs text-white/40">Loading...</span>}
            </>
          )}
        </div>

        {showPaletteList && paletteItems.length === 0 && !paletteLoading && (
          <p className="text-sm text-white/40">No videos missing a palette.</p>
        )}
        {showPaletteList && paletteItems.length > 0 && (
          <div className="space-y-3">
            {paletteItems.map((video) => {
              const isSelected = paletteSelected.has(video.id);
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
                        onChange={() => togglePaletteSelected(video.id)}
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
                          <span>{video.codec ?? "unknown"}</span>
                        </div>
                      </div>

                      <div className="flex flex-wrap justify-end gap-2">
                        <button
                          onClick={() => handlePaletteOne(video)}
                          disabled={paletteGeneratingId === video.id}
                          className="rounded-xl border border-accent/30 bg-accent/10 px-4 py-2 text-sm text-accent hover:bg-accent/20 transition disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {paletteGeneratingId === video.id ? "Queuing..." : "Generate Palette"}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {showPaletteList && paletteTotal > PALETTE_PAGE_SIZE && (
          <div className="flex items-center justify-between gap-3 pt-2">
            <button
              onClick={() => setPalettePage((p) => Math.max(0, p - 1))}
              disabled={palettePage === 0 || paletteLoading}
              className={btnCls + " disabled:opacity-40 disabled:cursor-not-allowed"}
            >
              &larr; Prev
            </button>
            <span className="text-xs text-white/45">
              Page {palettePage + 1} / {Math.max(1, Math.ceil(paletteTotal / PALETTE_PAGE_SIZE))}
              {" • "}
              Showing {palettePage * PALETTE_PAGE_SIZE + 1}-
              {Math.min((palettePage + 1) * PALETTE_PAGE_SIZE, paletteTotal)} of {paletteTotal}
            </span>
            <button
              onClick={() => setPalettePage((p) => p + 1)}
              disabled={(palettePage + 1) * PALETTE_PAGE_SIZE >= paletteTotal || paletteLoading}
              className={btnCls + " disabled:opacity-40 disabled:cursor-not-allowed"}
            >
              Next &rarr;
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
