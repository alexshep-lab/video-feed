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
  category?: string;
  library?: string;
  codec?: string;
  duration_min?: number;
  duration_max?: number;
  sort?: string;
  offset?: number;
  limit?: number;
  confirmed?: boolean;
  favorite?: boolean;
};

export async function fetchVideos(filters: VideoFilters = {}): Promise<VideoItem[]> {
  const url = new URL(`${API_BASE}/videos`);
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== "" && v !== null) url.searchParams.set(k, String(v));
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

export async function deleteVideo(videoId: string, hard = false, recycle = false): Promise<void> {
  await fetch(`${API_BASE}/videos/${videoId}?hard=${hard}&recycle=${recycle}`, { method: "DELETE" });
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

export type StatsResponse = {
  overview: StatsOverview;
  most_viewed: StatVideoEntry[];
  most_watched_time: StatVideoEntry[];
  recent_history: StatHistoryEntry[];
  popular_tags: StatTagEntry[];
  favorites: StatVideoEntry[];
  daily_activity: StatDayEntry[];
};

export async function fetchStats(): Promise<StatsResponse> {
  const r = await fetch(`${API_BASE}/stats`);
  if (!r.ok) throw new Error("Failed to load stats");
  return r.json();
}
