import { PropsWithChildren } from "react";
import { Link, NavLink } from "react-router-dom";

export default function Layout({ children }: PropsWithChildren) {
  const navCls = ({ isActive }: { isActive: boolean }) =>
    `text-sm transition ${isActive ? "text-accentSoft" : "text-white/50 hover:text-white"}`;

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(255,122,24,0.25),_transparent_28%),linear-gradient(180deg,_#11182a_0%,_#090d18_100%)] text-white">
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
      <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
    </div>
  );
}
