import OpenSeadragon from "openseadragon";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MaterialIcon } from "../../components/MaterialIcon";
import { dziUrl } from "../../services/api";

export interface ViewportBbox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

interface Props {
  slideId: number;
  className?: string;
  showNavigator?: boolean;
  /** Custom zoom/fit/fullscreen control cluster (styled to match the app,
   * unlike OpenSeadragon's default sprite-image buttons). Off by default for
   * small embedded/non-interactive uses (e.g. the workspace's minimap). */
  showControls?: boolean;
  /** `scale` is screen pixels per Level-0 pixel at the current zoom. */
  onViewportChange?: (bbox: ViewportBbox, zoom: number, scale: number) => void;
  /** Asked before every drag: return true to keep the viewer still (an annotation is being drawn or dragged). */
  blockPan?: () => boolean;
  /** OpenSeadragon's own keyboard shortcuts (arrows, W/A/S/D ...); switch off where the keys mean something else. */
  keyboardNav?: boolean;
  onViewerReady?: (viewer: OpenSeadragon.Viewer) => void;
  /** Rendered inside a single full-image SVG overlay whose viewBox is
   * "0 0 slideWidthL0 slideHeightL0" -- children can plot directly in
   * Level-0 pixel coordinates with zero manual pan/zoom math. */
  children?: React.ReactNode;
}

export function WsiViewer({
  slideId,
  className = "",
  showNavigator = true,
  showControls = true,
  onViewportChange,
  blockPan,
  keyboardNav = true,
  onViewerReady,
  children,
}: Props) {
  const blockPanRef = useRef(blockPan);
  blockPanRef.current = blockPan; // read at event time, so the viewer need not be rebuilt when it changes
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<OpenSeadragon.Viewer | null>(null);
  const overlayElRef = useRef<HTMLDivElement>(document.createElement("div"));
  const [contentSize, setContentSize] = useState<{ w: number; h: number } | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    setContentSize(null);

    const viewer = OpenSeadragon({
      element: containerRef.current,
      tileSources: dziUrl(slideId),
      drawer: "canvas",
      showNavigator,
      navigatorPosition: "BOTTOM_LEFT",
      showRotationControl: false,
      gestureSettingsMouse: { clickToZoom: false },
      keyboardNavEnabled: keyboardNav,
      // OpenSeadragon's own zoom/home/fullpage buttons render from a sprite-sheet
      // image path we don't serve; we draw our own matching-styled controls
      // instead (see the `showControls` overlay below).
      showNavigationControl: false,
      crossOriginPolicy: "Anonymous",
      minZoomImageRatio: 0.5,
      maxZoomPixelRatio: 4,
    });
    viewerRef.current = viewer;

    overlayElRef.current.style.width = "100%";
    overlayElRef.current.style.height = "100%";
    overlayElRef.current.style.pointerEvents = "none";

    viewer.addHandler("open", () => {
      const item = viewer.world.getItemAt(0);
      if (!item) return;
      const size = item.getContentSize();
      const rect = viewer.viewport.imageToViewportRectangle(
        new OpenSeadragon.Rect(0, 0, size.x, size.y),
      );
      viewer.addOverlay({ element: overlayElRef.current, location: rect });
      setContentSize({ w: size.x, h: size.y });
      onViewerReady?.(viewer);
      reportViewport();
    });

    let raf: number | null = null;
    function reportViewport() {
      if (!onViewportChange) return;
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const item = viewer.world.getItemAt(0);
        if (!item) return;
        const bounds = viewer.viewport.getBounds();
        const imgRect = viewer.viewport.viewportToImageRectangle(bounds);
        onViewportChange(
          { x0: imgRect.x, y0: imgRect.y, x1: imgRect.x + imgRect.width, y1: imgRect.y + imgRect.height },
          viewer.viewport.getZoom(),
          imgRect.width > 0 ? viewer.viewport.getContainerSize().x / imgRect.width : 1,
        );
      });
    }
    // Let a layer on top claim a drag (drawing a shape, moving a handle) so the slide does not pan under it.
    const holdStill = (event: OpenSeadragon.ViewerEvent) => {
      if (blockPanRef.current?.()) (event as unknown as { preventDefaultAction: boolean }).preventDefaultAction = true;
    };
    viewer.addHandler("canvas-drag", holdStill);
    viewer.addHandler("canvas-drag-end", holdStill);
    viewer.addHandler("animation", reportViewport);
    viewer.addHandler("resize", reportViewport);

    return () => {
      viewer.destroy();
      viewerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slideId]);

  return (
    <div ref={containerRef} className={`relative bg-[#0f172a] ${className}`}>
      {contentSize &&
        createPortal(
          <svg
            viewBox={`0 0 ${contentSize.w} ${contentSize.h}`}
            width="100%"
            height="100%"
            preserveAspectRatio="none"
            style={{ display: "block", overflow: "visible" }}
          >
            {children}
          </svg>,
          overlayElRef.current,
        )}
      {showControls && contentSize && (
        <div className="absolute bottom-3 right-3 z-30 flex items-center gap-0.5 bg-[#0f172a]/90 backdrop-blur-md rounded-lg shadow-lg p-1">
          <ViewerControlButton icon="zoom_in" title="Zoom in" onClick={() => viewerRef.current?.viewport.zoomBy(1.4).applyConstraints()} />
          <ViewerControlButton icon="zoom_out" title="Zoom out" onClick={() => viewerRef.current?.viewport.zoomBy(1 / 1.4).applyConstraints()} />
          <ViewerControlButton icon="crop_free" title="Fit to screen" onClick={() => viewerRef.current?.viewport.goHome()} />
          <ViewerControlButton
            icon="fullscreen"
            title="Toggle full page"
            onClick={() => viewerRef.current?.setFullScreen(!viewerRef.current.isFullPage())}
          />
        </div>
      )}
    </div>
  );
}

function ViewerControlButton({ icon, title, onClick }: { icon: string; title: string; onClick: () => void }) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="w-8 h-8 rounded flex items-center justify-center text-slate-300 hover:bg-white/10 hover:text-white transition-colors"
    >
      <MaterialIcon name={icon} className="!text-[18px]" />
    </button>
  );
}
