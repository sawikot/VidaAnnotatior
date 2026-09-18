---
name: Clinical Precision WSI
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#3f4850'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#707881'
  outline-variant: '#bfc7d2'
  surface-tint: '#006398'
  primary: '#006194'
  on-primary: '#ffffff'
  primary-container: '#007bb9'
  on-primary-container: '#fdfcff'
  inverse-primary: '#93ccff'
  secondary: '#565e74'
  on-secondary: '#ffffff'
  secondary-container: '#dae2fd'
  on-secondary-container: '#5c647a'
  tertiary: '#006577'
  on-tertiary: '#ffffff'
  tertiary-container: '#008096'
  on-tertiary-container: '#f9fdff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#cce5ff'
  primary-fixed-dim: '#93ccff'
  on-primary-fixed: '#001d31'
  on-primary-fixed-variant: '#004b73'
  secondary-fixed: '#dae2fd'
  secondary-fixed-dim: '#bec6e0'
  on-secondary-fixed: '#131b2e'
  on-secondary-fixed-variant: '#3f465c'
  tertiary-fixed: '#acedff'
  tertiary-fixed-dim: '#4cd7f6'
  on-tertiary-fixed: '#001f26'
  on-tertiary-fixed-variant: '#004e5c'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  headline-lg:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '600'
    lineHeight: 18px
    letterSpacing: -0.005em
  body-lg:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  body-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '400'
    lineHeight: 14px
  label-lg:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.02em
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '400'
    lineHeight: 12px
    letterSpacing: 0.03em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 0.75rem
  margin: 1rem
  space-xs: 0.25rem
  space-sm: 0.375rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system delivers a clinical, high-performance interface engineered for computational pathology, slide-level analytics, and multi-resolution virtual patch annotation. The emotional core balances surgical precision, cognitive calm, and scientific authority. It strips away ornamental distraction to emphasize gigapixel histology tissue patterns, cellular morphology, and dense quantitative metadata.

The aesthetic fuses modern high-density data software with refined bio-computational instrumentation. It employs an asymmetric chrome architecture: deep-slate dark chrome frames the application context and master navigation to reduce eye fatigue during long microscopy sessions, while pristine, high-luminance clinical surfaces isolate patch inspectors, slide metadata, and analytical readouts. Hairline technical borders, monospaced coordinate registers, and calibrated diagnostic class color tokens ground the system in laboratory-grade rigor.

## Colors

The system employs a dual-context surface strategy calibrated for microscopic whole-slide imaging:
- **Application Shell & Viewport Chrome (Deep Space):** Canvas boundaries, viewport rulers, primary activity bars, and viewport overlays utilize deep slate/charcoal tones (`#0f172a`, `#1e293b`). This concentrates visual acuity entirely on stained H&E and IHC image channels.
- **Analytical & Data Panels (Clinical Light):** Inspector sidebars, patch grids, coordinate manifests, and classification cards reside on crisp clinical surfaces (`#ffffff`, `#f8fafc`, `#f1f5f9`), framed by surgical hairline dividers (`#e2e8f0`, `#cbd5e1`).
- **Primary & Interactive Accents:** Electric clinical sky-blue (`#0284c7`, `#0ea5e9`) and cyan (`#06b6d4`) denote active tools, selection marquees, zoom scales, and confirmed state transitions.

### Diagnostic Tissue Class System
Standardized categorical hues guarantee instantaneous classification mapping across zoom pyramids, segmentation overlays, and classification chips:
- **Tumor / Malignant:** Crimson Red (`#dc2626` base, `#ef4444` hover, `#fef2f2` tint)
- **Stroma / Reactive:** Emerald Green (`#059669` base, `#10b981` hover, `#ecfdf5` tint)
- **Necrosis:** Amber Gold (`#d97706` base, `#f59e0b` hover, `#fffbeb` tint)
- **Normal / Benign:** Royal Cobalt (`#2563eb` base, `#3b82f6` hover, `#eff6ff` tint)
- **Artifact / Background:** Neutral Slate (`#64748b` base, `#94a3b8` hover, `#f8fafc` tint)
- **Skipped / Indeterminate:** Muted Ochre (`#ca8a04` base, `#eab308` hover, `#fefce8` tint)
- **Review / Flagged:** Deep Violet (`#7c3aed` base, `#8b5cf6` hover, `#f5f3ff` tint)

## Typography

Typography prioritizes information density, numerical legibility, and zero ambiguity between spatial markers:
- **Inter:** Drives primary interface controls, panel headlines, patient/case IDs, status indicators, and micro-copy. Tight tracking on headers maintains compact layouts, while open metrics at small sizes (`11px`–`12px`) prevent fatigue during extended slide reviews.
- **JetBrains Mono:** Mandated across all geospatial and computational values, including Level-0 coordinate indices `(X, Y, W, H)`, micron-per-pixel (`MPP`) scales, patch grid matrices, magnification factors (`20x`, `40x`), inference confidence probabilities (`0.9984`), and raw geoJSON exports. Tabular numbers (`tnum`) ensure unwavering spatial alignment across live data feeds and dynamic tables.

## Layout & Spacing

The architecture employs an ergonomic, high-density scientific workspace anchored by an expandable tri-pane viewport:
- **Global Tool Ribbon (Left Chrome):** Fixed `48px` or `56px` slim utility rail housing primary modes (Pan/Zoom, Patch Grid Generator, Freehand Polygons, Measurement Calipers, Model Inferencing, QA Validation).
- **Primary Slide Stage (Fluid Viewport):** Expands to fill all remaining screen dimensions. Houses canvas HUD elements (magnification navigator, viewport scalebar, level-0 floating crosshair coordinates, and mini-map minimap thumbnail overlay).
- **Analytical & Patch Inspector (Right Panel):** Multi-tabbed docking tray (`320px` default, resizable between `280px` and `440px`) accommodating thumbnail patch cascades, diagnostic label assignment selectors, coordinate data sheets, and batch annotation progress bars.
- **Micro-Metric Rhythm:** UI components operate on a tight `4px` grid increments (`space-xs` = `4px`, `space-sm` = `6px`, `space-md` = `12px`). Density is balanced so data-entry controls consume minimal screen real estate, yielding maximum physical pixels to tissue observation.

## Elevation & Depth

Visual hierarchy is maintained through precision borders and tonal elevation tiers rather than dramatic ambient drop shadows, preventing blurred visual interference against slide pixels:
- **Tier 0 (Viewport Floor):** `#0f172a` canvas backdrop upon which multi-resolution slide tiles are composed.
- **Tier 1 (Surface Base):** `#ffffff` panels with a crisp `1px solid #e2e8f0` stroke. Zero drop-shadow.
- **Tier 2 (Floating Viewport Overlays & Toolbars):** `#ffffff` or `#1e293b` with a hairline `1px solid #cbd5e1` (or `#334155` in dark mode) paired with an ultra-fine, highly-attenuated shadow: `0 1px 3px 0 rgba(15, 23, 42, 0.08), 0 1px 2px -1px rgba(15, 23, 42, 0.05)`.
- **Tier 3 (Modals, Slide Overview Minimaps & Context Menus):** Clean `#ffffff` elevation bordered by `#94a3b8` and supported by `0 10px 15px -3px rgba(15, 23, 42, 0.12), 0 4px 6px -4px rgba(15, 23, 42, 0.05)`.
- **Spatial Focus Indicators:** Active tools and selected patches use a dual-ring highlight (`2px` solid `#0284c7` offset by `1px` white border) for instant distinction over densely colored H&E tissue backgrounds.

## Shapes

The design system enforces a soft, clinical geometric profile (`roundedness: 1`):
- **Base Components (Buttons, Inputs, Table Cells):** Configured with `0.25rem` (`4px`) radii, providing a precise, instrument-like silhouette that signals technical stability.
- **Containers & Docked Panels:** Panels, cards, and inspector windows utilize `0.375rem` to `0.5rem` (`6px`–`8px`) outer corner radii with flush interior dividers.
- **Badges, Status Dots, & Level-0 Metadata Pills:** Use compact full-pill contours (`9999px`) to visually differentiate floating scientific status tags from actionable square tool toggles.

## Components

### Viewport Action & Mode Buttons
- **Tool Toolbar Buttons:** Compact `32x32px` square triggers featuring single-color SVG icons. Unselected state rests on transparent dark slate; selected state features an electric blue accent edge (`#0284c7`), high-contrast white glyph, and subtle background tint (`#0284c7/15`).
- **Clinical Primary Action:** `h-8` (`32px`), `Inter` 12px semibold, background `#0284c7`, text `#ffffff`, hover `#0369a1`. Active states respond instantly without spring/bounce physics.

### Diagnostic Class Selector Chips
- Interactive chips for categorizing active patches (Tumor, Stroma, Normal, etc.).
- Feature a `6px` solid color indicator dot, label in `Inter` 12px Medium, and a numerical keyboard shortcut cue in `JetBrains Mono` (`1`, `2`, `3`).
- Selected state adopts the class hue's soft pastel background (`5%` opacity) with a solid `1px` border matching the class color token.

### Level-0 Coordinate Badge Pills
- Compact status tokens displaying real-time slide spatial data: `X: 142,800  Y: 84,200  |  20x  |  0.25 µm/px`.
- Rendered in `JetBrains Mono` 10px (`label-sm`), `#0f172a` text on `#f1f5f9` surface with a `1px` `#cbd5e1` outline.

### Virtual Patch Matrix & Histology Preview Cards
- Grid containers displaying extracted `256x256` or `512x512` patches.
- Each thumbnail includes an overlay header displaying patch status (e.g., `Class: Stroma (0.94)`), a bounding box indicator, and a bottom coordinate footer.
- Rejected/Excluded patches reveal a semi-transparent diagonal hash overlay (`#64748b` pattern) with instantaneous undo actions.

### Multi-Step Annotation Wizard & Batch Progress Meter
- Step wizards use linear progress bars (`2px` thickness, `#e2e8f0` track with `#0284c7` fill).
- Coordinate inspection tables feature zebra striping (`#ffffff` and `#f8fafc`), sticky headers with monospace column headers, and sub-pixel vertical alignment for quick numerical scanning.