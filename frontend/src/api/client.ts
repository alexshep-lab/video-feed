export function resolveApiBase(): string {
  const configured = import.meta.env.VITE_API_BASE?.trim();
  if (configured) return configured.replace(/\/+$/, "");

  const { protocol, hostname, origin, port } = window.location;
  if (port === "5173") {
    return `${protocol}//${hostname}:7999/api`;
  }

  return `${origin}/api`;
}

export const API_BASE = resolveApiBase();

export type VideoItem = {
  id: string;
  title: string;
  description: string | null;
  original_filename: string;
  original_path: string;
  duration: number | null;
  width: number | null;
  height: number | null;
  file_size: number;
  codec: string | null;
  transcode_status: string;
  transcode_progress: number;
  thumbnail_path: string | null;
  library_path: string | null;
  category: string | null;
  is_vertical: boolean;
  favorite: boolean;
  confirmed: boolean;
  view_count: number;
  total_watch_time: number;
  last_watched_at: string | null;
  deleted_at: string | null;
  compress_status: string;
  compress_progress: number;
  compressed_size: number | null;
  convert_status: string;
  convert_progress: number;
  added_at: string;
  tag_list: string[];
  raw_stream_url: string;
  hls_stream_url: string | null;
  thumbnail_url: string;
  preview_frame_template_url: string;
};

export type VideoDetail = VideoItem & {
  original_path: string;
  hls_path: string | null;
  tags: string | null;
};

export type VideoUpdate = {
  title?: string;
  description?: string;
  category?: string;
  favorite?: boolean;
  confirmed?: boolean;
  tag_list?: string[];
};

export type FilterOptions = {
  categories: string[];
  codecs: string[];
  libraries: LibraryFolder[];
  tags: Tag[];
};

export type Tag = { id: number; name: string; video_count: number };
export type LibraryFolder = { id: number; path: string; enabled: boolean; is_incoming: boolean; display_name: string | null; video_count: number };

export type VideoFilters = {
  q?: string;
  tag?: string;
  tags?: string[];
  tag_mode?: "any" | "all";
  category?: string;
  library?: string;
  codec?: string;
  duration_min?: number;
  duration_max?: number;
  sort?: string;
  shuffle_seed?: number;
  offset?: number;
  limit?: number;
  confirmed?: boolean;
  favorite?: boolean;
  ready?: boolean;
};

export async function fetchVideos(filters: VideoFilters = {}): Promise<VideoItem[]> {
  const url = new URL(`${API_BASE}/videos`);
  Object.entries(filters).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    if (Array.isArray(v)) {
      v.forEach((item) => { if (item !== "" && item !== null && item !== undefined) url.searchParams.append(k, String(item)); });
    } else {
      url.searchParams.set(k, String(v));
    }
  });
  const response = await fetch(url);
  if (!response.ok) throw new Error("Failed to load videos");
  return response.json();
}

export async function fetchFilters(): Promise<FilterOptions> {
  const response = await fetch(`${API_BASE}/videos/filters`);
  if (!response.ok) throw new Error("Failed to load filters");
  return response.json();
}

export async function fetchVideo(videoId: string): Promise<VideoDetail> {
  const response = await fetch(`${API_BASE}/videos/${videoId}`);
  if (!response.ok) throw new Error("Failed to load video");
  return response.json();
}

export async function updateVideo(videoId: string, data: VideoUpdate): Promise<VideoDetail> {
  const response = await fetch(`${API_BASE}/videos/${videoId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error("Failed to update video");
  return response.json();
}

export async function deleteVideo(videoId: string, hard = false, recycle = false): Promise<{ status?: string; move_error?: string }> {
  const r = await fetch(`${API_BASE}/videos/${videoId}?hard=${hard}&recycle=${recycle}`, { method: "DELETE" });
  try { return await r.json(); } catch { return {}; }
}

export async function startTranscode(videoId: string): Promise<void> {
  await fetch(`${API_BASE}/transcode/start/${videoId}`, { method: "POST" });
}

export async function startCompress(videoId: string): Promise<void> {
  await fetch(`${API_BASE}/maintenance/compress/${videoId}`, { method: "POST" });
}

export async function ignoreCompress(videoId: string): Promise<void> {
  await fetch(`${API_BASE}/maintenance/compress/${videoId}/ignore`, { method: "POST" });
}

export async function recordWatchEvent(videoId: string, duration = 0): Promise<void> {
  await fetch(`${API_BASE}/stats/watch-event?video_id=${videoId}&duration=${duration}`, { method: "POST" });
}

export async function updateWatchTime(videoId: string, seconds: number): Promise<void> {
  await fetch(`${API_BASE}/stats/update-watch-time?video_id=${videoId}&seconds=${seconds}`, { method: "POST" });
}

// Libraries
export async function fetchLibraries(): Promise<LibraryFolder[]> {
  const r = await fetch(`${API_BASE}/libraries`);
  return r.json();
}

export async function addLibrary(path: string): Promise<LibraryFolder[]> {
  const r = await fetch(`${API_BASE}/libraries`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function scanLibrary(): Promise<{ created: number; updated: number; scanned_files: number }> {
  const r = await fetch(`${API_BASE}/transcode/scan`, { method: "POST" });
  return r.json();
}

export type ScanProgress = {
  running: boolean;
  total_files: number;
  processed: number;
  created: number;
  phase: string;
};

export async function fetchScanProgress(): Promise<ScanProgress> {
  const r = await fetch(`${API_BASE}/transcode/scan/progress`);
  if (!r.ok) throw new Error("Failed to load scan progress");
  return r.json();
}

// Recommendations
export async function fetchRecommendations(videoId: string, limit = 8): Promise<VideoItem[]> {
  const r = await fetch(`${API_BASE}/videos/${videoId}/recommendations?limit=${limit}`);
  if (!r.ok) throw new Error("Failed to load recommendations");
  return r.json();
}

// Duplicates
export async function fetchExactDuplicates(): Promise<{ count: number; groups: VideoItem[][] }> {
  const r = await fetch(`${API_BASE}/maintenance/duplicates/exact`);
  return r.json();
}

export async function fetchPerceptualDuplicates(threshold = 5): Promise<{ count: number; groups: VideoItem[][] }> {
  const r = await fetch(`${API_BASE}/maintenance/duplicates/perceptual?threshold=${threshold}`);
  return r.json();
}

export async function computeHashes(): Promise<{ status: string; computed: number }> {
  const r = await fetch(`${API_BASE}/maintenance/duplicates/compute-hashes`, { method: "POST" });
  return r.json();
}

// Compression
export async function compressOversized(
  minHeight = 1440,
  force = false,
): Promise<{ status: string; count: number; eligible: number; min_height: number; force: boolean }> {
  const r = await fetch(`${API_BASE}/maintenance/compress/oversized?min_height=${minHeight}&force=${force}`, { method: "POST" });
  return r.json();
}

export async function fetchCompressCandidates(
  minHeight = 1440,
  force = false,
): Promise<{ eligible: number; min_height: number; force: boolean }> {
  const r = await fetch(`${API_BASE}/maintenance/compress/candidates?min_height=${minHeight}&force=${force}`);
  return r.json();
}

export type CompressCandidate = VideoItem & {
  target_filename: string;
};

export async function fetchCompressCandidateList(
  minHeight = 1440,
  force = false,
  limit = 200,
): Promise<{ eligible: number; items: CompressCandidate[]; min_height: number; force: boolean }> {
  const r = await fetch(
    `${API_BASE}/maintenance/compress/candidates/list?min_height=${minHeight}&force=${force}&limit=${limit}`,
  );
  return r.json();
}

export async function fetchCompressStatus(): Promise<{
  queue_size: number;
  current_video_id: string | null;
  current_video_title: string | null;
  current_progress: number;
  worker_running: boolean;
  batch_total_jobs: number;
  batch_completed_jobs: number;
  batch_failed_jobs: number;
  overall_progress: number;
}> {
  const r = await fetch(`${API_BASE}/maintenance/compress/status`);
  return r.json();
}

export async function stopCompress(): Promise<{ dropped_queued: number; killed_current: boolean; interrupted_video_id: string | null }> {
  const r = await fetch(`${API_BASE}/maintenance/compress/stop`, { method: "POST" });
  return r.json();
}

// ---- Browser-friendly conversion (WMV/AVI -> MP4) ----

export type ConvertStatus = {
  queue_size: number;
  current_video_id: string | null;
  current_video_title: string | null;
  current_progress: number;
  worker_running: boolean;
  batch_total_jobs: number;
  batch_completed_jobs: number;
  batch_failed_jobs: number;
  overall_progress: number;
  encoder: string;
};

export async function fetchConvertStatus(): Promise<ConvertStatus> {
  const r = await fetch(`${API_BASE}/maintenance/convert/status`);
  return r.json();
}

export type ConvertSort = "h264_first" | "size_asc" | "size_desc" | "name";

export type ConvertCandidatesResponse = {
  total: number;
  eligible: number;
  limit: number;
  offset: number;
  sort: ConvertSort;
  sort_options: Record<string, string>;
  items: VideoItem[];
};

export async function fetchConvertCandidates(
  limit = 20,
  offset = 0,
  sort: ConvertSort = "h264_first",
): Promise<ConvertCandidatesResponse> {
  const url = new URL(`${API_BASE}/maintenance/convert/candidates`);
  url.searchParams.set("limit", String(limit));
  url.searchParams.set("offset", String(offset));
  url.searchParams.set("sort", sort);
  const r = await fetch(url);
  return r.json();
}

export async function convertOne(videoId: string): Promise<{ status: string; video_id?: string; reason?: string }> {
  const r = await fetch(`${API_BASE}/maintenance/convert/${videoId}`, { method: "POST" });
  return r.json();
}

export async function convertAllPending(): Promise<{ status: string; count: number }> {
  const r = await fetch(`${API_BASE}/maintenance/convert/all`, { method: "POST" });
  return r.json();
}

export async function stopConvert(): Promise<{ dropped_queued: number; killed_current: boolean; interrupted_video_id: string | null }> {
  const r = await fetch(`${API_BASE}/maintenance/convert/stop`, { method: "POST" });
  return r.json();
}

export async function convertBatch(videoIds: string[]): Promise<{ status: string; queued: number; skipped: { id: string; reason: string }[] }> {
  const r = await fetch(`${API_BASE}/maintenance/convert/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_ids: videoIds }),
  });
  return r.json();
}

export type ConvertedOriginal = {
  id: string;
  title: string;
  original_path: string;
  original_size: number;
  converted_path: string;
};

export async function fetchConvertedOriginals(): Promise<{ count: number; reclaimable_bytes: number; items: ConvertedOriginal[] }> {
  const r = await fetch(`${API_BASE}/maintenance/converted-originals`);
  return r.json();
}

export async function replaceConvertedOriginals(): Promise<{
  replaced: number;
  skipped_collision: number;
  move_failed: number;
  recycle_failed: number;
  errors: { id: string; reason: string; error?: string; target?: string }[];
}> {
  const r = await fetch(`${API_BASE}/maintenance/converted-originals/replace`, { method: "POST" });
  return r.json();
}

export async function fetchEncoderInfo(): Promise<{ effective: string; nvenc_available: boolean }> {
  const r = await fetch(`${API_BASE}/maintenance/encoder`);
  return r.json();
}

// ---- Compress archive (big_archive_dir) management ----

export type ArchiveItem = {
  path: string;
  name: string;
  size: number;
  mtime: number;
  age_days: number;
};

export type ArchiveListing = {
  path: string;
  exists: boolean;
  total_size: number;
  file_count: number;
  items: ArchiveItem[];
};

export async function fetchCompressArchive(): Promise<ArchiveListing> {
  const r = await fetch(`${API_BASE}/maintenance/compress/archive`);
  return r.json();
}

export async function purgeCompressArchive(body: {
  older_than_days?: number | null;
  paths?: string[] | null;
}): Promise<{
  recycled: number;
  failed: number;
  total_bytes_freed: number;
  errors: { path: string; error: string }[];
}> {
  const r = await fetch(`${API_BASE}/maintenance/compress/archive/purge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

// ---- Missing files (DB row exists, source file gone) ----

export type MissingFileItem = {
  id: string;
  title: string;
  original_filename: string;
  original_path: string;
};

export async function fetchMissingFiles(): Promise<{ count: number; items: MissingFileItem[] }> {
  const r = await fetch(`${API_BASE}/maintenance/missing-files`);
  return r.json();
}

export async function purgeMissingFiles(): Promise<{ purged: number }> {
  const r = await fetch(`${API_BASE}/maintenance/missing-files/purge`, { method: "POST" });
  return r.json();
}

// ---- Tag normalization ----

export type TagNormalizePlan = {
  dry_run: boolean;
  total_tags_before: number;
  total_tags_after: number;
  deletes: { name: string; videos: number }[];
  merges: { canonical: string; sources: string[]; videos_affected: number }[];
  renames: { from: string; to: string; videos: number }[];
};

export async function fetchTagNormalizePreview(): Promise<TagNormalizePlan> {
  const r = await fetch(`${API_BASE}/maintenance/tags/normalize-preview`);
  return r.json();
}

export async function applyTagNormalize(): Promise<{
  dry_run: boolean;
  renamed: number;
  merged_tags: number;
  links_remapped: number;
  deleted_tags: number;
  links_dropped: number;
}> {
  const r = await fetch(`${API_BASE}/maintenance/tags/normalize`, { method: "POST" });
  return r.json();
}

// ---- Screenshot / pack folder cleanup ----

export type ScreenFolderItem = {
  path: string;
  name: string;
  size: number;
  file_count: number;
};

export async function fetchScreenFolders(): Promise<{
  count: number;
  total_size: number;
  items: ScreenFolderItem[];
}> {
  const r = await fetch(`${API_BASE}/maintenance/library/screen-folders`);
  return r.json();
}

export async function purgeScreenFolders(paths: string[]): Promise<{
  recycled: number;
  failed: number;
  total_bytes_freed: number;
  errors: { path: string; error: string }[];
}> {
  const r = await fetch(`${API_BASE}/maintenance/library/screen-folders/purge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  return r.json();
}

// ---- Tag dedup (similar clusters + manual merge) ----

export type TagClusterMember = { id: number; name: string; videos: number };
export type TagCluster = {
  kind: "fingerprint" | "fuzzy";
  suggested_canonical: string;
  total_videos: number;
  members: TagClusterMember[];
};

export async function fetchSimilarTags(): Promise<{
  fingerprint_clusters: TagCluster[];
  fuzzy_clusters: TagCluster[];
}> {
  const r = await fetch(`${API_BASE}/maintenance/tags/similar`);
  return r.json();
}

export async function mergeTags(canonical: string, sources: string[]): Promise<{
  merged: number;
  links_remapped: number;
  canonical: string;
  created: boolean;
  error?: string;
}> {
  const r = await fetch(`${API_BASE}/maintenance/tags/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ canonical, sources }),
  });
  return r.json();
}

// ---- Tag extraction from filenames ----

export type ExtractPreview = {
  total_tags: number;
  total_additions: number;
  videos_touched: number;
  proposed_tags: {
    tag: string;
    videos: number;
    sample_videos: { id: string; title: string; filename: string }[];
  }[];
};

export async function fetchExtractPreview(): Promise<ExtractPreview> {
  const r = await fetch(`${API_BASE}/maintenance/tags/extract-preview`);
  return r.json();
}

export async function applyTagExtract(tagWhitelist: string[] | null): Promise<{
  applied: number;
  tags_created: number;
  tags_reused: number;
  videos_touched: number;
}> {
  const r = await fetch(`${API_BASE}/maintenance/tags/extract`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tag_whitelist: tagWhitelist }),
  });
  return r.json();
}

// ---- Video palette (contact sheet) batch generation ----

export type PaletteStatus = {
  queue_size: number;
  current_video_id: string | null;
  current_video_title: string | null;
  worker_running: boolean;
  batch_total_jobs: number;
  batch_completed_jobs: number;
  batch_failed_jobs: number;
  overall_progress: number;
};

export async function fetchPaletteStatus(): Promise<PaletteStatus> {
  const r = await fetch(`${API_BASE}/maintenance/palettes/status`);
  return r.json();
}

export async function fetchPaletteMissingCount(): Promise<{ missing: number }> {
  const r = await fetch(`${API_BASE}/maintenance/palettes/missing-count`);
  return r.json();
}

export async function generateAllPalettes(): Promise<{ status: string; count: number }> {
  const r = await fetch(`${API_BASE}/maintenance/palettes/generate-all`, { method: "POST" });
  return r.json();
}

export type PaletteSort = "name" | "size_desc" | "size_asc" | "duration_desc" | "duration_asc";

export type PaletteCandidatesResponse = {
  total: number;
  limit: number;
  offset: number;
  sort: PaletteSort;
  sort_options: Record<string, string>;
  items: VideoItem[];
};

export async function fetchPaletteCandidates(
  limit = 20,
  offset = 0,
  sort: PaletteSort = "name",
): Promise<PaletteCandidatesResponse> {
  const url = new URL(`${API_BASE}/maintenance/palettes/candidates`);
  url.searchParams.set("limit", String(limit));
  url.searchParams.set("offset", String(offset));
  url.searchParams.set("sort", sort);
  const r = await fetch(url);
  return r.json();
}

export async function generatePaletteOne(videoId: string): Promise<{ status: string; video_id?: string }> {
  const r = await fetch(`${API_BASE}/maintenance/palettes/generate/${videoId}`, { method: "POST" });
  return r.json();
}

export async function generatePalettesBatch(videoIds: string[]): Promise<{ status: string; queued: number; requested: number }> {
  const r = await fetch(`${API_BASE}/maintenance/palettes/generate/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_ids: videoIds }),
  });
  return r.json();
}

export async function stopPalettes(): Promise<{ dropped_queued: number; interrupted_video_id: string | null }> {
  const r = await fetch(`${API_BASE}/maintenance/palettes/stop`, { method: "POST" });
  return r.json();
}

// ---- Locked / orphan files (soft-deleted DB rows whose file is still on disk) ----

export type OrphanItem = VideoItem & { deleted_at: string | null };

export async function fetchOrphans(): Promise<{ count: number; items: OrphanItem[] }> {
  const r = await fetch(`${API_BASE}/maintenance/orphans`);
  return r.json();
}

export async function retryOrphan(videoId: string): Promise<{ status: string; error?: string }> {
  const r = await fetch(`${API_BASE}/maintenance/orphans/${videoId}/retry`, { method: "POST" });
  return r.json();
}

export async function retryAllOrphans(): Promise<{ recycled: number; still_locked: number; purged_no_file: number }> {
  const r = await fetch(`${API_BASE}/maintenance/orphans/retry-all`, { method: "POST" });
  return r.json();
}

// ---- Review-mode auto-advance: next video matching the same filters ----

export async function fetchNextVideo(
  afterId: string,
  filters: Omit<VideoFilters, "offset" | "limit"> = {},
): Promise<{ next_id: string | null; next: VideoItem | null }> {
  const url = new URL(`${API_BASE}/videos/next`);
  url.searchParams.set("after", afterId);
  Object.entries(filters).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    if (Array.isArray(v)) {
      v.forEach((item) => { if (item !== "" && item !== null && item !== undefined) url.searchParams.append(k, String(item)); });
    } else {
      url.searchParams.set(k, String(v));
    }
  });
  const r = await fetch(url);
  return r.json();
}

// Stats
export type StatsOverview = {
  total_videos: number;
  total_size_bytes: number;
  total_duration_seconds: number;
  total_favorites: number;
  total_views: number;
  total_watch_time_seconds: number;
};

export type StatVideoEntry = { id: string; title: string; view_count: number; total_watch_time: number; duration: number | null };
export type StatHistoryEntry = { video_id: string; watched_at: string | null; watch_duration: number; title: string };
export type StatTagEntry = { name: string; total_views: number; video_count: number };
export type StatDayEntry = { date: string; views: number; watch_time: number };

export type PipelineStats = {
  total_active: number;
  confirmed: number;
  unconfirmed: number;
  ready_to_review: number;
  with_palette: number;
  missing_palette: number;
  palette_failed: number;
  convert: {
    pending: number;
    processing: number;
    completed: number;
    failed: number;
    skipped: number;
    none: number;
  };
  soft_deleted: number;
};

export type StatsResponse = {
  overview: StatsOverview;
  most_viewed: StatVideoEntry[];
  most_watched_time: StatVideoEntry[];
  recent_history: StatHistoryEntry[];
  popular_tags: StatTagEntry[];
  favorites: StatVideoEntry[];
  daily_activity: StatDayEntry[];
  pipeline: PipelineStats;
};

export async function fetchStats(): Promise<StatsResponse> {
  const r = await fetch(`${API_BASE}/stats`);
  if (!r.ok) throw new Error("Failed to load stats");
  return r.json();
}
