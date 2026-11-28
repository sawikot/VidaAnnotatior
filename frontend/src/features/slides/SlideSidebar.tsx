import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MaterialIcon } from "../../components/MaterialIcon";
import { listSlides, thumbnailUrl } from "../../services/api";
import type { Slide } from "../../types/api";
import { visibleRange } from "../../utils/annotationPreview";

const ROW_HEIGHT = 64;
const LOAD_DELAY_MS = 150; // a row scrolled straight past never asks the server for its picture
const OPEN_KEY = "slideSidebarOpen";

/**
 * The project's slides down the side of the workspace, each with a small picture, to see what a slide is
 * and move to another. Only the rows on screen exist, and a row asks for its picture only once it has
 * stayed on screen a moment, so a project of thousands of slides scrolls as smoothly as one of ten.
 */
export function SlideSidebar({ projectId, currentId }: { projectId: number; currentId: number }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(() => localStorage.getItem(OPEN_KEY) !== "0");
  const [slides, setSlides] = useState<Slide[] | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let live = true;
    listSlides(projectId)
      .then((list) => live && setSlides(list))
      .catch(() => live && setSlides([]));
    return () => {
      live = false;
    };
  }, [projectId]);

  const toggle = () => {
    localStorage.setItem(OPEN_KEY, open ? "0" : "1");
    setOpen(!open);
  };

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return !slides ? [] : q ? slides.filter((s) => s.filename.toLowerCase().includes(q)) : slides;
  }, [slides, query]);

  if (!open) {
    return (
      <button
        onClick={toggle}
        title="Show the project's slides"
        className="w-8 shrink-0 bg-[#0f172a] border-r border-[#1e293b] flex flex-col items-center pt-space-sm gap-1 text-slate-400 hover:text-white"
      >
        <MaterialIcon name="chevron_right" />
        <span className="text-label-sm [writing-mode:vertical-rl] rotate-180">Slides</span>
      </button>
    );
  }

  return (
    <div className="w-60 shrink-0 bg-[#0f172a] border-r border-[#1e293b] flex flex-col overflow-hidden" data-testid="slide-sidebar">
      <div className="p-space-sm border-b border-[#1e293b] flex flex-col gap-space-sm">
        <div className="flex items-center justify-between">
          <span className="text-label-sm text-slate-400">Slides{slides ? ` (${shown.length.toLocaleString()})` : ""}</span>
          <button onClick={toggle} title="Hide the slide list" className="text-slate-400 hover:text-white">
            <MaterialIcon name="chevron_left" />
          </button>
        </div>
        {slides && slides.length > 8 && (
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find a slide"
            className="w-full rounded bg-[#0b1329] border border-[#1e293b] px-2 py-1 text-label-md text-slate-200 placeholder:text-slate-500"
          />
        )}
      </div>
      {!slides ? (
        <div className="p-space-md text-body-sm text-slate-500">Loading...</div>
      ) : (
        <Rows slides={shown} currentId={currentId} onOpen={(id) => navigate(`/projects/${projectId}/slides/${id}/workspace`)} />
      )}
    </div>
  );
}

function Rows({ slides, currentId, onOpen }: { slides: Slide[]; currentId: number; onOpen: (id: number) => void }) {
  const scroller = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(600);
  const { start, end } = visibleRange(scrollTop, height, ROW_HEIGHT, slides.length);
  const currentIndex = useMemo(() => slides.findIndex((s) => s.id === currentId), [slides, currentId]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setHeight(el.clientHeight));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // The slide being annotated is brought into view.
  useEffect(() => {
    const el = scroller.current;
    if (!el || currentIndex < 0) return;
    const top = currentIndex * ROW_HEIGHT;
    if (top < el.scrollTop || top + ROW_HEIGHT > el.scrollTop + el.clientHeight) el.scrollTop = Math.max(0, top - el.clientHeight / 2 + ROW_HEIGHT / 2);
  }, [currentIndex]);

  if (slides.length === 0) return <div className="p-space-md text-body-sm text-slate-500">No slides found.</div>;
  return (
    <div ref={scroller} className="flex-1 overflow-y-auto" onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}>
      <div className="relative" style={{ height: slides.length * ROW_HEIGHT }}>
        {slides.slice(start, end).map((s, i) => (
          <SlideRow key={s.id} slide={s} top={(start + i) * ROW_HEIGHT} current={s.id === currentId} onOpen={onOpen} />
        ))}
      </div>
    </div>
  );
}

const SlideRow = memo(function SlideRow({ slide, top, current, onOpen }: { slide: Slide; top: number; current: boolean; onOpen: (id: number) => void }) {
  const failed = slide.status === "error";
  return (
    <button
      onClick={() => !current && !failed && onOpen(slide.id)}
      disabled={failed}
      title={failed ? `${slide.filename}: failed to import` : slide.filename}
      className={`absolute left-0 right-0 flex items-center gap-space-sm px-space-sm text-left border-l-2 ${
        current ? "bg-[#1e293b] border-cyan-400" : "border-transparent hover:bg-[#162036]"
      } ${failed ? "opacity-50 cursor-not-allowed" : ""}`}
      style={{ top, height: ROW_HEIGHT }}
    >
      <SlidePicture slide={slide} className="w-12 h-12" />
      <span className="flex-1 min-w-0">
        <span className="block truncate text-label-md text-slate-200">{slide.filename}</span>
        <span className="block text-label-sm text-slate-500 truncate">
          {slide.annotation_count != null ? `${slide.annotation_count.toLocaleString()} annotations` : slide.status.replace(/_/g, " ")}
        </span>
      </span>
    </button>
  );
});

/** A small picture of a whole slide, asked for only once it has stayed on screen a moment. A slide that
 * failed to import, or whose picture cannot be made, shows an icon instead. */
export function SlidePicture({ slide, className = "" }: { slide: Slide; className?: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [broken, setBroken] = useState(false);
  const failed = slide.status === "error";
  const url = thumbnailUrl(slide.id, 128, slide.image_version);
  useEffect(() => {
    if (failed) return;
    const timer = setTimeout(() => setSrc(url), LOAD_DELAY_MS);
    return () => clearTimeout(timer);
  }, [url, failed]);
  return (
    <span className={`relative shrink-0 rounded overflow-hidden bg-[#0b1329] flex items-center justify-center ${className}`}>
      {failed || broken ? (
        <MaterialIcon name={failed ? "error" : "image_not_supported"} className="!text-[18px] text-slate-600" />
      ) : (
        src && <img src={src} alt="" decoding="async" onError={() => setBroken(true)} className="absolute inset-0 w-full h-full object-cover" />
      )}
    </span>
  );
}
