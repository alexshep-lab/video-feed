import { PropsWithChildren, useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";

type VersionInfo = { version: string; release_date: string; name: string };

export default function Layout({ children }: PropsWithChildren) {
  const navCls = ({ isActive }: { isActive: boolean }) =>
    `text-sm transition ${isActive ? "text-accentSoft" : "text-white/50 hover:text-white"}`;

  const [version, setVersion] = useState<VersionInfo | null>(null);

  useEffect(() => {
    fetch("/api/version")
      .then((r) => (r.ok ? r.json() : null))
      .then((v) => v && setVersion(v))
      .catch(() => {});
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-[radial-gradient(circle_at_top,_rgba(255,122,24,0.25),_transparent_28%),linear-gradient(180deg,_#11182a_0%,_#090d18_100%)] text-white">
      <header className="border-b border-white/10 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">
          <Link to="/" className="text-2xl font-semibold tracking-[0.18em] text-accentSoft">
            VIDEOFEED
          </Link>
          <nav className="flex items-center gap-6">
            <NavLink to="/" end className={navCls}>Library</NavLink>
            <NavLink to="/stats" className={navCls}>Stats</NavLink>
            <NavLink to="/maintenance" className={navCls}>Maintenance</NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-8">{children}</main>
      <footer className="border-t border-white/5 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 text-xs text-white/40">
          <span>
            VideoFeed
            {version ? ` v${version.version}` : ""}
            {version ? ` · ${version.release_date}` : ""}
          </span>
          <a
            href="https://github.com/alexshep-lab/video-feed"
            target="_blank"
            rel="noreferrer"
            className="hover:text-white/70"
          >
            github.com/alexshep-lab/video-feed
          </a>
        </div>
      </footer>
    </div>
  );
}
