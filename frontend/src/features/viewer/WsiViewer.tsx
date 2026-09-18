import OpenSeadragon from "openseadragon";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
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
  onViewportChange?: (bbox: ViewportBbox, zoom: number) => void;
  onViewerReady?: (viewer: OpenSeadragon.Viewer) => void;
  /** Rendered inside a single full-image SVG overlay whose viewBox is
   * "0 0 slideWidthL0 slideHeightL0" -- children can plot directly in
   * Level-0 pixel coordinates with zero manual pan/zoom math. */
  children?: React.ReactNode;
}

export function WsiViewer({ slideId, className = "", showNavigator = true, onViewportChange, onViewerReady, children }: Props) {
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
      zoomInButton: undefined,
      zoomOutButton: undefined,
      showZoomControl: true,
      showHomeControl: true,
      showFullPageControl: true,
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
        );
      });
    }
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
    </div>
  );
}

export function useOpenSeadragonZoomControls(viewer: OpenSeadragon.Viewer | null) {
  return {
    zoomIn: () => viewer?.viewport.zoomBy(1.4).applyConstraints(),
    zoomOut: () => viewer?.viewport.zoomBy(1 / 1.4).applyConstraints(),
    fit: () => viewer?.viewport.goHome(),
  };
}
