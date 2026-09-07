/** Shared nav links back to EduardoSaverin dashboards. */
export function SiteNav() {
  return (
    <nav className="shrink-0 flex gap-1 px-3 pt-2 border-b border-neutral-800 bg-black">
      <a
        href="../"
        className="text-[10px] uppercase tracking-widest text-neutral-500 hover:text-neutral-200 px-2 py-1.5"
      >
        Storage
      </a>
      <a
        href="../kalshi/"
        className="text-[10px] uppercase tracking-widest text-neutral-500 hover:text-neutral-200 px-2 py-1.5"
      >
        Weather markets
      </a>
      <span className="text-[10px] uppercase tracking-widest text-neutral-100 px-2 py-1.5 border-b-2 border-green-500">
        Trading bot
      </span>
    </nav>
  )
}
