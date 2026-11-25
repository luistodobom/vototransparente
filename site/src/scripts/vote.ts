// Per-MP breakdown of one vote. The page embeds one character per MP (aligned with the
// legislature roster); the roster itself (~230–400 MPs) is fetched once per legislature and cached.
type RosterRow = [string, string, string | null, [string, string, string | null][], number];
const D = JSON.parse(document.getElementById("vote-data")!.textContent!) as {
  row: string; date: string; leg: string; positions: Record<string, string>; colors: Record<string, string>; file: string; title: string;
  absent: Record<string, [string, string | null]>;
};
const SHORT: Record<string, string> = { "Falta Justificada (FJ)": "Falta justificada", "Ausência em Missão Parlamentar (AMP)": "Em missão parlamentar", "Falta Injustificada (FI)": "Falta injustificada" };
const BASE = (window as any).__BASE as string;
const LABEL: Record<string, string> = { F: "A favor", C: "Contra", A: "Abstenção", X: "Não votou" };
const POSCODE: Record<string, string> = { favor: "F", contra: "C", abstencao: "A", ausente: "X" };
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const LIMIT = 60;

interface MP { id: string; name: string; circle: string; party: string; code: string; dissent: boolean; why: string; photo: boolean }
let mps: MP[] = [];
let showAll = false;

const partyAt = (ps: RosterRow[3]) => (ps.find(([, f, t]) => f <= D.date && (!t || t >= D.date)) ?? ps[ps.length - 1])?.[0] ?? "?";
const initials = (n: string) => n.split(/\s+/).filter((w) => w && w[0] === w[0].toUpperCase()).slice(0, 2).map((w) => w[0]).join("");

async function load() {
  const res = await fetch(`${BASE}/data/parlamento/${D.leg}/mps.json`);
  const roster = (await res.json()) as RosterRow[];
  mps = [];
  roster.forEach(([id, name, circle, parties, photo], j) => {
    const code = D.row[j];
    if (!code || code === "-") return;
    const party = partyAt(parties);
    const gp = D.positions[party];
    const dissent = code !== "X" && !!gp && gp !== "ausente" && POSCODE[gp] !== code;
    const a = D.absent[id];
    const why = a ? `${SHORT[a[0]] ?? a[0]}${a[1] ? ` — ${a[1]}` : ""}` : "";
    mps.push({ id, name, circle: circle ?? "", party, code, dissent, why, photo: !!photo });
  });
  mps.sort((a, b) => a.name.localeCompare(b.name, "pt"));
  const circles = [...new Set(mps.map((m) => m.circle).filter(Boolean))].sort((a, b) => a.localeCompare(b, "pt"));
  $<HTMLSelectElement>("fc").insertAdjacentHTML("beforeend", circles.map((c) => `<option>${esc(c)}</option>`).join(""));
  render();
  renderCircles();
}

function filtered() {
  const q = $<HTMLInputElement>("fq").value.trim().toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
  const p = $<HTMLSelectElement>("fp").value, c = $<HTMLSelectElement>("fc").value, v = $<HTMLSelectElement>("fv").value;
  return mps.filter((m) =>
    (!q || m.name.toLowerCase().normalize("NFD").replace(/\p{M}/gu, "").includes(q)) &&
    (!p || m.party === p) && (!c || m.circle === c) && (!v || m.code === v));
}

function card(m: MP) {
  const col = D.colors[m.party] ?? "#888";
  return `<li><a class="mp${m.dissent ? " dissent" : ""}" href="${BASE}/parlamento/deputados/${m.id}/" title="${m.dissent ? "Votou de forma diferente do seu grupo" : ""}">
    <span class="av2" style="background:color-mix(in srgb, ${col} 20%, var(--bg-sunken));color:${col}">${esc(initials(m.name))}${m.photo ? `<img src="${BASE}/img/mp/${m.id}.webp" alt="" loading="lazy" onerror="this.remove()">` : ""}</span>
    <span class="who"><b>${esc(m.name)}</b><span>${esc(m.party)}${m.circle ? " · " + esc(m.circle) : ""}</span></span>
    <span class="pos ${m.code}" title="${esc(m.why)}">${m.why ? esc(m.why.split(" — ")[0]) : LABEL[m.code]}</span></a></li>`;
}

function render() {
  const list = filtered();
  const shown = showAll ? list : list.slice(0, LIMIT);
  $("mps").innerHTML = shown.length ? shown.map(card).join("") : `<li class="muted small">Nenhum deputado corresponde aos filtros.</li>`;
  const more = $<HTMLButtonElement>("more");
  more.hidden = shown.length >= list.length;
  more.textContent = `Mostrar todos (${list.length})`;
}

function renderCircles() {
  const agg = new Map<string, Record<string, number>>();
  for (const m of mps) {
    const k = m.circle || "—";
    const a = agg.get(k) ?? { F: 0, C: 0, A: 0, X: 0 };
    a[m.code]++;
    agg.set(k, a);
  }
  const rows = [...agg.entries()].sort((a, b) => a[0].localeCompare(b[0], "pt"));
  $("circ-table").innerHTML = `<thead><tr><th>Círculo</th><th class="num">A favor</th><th class="num">Contra</th><th class="num">Abst.</th><th class="num">Não votou</th></tr></thead><tbody>` +
    rows.map(([c, a]) => `<tr><th scope="row">${esc(c)}</th><td class="num">${a.F || ""}</td><td class="num">${a.C || ""}</td><td class="num">${a.A || ""}</td><td class="num muted">${a.X || ""}</td></tr>`).join("") + "</tbody>";
}

function csv() {
  const q = (s: string) => `"${String(s).replace(/"/g, '""')}"`;
  const lines = [["id_deputado", "nome", "partido", "circulo", "voto", "votou_contra_o_grupo", "falta_registada"].join(",")]
    .concat(mps.map((m) => [m.id, q(m.name), m.party, q(m.circle), LABEL[m.code], m.dissent ? "sim" : "nao", q(m.why)].join(",")));
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = D.file;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

for (const id of ["fq", "fp", "fc", "fv"]) $(id).addEventListener("input", () => { showAll = false; render(); });
$("more").addEventListener("click", () => { showAll = true; render(); });
$("csv").addEventListener("click", csv);
document.querySelectorAll<HTMLButtonElement>("[role=tab]").forEach((t) =>
  t.addEventListener("click", () => {
    document.querySelectorAll<HTMLButtonElement>("[role=tab]").forEach((x) => x.setAttribute("aria-selected", String(x === t)));
    document.querySelectorAll<HTMLElement>("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== t.dataset.tab));
  }),
);
load().catch(() => { $("mps").innerHTML = `<li class="muted small">Não foi possível carregar a lista de deputados.</li>`; });
