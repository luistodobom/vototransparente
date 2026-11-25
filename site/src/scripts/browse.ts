// Client-side browsing powered by Pagefind: the index is split into small chunks and
// only the pieces needed for the current query/filters/page are downloaded.
type PF = any;
const VT = (window as any).__VT as { partyInfo: Record<string, { n: string; c: string }>; partyOrder: string[]; base: string };
const PAGE = 20;
const STATUS: Record<string, [string, string]> = {
  aprovado: ["Aprovada", "ok"],
  rejeitado: ["Rejeitada", "no"],
  em_curso: ["Aprovada na generalidade", "mid"],
  outro: ["Outro", "neutral"],
};
const MONTHS = ["jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez."];
const fmtDate = (iso: string) => {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
};
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const q = $<HTMLInputElement>("q");
const sortSel = $<HTMLSelectElement>("sort");
const results = $<HTMLOListElement>("results");
const count = $<HTMLParagraphElement>("count");
const more = $<HTMLButtonElement>("more");
const partySel = $<HTMLSelectElement>("f-party");
const posSel = $<HTMLSelectElement>("f-pos");
const filterSels = [...document.querySelectorAll<HTMLSelectElement>("select[data-filter]")];

let pagefind: PF;
let current: any[] = [];
let shown = 0;
let token = 0;

function readURL() {
  const p = new URLSearchParams(location.search);
  q.value = p.get("q") ?? "";
  for (const s of filterSels) s.dataset.want = p.get(s.dataset.filter!) ?? "";
  partySel.value = p.get("partido") ?? "";
  posSel.value = p.get("voto") ?? "";
  sortSel.value = p.get("ordem") ?? (q.value ? "rel" : "desc");
}

function writeURL() {
  const p = new URLSearchParams();
  if (q.value.trim()) p.set("q", q.value.trim());
  for (const s of filterSels) if (s.value) p.set(s.dataset.filter!, s.value);
  if (partySel.value) p.set("partido", partySel.value);
  if (posSel.value) p.set("voto", posSel.value);
  const defSort = q.value.trim() ? "rel" : "desc";
  if (sortSel.value !== defSort) p.set("ordem", sortSel.value);
  const s = p.toString();
  history.replaceState(null, "", s ? `?${s}` : location.pathname);
}

const fwrap = document.getElementById("fwrap") as HTMLDetailsElement;
if (matchMedia("(max-width: 899px)").matches) fwrap.open = false;

function filters() {
  const f: Record<string, string> = {};
  for (const s of filterSels) if (s.value) f[s.dataset.filter!] = s.value;
  if (partySel.value && posSel.value) f[`voto_${partySel.value}`] = posSel.value;
  const n = Object.keys(f).length;
  ($("fcount") as HTMLElement).textContent = n ? `${n} ativo${n > 1 ? "s" : ""}` : "";
  return f;
}

async function populate() {
  const counts = await pagefind.filters();
  for (const s of filterSels) {
    const key = s.dataset.filter!;
    const vals = counts[key] ?? {};
    const existing = new Set([...s.options].map((o) => o.value));
    let entries = Object.entries(vals) as [string, number][];
    if (key === "Proponente") {
      const ord = [...VT.partyOrder, "Governo"];
      entries.sort((a, b) => (ord.indexOf(a[0]) + 1 || 99) - (ord.indexOf(b[0]) + 1 || 99) || b[1] - a[1]);
    } else if (key !== "Legislatura") entries.sort((a, b) => b[1] - a[1]);
    for (const [v, n] of entries) {
      if (existing.has(v)) {
        const o = [...s.options].find((o) => o.value === v)!;
        o.textContent = `${o.textContent} (${n})`;
        continue;
      }
      const o = document.createElement("option");
      o.value = v;
      o.textContent = `${v} (${n})`;
      s.append(o);
    }
    if (s.dataset.want) s.value = s.dataset.want;
  }
}

function skeleton() {
  results.innerHTML = Array.from({ length: 5 }, () => `<li class="sk-row skeleton"></li>`).join("");
}

async function run() {
  const my = ++token;
  writeURL();
  skeleton();
  const term = q.value.trim() || null;
  const sort = sortSel.value === "rel" && term ? undefined : { date: sortSel.value === "asc" ? "asc" : "desc" };
  const res = term
    ? await pagefind.debouncedSearch(term, { filters: filters(), sort }, 250)
    : await pagefind.search(null, { filters: filters(), sort });
  if (my !== token || res === null) return;
  current = res.results;
  shown = 0;
  results.innerHTML = "";
  count.textContent = current.length
    ? `${current.length.toLocaleString("pt-PT")} ${current.length === 1 ? "iniciativa" : "iniciativas"}`
    : "Nenhum resultado. Experimente outras palavras ou remova filtros.";
  await page(my);
}

async function page(my = token) {
  const slice = current.slice(shown, shown + PAGE);
  const data = await Promise.all(slice.map((r) => r.data()));
  if (my !== token) return;
  results.insertAdjacentHTML("beforeend", data.map(render).join(""));
  shown += slice.length;
  more.hidden = shown >= current.length;
}

function render(d: any) {
  const m = d.meta ?? {};
  const [label, cls] = STATUS[m.status] ?? STATUS.outro;
  const strip = (m.votes ?? "")
    .split(",")
    .filter(Boolean)
    .map((x: string) => {
      const [p, v] = x.split(":");
      const c = VT.partyInfo[p]?.c ?? "#999";
      const t = { F: "a favor", C: "contra", A: "absteve-se", X: "ausente" }[v] ?? "";
      return `<span class="pv ${v}" title="${esc(p)} ${t}"><i style="background:${c}"></i>${esc(p)}</span>`;
    })
    .join("");
  const showExcerpt = q.value.trim() && d.excerpt;
  return `<li><a class="r card" href="${esc(d.url)}">
    <div class="rt"><span class="badge ${cls}">${label}</span><span class="xs muted mono">${fmtDate(m.date)}</span><span class="xs muted">· ${esc(m.ref ?? "")} · ${esc(m.author ?? "")}</span></div>
    <h3>${esc(m.title ?? "")}</h3>
    ${showExcerpt ? `<p class="ex">${d.excerpt}</p>` : ""}
    <div class="strip">${strip}</div>
  </a></li>`;
}

async function init() {
  readURL();
  skeleton();
  try {
    pagefind = await import(/* @vite-ignore */ `${VT.base.replace(/\/$/, "")}/pagefind/pagefind.js`);
    await pagefind.options({ excerptLength: 26 });
    await pagefind.init();
    await populate();
  } catch (e) {
    count.textContent = "O índice de pesquisa ainda não foi gerado (corra `npm run build`).";
    results.innerHTML = "";
    console.error(e);
    return;
  }
  run();
}

q.addEventListener("input", () => {
  if (q.value.trim() && sortSel.value === "desc" && !new URLSearchParams(location.search).get("ordem")) sortSel.value = "rel";
  if (!q.value.trim() && sortSel.value === "rel") sortSel.value = "desc";
  run();
});
for (const s of [...filterSels, sortSel, posSel]) s.addEventListener("change", run);
partySel.addEventListener("change", () => { if (posSel.value) run(); else posSel.focus(); });
more.addEventListener("click", () => page());
$("clear").addEventListener("click", () => {
  for (const s of [...filterSels, partySel, posSel]) s.value = "";
  q.value = "";
  sortSel.value = "desc";
  run();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== q && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement)) {
    e.preventDefault();
    q.focus();
  }
});
init();
