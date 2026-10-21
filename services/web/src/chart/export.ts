/** Deck-ready SVG and PNG of the chart as drawn: hover marks removed, and the theme's CSS
 * classes replaced by a light palette embedded in the file (white background), so the export
 * looks the same in any slide whatever theme the app is in. */

/** tokens.css light theme: --series-0..7, --text, --text-3, --line. */
const SERIES = ["#3b5bdb", "#2f9e44", "#e8590c", "#c92a2a", "#0c8599", "#7048e8", "#d6336c", "#495057"];
const EXPORT_CSS = [
  "svg{background:#fff;font-family:Inter,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}",
  ".ch-bg{fill:#fff}",
  ".ch-title{fill:#14171c;font-size:15px;font-weight:650}",
  ".ch-legend text{fill:#4a525e;font-size:11.5px}",
  ".ch-grid line{stroke:#e3e6eb}",
  ".ch-axis text,.ch-grid text{fill:#7a8390;font-size:10.5px}",
  ".ch-zero{stroke:#d3d8df}",
  ".ch-foot{fill:#7a8390;font-size:10.5px;font-style:italic}",
  ...SERIES.map((c, i) => `.c${i}{color:${c}}`),
].join("");

/** The chart's SVG as a standalone file. */
export function svgText(svg: SVGSVGElement): string {
  const copy = svg.cloneNode(true) as SVGSVGElement;
  copy.querySelectorAll(".hover-only").forEach((el) => el.remove());
  const [, , w, h] = (copy.getAttribute("viewBox") ?? "0 0 900 400").split(" ");
  copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  copy.setAttribute("width", w!);
  copy.setAttribute("height", h!);
  copy.removeAttribute("class");
  const style = document.createElementNS("http://www.w3.org/2000/svg", "style");
  style.textContent = EXPORT_CSS;
  copy.insertBefore(style, copy.firstChild);
  return new XMLSerializer().serializeToString(copy);
}

/** Renders the SVG text at 2x into a PNG. The image loads from a data: URL, which the CSP's
 * img-src allows. */
export function pngBlob(svg: string, width: number, height: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = width * 2;
      canvas.height = height * 2;
      const ctx = canvas.getContext("2d");
      if (!ctx) return reject(new Error("no canvas"));
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("no PNG"))), "image/png");
    };
    img.onerror = () => reject(new Error("couldn't render the SVG"));
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  });
}

/** "Revenue and margin" → "revenue-and-margin" for file names. */
export function slug(title: string): string {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "chart";
}
