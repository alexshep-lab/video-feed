import { useEffect, useRef, useState } from "react";
import {
  fetchVideos, fetchFilters, fetchLibraries, addLibrary, scanLibrary, fetchScanProgress,
  updateVideo,
  VideoItem, VideoFilters, FilterOptions, LibraryFolder, ScanProgress,
} from "../api/client";
import VideoGrid from "../components/VideoGrid";
import VideoCard from "../components/VideoCard";

const SORT_OPTIONS = [
  { value: "shuffle", label: "Shuffle" },
  { value: "newest", label: "Newest" },
  { value: "oldest", label: "Oldest" },
  { value: "title", label: "A–Z" },
  { value: "duration", label: "Duration" },
  { value: "size", label: "Size" },
  { value: "most_viewed", label: "Most Viewed" },
  { value: "last_watched", label: "Last Watched" },
];

const PAGE_SIZE = 50;

type Mode = "library" | "unconfirmed";
type ReviewFilter = "all" | "unconfirmed" | "confirmed";

export default function HomePage() {
  const [videos, setVideos] = useState<VideoItem[]>([]);
  const [filters, setFilters] = useState<FilterOptions | null>(null);
  const [libraries, setLibraries] = useState<LibraryFolder[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("library");
  const [scanResult, setScanResult] = useState<string | null>(null);
  const [newFolder, setNewFolder] = useState("");
  const [addingFolder, setAddingFolder] = useState(false);
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [tileSize, setTileSize] = useState(260);
  const [scanProgress, setScanProgress] = useState<ScanProgress | null>(null);
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [readyOnly, setReadyOnly] = useState(false);

  // Filters state
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("shuffle");
  const [tag, setTag] = useState("");
  const [codec, setCodec] = useState("");
  const [library, setLibrary] = useState("");

  const folderInputRef = useRef<HTMLInputElement>(null);
  const addFolderSectionRef = useRef<HTMLDivElement>(null);
  const loadMoreRef = useRef<HTMLDivElement>(null);
  const scanPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    fetchFilters().then(setFilters).catch(() => null);
    fetchLibraries().then(setLibraries).catch(() => null);
  }, []);

  useEffect(() => {
    return () => {
      if (scanPollRef.current) {
        clearInterval(scanPollRef.current);
        scanPollRef.current = null;
      }
    };
  }, []);

  // Reset page when filters change
  useEffect(() => {
    setPage(0);
    setVideos([]);
    setHasMore(true);
  }, [q, sort, tag, codec, library, mode, reviewFilter, readyOnly]);

  useEffect(() => {
    let cancelled = false;
    if (page === 0) {
      setLoading(true);
    } else {
      setLoadingMore(true);
    }
    const params: VideoFilters = { sort, limit: PAGE_SIZE, offset: page * PAGE_SIZE };
    if (q) params.q = q;
    if (tag) params.tag = tag;
    if (codec) params.codec = codec;
    if (library) params.library = library;
    if (mode === "unconfirmed") {
      if (reviewFilter === "unconfirmed") params.confirmed = false;
      if (reviewFilter === "confirmed") params.confirmed = true;
      if (readyOnly) params.ready = true;
    }

    fetchVideos(params)
      .then((items) => {
        if (cancelled) return;
        setVideos((prev) => {
          if (page === 0) return items;
          const seen = new Set(prev.map((video) => video.id));
          const uniqueItems = items.filter((video) => !seen.has(video.id));
          return [...prev, ...uniqueItems];
        });
        setHasMore(items.length === PAGE_SIZE);
        setError(null);
      })
      .catch((err: Error) => { if (!cancelled) setError(err.message); })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
          setLoadingMore(false);
        }
      });

    return () => { cancelled = true; };
  }, [q, sort, tag, codec, library, mode, reviewFilter, readyOnly, page]);

  useEffect(() => {
    if (loading || loadingMore || !hasMore) return;

    const target = loadMoreRef.current;
    if (!target) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setPage((current) => current + 1);
        }
      },
      { rootMargin: "600px 0px" },
    );

    observer.observe(target);
    return () => observer.disconnect();
  }, [mode, loading, loadingMore, hasMore]);

  async function handleScan() {
    setScanResult("Scanning...");
    setScanProgress({ running: true, total_files: 0, processed: 0, created: 0, phase: "counting" });
    if (scanPollRef.current) clearInterval(scanPollRef.current);
    scanPollRef.current = setInterval(() => {
      fetchScanProgress()
        .then((progress) => {
          setScanProgress(progress);
          if (!progress.running && scanPollRef.current) {
            clearInterval(scanPollRef.current);
            scanPollRef.current = null;
          }
        })
        .catch(() => null);
    }, 700);
    try {
      const r = await scanLibrary();
      setScanResult(`Done: ${r.scanned_files} files, ${r.created} new, ${r.updated} updated`);
      setScanProgress((prev) => prev ? { ...prev, running: false, processed: r.scanned_files, total_files: r.scanned_files, phase: "done" } : null);
      fetchFilters().then(setFilters);
      fetchLibraries().then(setLibraries);
    } catch {
      setScanResult("Scan failed");
    } finally {
      if (scanPollRef.current) {
        clearInterval(scanPollRef.current);
        scanPollRef.current = null;
      }
    }
  }

  async function handleAddFolder() {
    if (!newFolder.trim()) return;
    setAddingFolder(true);
    try {
      const libs = await addLibrary(newFolder.trim());
      setLibraries(libs);
      setNewFolder("");
      await handleScan();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setAddingFolder(false);
    }
  }

  function handleIncomingClick(path: string) {
    setNewFolder(path);
    addFolderSectionRef.current?.scrollIntoView({ behavior: "smooth" });
    setTimeout(() => folderInputRef.current?.focus(), 300);
  }

  async function confirmVideo(videoId: string) {
    const current = videos.find((v) => v.id === videoId);
    if (!current) return;
    const nextConfirmed = !current.confirmed;
    await updateVideo(videoId, { confirmed: nextConfirmed });
    setVideos((prev) => prev.map((v) => v.id === videoId ? { ...v, confirmed: nextConfirmed } : v));
  }

  const inputCls = "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-white placeholder:text-white/30 focus:outline-none focus:border-accent/50";
  const selectCls = "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-white focus:outline-none focus:border-accent/50";
  const btnCls = "w-full rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-sm text-white/80 hover:bg-white/10 transition text-left";
  const tabCls = (active: boolean) =>
    `px-3 py-1.5 rounded-lg text-sm transition cursor-pointer ${active ? "bg-accent/20 text-accent border border-accent/30" : "text-white/60 hover:text-white hover:bg-white/5"}`;

  return (
    <div className="flex gap-6">
      {/* Sidebar */}
      <aside className="w-64 shrink-0 space-y-5">
        {/* Mode tabs */}
        <div className="space-y-1">
          <button className={tabCls(mode === "library")} onClick={() => setMode("library")}>📁 Library</button>
          <button className={tabCls(mode === "unconfirmed")} onClick={() => setMode("unconfirmed")}>⚠ Unconfirmed</button>
        </div>

        <hr className="border-white/10" />

        {/* Search */}
        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wider text-white/35">Search</label>
          <input type="text" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Title or filename..." className={inputCls} />
        </div>

        {/* Sort */}
        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wider text-white/35">Sort</label>
          <select value={sort} onChange={(e) => setSort(e.target.value)} className={selectCls}>
            {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wider text-white/35">Tile Size</label>
          <input
            type="range"
            min={180}
            max={420}
            step={10}
            value={tileSize}
            onChange={(e) => setTileSize(Number(e.target.value))}
            className="w-full accent-amber-500"
          />
          <div className="text-xs text-white/40">{tileSize}px</div>
        </div>

        {/* Tags */}
        {filters?.tags && filters.tags.length > 0 && (
          <div className="space-y-1">
            <label className="text-xs uppercase tracking-wider text-white/35">Tag</label>
            <select value={tag} onChange={(e) => setTag(e.target.value)} className={selectCls}>
              <option value="">All tags</option>
              {filters.tags.map((t) => <option key={t.id} value={t.name}>{t.name} ({t.video_count})</option>)}
            </select>
            {/* Clickable tag list */}
            <div className="max-h-48 overflow-y-auto space-y-0.5 mt-1">
              {filters.tags.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTag(tag === t.name ? "" : t.name)}
                  className={`w-full text-left px-2 py-0.5 rounded text-xs transition ${tag === t.name ? "bg-accent/20 text-accent" : "text-white/50 hover:text-white hover:bg-white/5"}`}
                >
                  {t.name} <span className="text-white/30">({t.video_count})</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Codec */}
        {filters?.codecs && filters.codecs.length > 0 && (
          <div className="space-y-1">
            <label className="text-xs uppercase tracking-wider text-white/35">Codec</label>
            <select value={codec} onChange={(e) => setCodec(e.target.value)} className={selectCls}>
              <option value="">All codecs</option>
              {filters.codecs.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <div className="max-h-32 overflow-y-auto space-y-0.5 mt-1">
              {filters.codecs.map((c) => (
                <button
                  key={c}
                  onClick={() => setCodec(codec === c ? "" : c)}
                  className={`w-full text-left px-2 py-0.5 rounded text-xs transition ${codec === c ? "bg-accent/20 text-accent" : "text-white/50 hover:text-white hover:bg-white/5"}`}
                >
                  {c}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Libraries */}
        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wider text-white/35">Library</label>
          <select value={library} onChange={(e) => setLibrary(e.target.value)} className={selectCls}>
            <option value="">All libraries</option>
            {libraries.map((l) => (
              <option key={l.id} value={l.path}>{l.display_name ?? l.path} ({l.video_count})</option>
            ))}
          </select>
          <div className="max-h-32 overflow-y-auto space-y-0.5 mt-1">
            {libraries.map((l) => (
              <div key={l.id} className="flex items-center gap-1">
                <button
                  onClick={() => setLibrary(library === l.path ? "" : l.path)}
                  className={`flex-1 text-left px-2 py-0.5 rounded text-xs transition truncate ${library === l.path ? "bg-accent/20 text-accent" : "text-white/50 hover:text-white hover:bg-white/5"}`}
                  title={l.path}
                >
                  {l.display_name ?? l.path} <span className="text-white/30">({l.video_count})</span>
                </button>
                {l.is_incoming && (
                  <button
                    onClick={() => handleIncomingClick(l.path)}
                    title="Add video to this folder"
                    className="shrink-0 px-1 py-0.5 rounded text-xs text-accent/70 hover:text-accent hover:bg-accent/10 transition"
                  >
                    ⬆
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>

        <hr className="border-white/10" />

        {/* Add folder */}
        <div className="space-y-1" ref={addFolderSectionRef}>
          <label className="text-xs uppercase tracking-wider text-white/35">Add Folder</label>
          <input
            ref={folderInputRef}
            type="text"
            value={newFolder}
            onChange={(e) => setNewFolder(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleAddFolder(); }}
            placeholder="C:\path\to\folder"
            className={inputCls}
          />
          <button onClick={handleAddFolder} disabled={addingFolder} className={btnCls}>
            {addingFolder ? "Adding..." : "+ Add & Scan"}
          </button>
        </div>

        {/* Scan */}
        <button onClick={handleScan} className={btnCls}>⟳ Scan Library</button>
        {scanResult && <p className="text-xs text-white/40">{scanResult}</p>}
        {scanProgress?.running && (
          <div className="space-y-2">
            <div className="text-xs text-white/45">
              {scanProgress.phase === "counting"
                ? "Counting files..."
                : `Scanning ${scanProgress.processed}/${scanProgress.total_files || "?"} files`}
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full bg-accent transition-all"
                style={{
                  width: `${scanProgress.total_files > 0 ? (scanProgress.processed / scanProgress.total_files) * 100 : 8}%`,
                }}
              />
            </div>
          </div>
        )}
      </aside>

      {/* Main content */}
      <main className="flex-1 min-w-0 space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="text-3xl font-semibold text-white">
            {mode === "library" ? "Library" : "Unconfirmed"}
          </h1>
          <span className="text-sm text-white/40">
            {videos.length} video{videos.length !== 1 ? "s" : ""}
          </span>
        </div>

        {loading && <p className="text-white/60">Loading...</p>}
        {error && <p className="text-red-300">{error}</p>}

        {!loading && mode === "unconfirmed" && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex flex-wrap gap-2">
                <button className={tabCls(reviewFilter === "all")} onClick={() => setReviewFilter("all")}>All</button>
                <button className={tabCls(reviewFilter === "unconfirmed")} onClick={() => setReviewFilter("unconfirmed")}>Unconfirmed</button>
                <button className={tabCls(reviewFilter === "confirmed")} onClick={() => setReviewFilter("confirmed")}>Confirmed</button>
              </div>
              <label className="flex items-center gap-2 text-sm text-white/70 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={readyOnly}
                  onChange={(e) => setReadyOnly(e.target.checked)}
                  className="h-4 w-4 accent-amber-500 cursor-pointer"
                />
                Ready to review (converted + palette generated)
              </label>
            </div>
            <p className="text-sm text-white/40">Search and filters work here too. You can review both confirmed and unconfirmed videos from one place.</p>
            <div
              className="grid gap-4"
              style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${tileSize}px, 1fr))` }}
            >
              {videos.map((v) => {
                // Propagate the active review filter to the watch page so it can
                // auto-advance to the next matching video after Confirm / Hard Delete.
                const reviewParams = new URLSearchParams({ review: "1" });
                if (reviewFilter === "unconfirmed") reviewParams.set("confirmed", "false");
                if (reviewFilter === "confirmed") reviewParams.set("confirmed", "true");
                if (readyOnly) reviewParams.set("ready", "true");
                if (q) reviewParams.set("q", q);
                if (tag) reviewParams.set("tag", tag);
                if (codec) reviewParams.set("codec", codec);
                if (library) reviewParams.set("library", library);
                if (sort && sort !== "shuffle") reviewParams.set("sort", sort);
                const linkQuery = reviewParams.toString();
                return (
                <div key={v.id} className="relative">
                  <VideoCard video={v} showPath showExtraMeta linkQuery={linkQuery} />
                  <button
                    onClick={() => confirmVideo(v.id)}
                    className={`mt-1 w-full rounded-lg px-3 py-1 text-xs transition ${
                      v.confirmed
                        ? "border border-white/15 bg-white/5 text-white/70 hover:bg-white/10"
                        : "border border-green-500/40 bg-green-500/10 text-green-300 hover:bg-green-500/20"
                    }`}
                  >
                    ✓ Confirm
                  </button>
                </div>
                );
              })}
            </div>
          </div>
        )}

        {!loading && mode === "library" && !error && (
          <VideoGrid videos={videos} minCardWidth={tileSize} />
        )}

        {!loading && !error && (
          <div ref={loadMoreRef} className="flex items-center justify-center py-6">
            {loadingMore ? (
              <span className="text-sm text-white/40">Loading more...</span>
            ) : hasMore ? (
              <span className="text-sm text-white/25">Scroll down to load more</span>
            ) : videos.length > 0 ? (
              <span className="text-sm text-white/25">No more videos</span>
            ) : null}
          </div>
        )}
      </main>
    </div>
  );
}
