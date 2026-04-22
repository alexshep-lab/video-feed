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
  const [tileSize, setTileSize] = useState(() => {
    const stored = localStorage.getItem("videofeed.tileSize");
    const n = stored ? Number(stored) : NaN;
    return Number.isFinite(n) && n >= 180 && n <= 420 ? n : 260;
  });
  useEffect(() => {
    localStorage.setItem("videofeed.tileSize", String(tileSize));
  }, [tileSize]);
  const [scanProgress, setScanProgress] = useState<ScanProgress | null>(null);
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [readyOnly, setReadyOnly] = useState(false);

  // Filters state
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("shuffle");
  // Stable seed for paginated shuffle. Without it, the server's
  // `ORDER BY RANDOM()` re-shuffles every page — infinite-scroll loses
  // ~30% of matches to cross-page duplicates. Regenerated each time the
  // user enters shuffle mode (including the initial mount).
  const [shuffleSeed, setShuffleSeed] = useState<number>(() =>
    Math.floor(Math.random() * 2_000_000_000) + 1,
  );
  useEffect(() => {
    if (sort === "shuffle") {
      setShuffleSeed(Math.floor(Math.random() * 2_000_000_000) + 1);
    }
  }, [sort]);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagMode, setTagMode] = useState<"any" | "all">("any");
  const [tagSearch, setTagSearch] = useState("");
  const [codec, setCodec] = useState("");
  const [library, setLibrary] = useState("");
  // "all" — без фильтра, "only" — только избранные, "hide" — скрыть избранные
  const [favoriteFilter, setFavoriteFilter] = useState<"all" | "only" | "hide">("all");

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
  }, [q, sort, shuffleSeed, selectedTags, tagMode, codec, library, favoriteFilter, mode, reviewFilter, readyOnly]);

  useEffect(() => {
    let cancelled = false;
    if (page === 0) {
      setLoading(true);
    } else {
      setLoadingMore(true);
    }
    const params: VideoFilters = { sort, limit: PAGE_SIZE, offset: page * PAGE_SIZE };
    if (sort === "shuffle") params.shuffle_seed = shuffleSeed;
    if (q) params.q = q;
    if (selectedTags.length > 0) {
      params.tags = selectedTags;
      if (tagMode === "all") params.tag_mode = "all";
    }
    if (codec) params.codec = codec;
    if (library) params.library = library;
    if (favoriteFilter === "only") params.favorite = true;
    if (favoriteFilter === "hide") params.favorite = false;
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
  }, [q, sort, shuffleSeed, selectedTags, tagMode, codec, library, favoriteFilter, mode, reviewFilter, readyOnly, page]);

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

  function toggleTag(tag: string) {
    setSelectedTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
  }

  const activeTagSet = new Set(selectedTags);

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

        {/* Favorites toggle — cycles all → only → hide → all */}
        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wider text-white/35">Favorites</label>
          <div className="flex gap-0 overflow-hidden rounded-lg border border-white/10 text-xs">
            <button
              onClick={() => setFavoriteFilter("all")}
              className={`flex-1 py-1.5 transition ${favoriteFilter === "all" ? "bg-accent/20 text-accent" : "text-white/55 hover:text-white hover:bg-white/5"}`}
              title="Show all videos"
            >
              All
            </button>
            <button
              onClick={() => setFavoriteFilter("only")}
              className={`flex-1 py-1.5 transition ${favoriteFilter === "only" ? "bg-accent/20 text-accent" : "text-white/55 hover:text-white hover:bg-white/5"}`}
              title="Only favorites"
            >
              ★ Only
            </button>
            <button
              onClick={() => setFavoriteFilter("hide")}
              className={`flex-1 py-1.5 transition ${favoriteFilter === "hide" ? "bg-accent/20 text-accent" : "text-white/55 hover:text-white hover:bg-white/5"}`}
              title="Hide favorites"
            >
              Hide ★
            </button>
          </div>
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

        {/* Tags — multi-select with substring search.
            Clicking a tag toggles it in `selectedTags`; the backend ANDs them
            (video must match every selected tag). */}
        {filters?.tags && filters.tags.length > 0 && (
          <div className="space-y-1">
            <div className="flex items-baseline justify-between">
              <label className="text-xs uppercase tracking-wider text-white/35">
                Tags {selectedTags.length > 0 && <span className="text-accent/80 normal-case">· {selectedTags.length} selected</span>}
              </label>
              {selectedTags.length > 0 && (
                <button onClick={() => setSelectedTags([])} className="text-[10px] text-white/40 hover:text-white/80">clear</button>
              )}
            </div>
            {selectedTags.length > 1 && (
              <div className="flex gap-0 text-[10px] rounded overflow-hidden border border-white/10">
                <button
                  onClick={() => setTagMode("any")}
                  className={`flex-1 py-0.5 transition ${tagMode === "any" ? "bg-accent/20 text-accent" : "text-white/50 hover:text-white/80"}`}
                  title="Match videos having any of the selected tags"
                >
                  any (OR)
                </button>
                <button
                  onClick={() => setTagMode("all")}
                  className={`flex-1 py-0.5 transition ${tagMode === "all" ? "bg-accent/20 text-accent" : "text-white/50 hover:text-white/80"}`}
                  title="Match only videos having every selected tag"
                >
                  all (AND)
                </button>
              </div>
            )}
            <input
              type="text"
              value={tagSearch}
              onChange={(e) => setTagSearch(e.target.value)}
              placeholder="Search tags…"
              className={`${selectCls} text-xs`}
            />
            {selectedTags.length > 0 && (
              <div className="flex flex-wrap gap-1 pt-1">
                {selectedTags.map((name) => (
                  <button
                    key={name}
                    onClick={() => setSelectedTags(selectedTags.filter((x) => x !== name))}
                    className="px-1.5 py-0.5 rounded bg-accent/20 text-accent text-[11px] hover:bg-accent/30"
                    title="Remove"
                  >
                    {name} ×
                  </button>
                ))}
              </div>
            )}
            <div className="max-h-48 overflow-y-auto space-y-0.5 mt-1">
              {filters.tags
                .filter((t) => !tagSearch || t.name.toLowerCase().includes(tagSearch.toLowerCase()))
                .map((t) => {
                  const active = selectedTags.includes(t.name);
                  return (
                    <button
                      key={t.id}
                      onClick={() =>
                        setSelectedTags(active ? selectedTags.filter((x) => x !== t.name) : [...selectedTags, t.name])
                      }
                      className={`w-full text-left px-2 py-0.5 rounded text-xs transition ${active ? "bg-accent/20 text-accent" : "text-white/50 hover:text-white hover:bg-white/5"}`}
                    >
                      {t.name} <span className="text-white/30">({t.video_count})</span>
                    </button>
                  );
                })}
            </div>
          </div>
        )}

        {/* Codec / Library filters were removed — tags cover the same ground.
            The underlying state (`codec`, `library`) and backend params are
            still wired up so URL-based filtering keeps working for anyone who
            bookmarked a filtered view. */}

        {/* Incoming folders — quick "add video here" links. The library
            filter is gone but the ⬆ upload shortcut is still useful. */}
        {libraries.some((l) => l.is_incoming) && (
          <div className="space-y-1">
            <label className="text-xs uppercase tracking-wider text-white/35">Incoming</label>
            <div className="max-h-32 space-y-0.5 overflow-y-auto">
              {libraries.filter((l) => l.is_incoming).map((l) => (
                <button
                  key={l.id}
                  onClick={() => handleIncomingClick(l.path)}
                  title={l.path}
                  className="flex w-full items-center gap-1 rounded px-2 py-0.5 text-left text-xs text-white/50 transition hover:bg-white/5 hover:text-white"
                >
                  <span className="shrink-0 text-accent/70">⬆</span>
                  <span className="truncate">{l.display_name ?? l.path}</span>
                </button>
              ))}
            </div>
          </div>
        )}

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
                selectedTags.forEach((name) => reviewParams.append("tags", name));
                if (selectedTags.length > 1 && tagMode === "all") reviewParams.set("tag_mode", "all");
                if (codec) reviewParams.set("codec", codec);
                if (library) reviewParams.set("library", library);
                if (sort) reviewParams.set("sort", sort);
                const linkQuery = reviewParams.toString();
                return (
                <div key={v.id} className="relative">
                  <VideoCard
                    video={v}
                    showPath
                    showExtraMeta
                    linkQuery={linkQuery}
                    activeTags={activeTagSet}
                    onTagClick={toggleTag}
                  />
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
          <VideoGrid
            videos={videos}
            minCardWidth={tileSize}
            activeTags={activeTagSet}
            onTagClick={toggleTag}
          />
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
