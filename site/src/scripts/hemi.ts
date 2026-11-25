// Draws every .hemi[data-runs] as a semicircle of seats. Runs are [label, color, count], left → right.
const NS = "http://www.w3.org/2000/svg";
const ROWS = 8, R0 = 0.42, R1 = 1, SEAT = 0.032;
const geo = new Map<number, { x: number; y: number }[]>();

function seats(total: number) {
  if (geo.has(total)) return geo.get(total)!;
  const radii = Array.from({ length: ROWS }, (_, i) => R0 + ((R1 - R0) * i) / (ROWS - 1));
  const sum = radii.reduce((a, b) => a + b, 0);
  const counts = radii.map((r) => Math.floor((total * r) / sum));
  let rem = total - counts.reduce((a, b) => a + b, 0);
  for (let i = ROWS - 1; rem > 0; i = (i - 1 + ROWS) % ROWS, rem--) counts[i]++;
  const pts: { x: number; y: number; a: number }[] = [];
  radii.forEach((r, i) => {
    for (let j = 0; j < counts[i]; j++) {
      const a = Math.PI - (Math.PI * (j + 0.5)) / counts[i];
      pts.push({ x: r * Math.cos(a), y: -r * Math.sin(a), a });
    }
  });
  pts.sort((p, q) => q.a - p.a || Math.hypot(p.x, p.y) - Math.hypot(q.x, q.y));
  geo.set(total, pts);
  return pts;
}

function draw(el: HTMLElement) {
  const runs = JSON.parse(el.dataset.runs || "[]") as [string, string, number][];
  const total = runs.reduce((a, r) => a + r[2], 0);
  if (!total) return;
  const pts = seats(total);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "-1.06 -1.04 2.12 1.1");
  svg.setAttribute("aria-hidden", "true");
  let k = 0;
  for (const [label, color, n] of runs) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("fill", color);
    const t = document.createElementNS(NS, "title");
    t.textContent = `${label} (${n})`;
    g.append(t);
    for (let i = 0; i < n; i++, k++) {
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", pts[k].x.toFixed(3));
      c.setAttribute("cy", pts[k].y.toFixed(3));
      c.setAttribute("r", String(SEAT));
      g.append(c);
    }
    svg.append(g);
  }
  el.replaceChildren(svg);
}

document.querySelectorAll<HTMLElement>(".hemi[data-runs]").forEach(draw);
