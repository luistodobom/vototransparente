// Full voting record of one MP. The page embeds one character per vote of each legislature;
// the vote index (titles/dates) is fetched lazily per legislature and shared across MP pages (HTTP cache).
type VoteRow = [string, string, string, string, string, string, string]; // id, iniId, date, phase, result(A/R/…), title, ref
const D = JSON.parse(document.getElementById("mp-data")!.textContent!) as { columns: Record<string, string>; dissent: Record<string, string[]> };
const BASE = (window as any).__BASE as string;
const LABEL: Record<string, string> = { F: "A favor", C: "Contra", A: "Abstenção", X: "Não votou" };
const PHASE: Record<string, string> = { generalidade: "Generalidade", especialidade: "Especialidade", final_global: "Votação final global", deliberacao: "Votação", global: "Votação global", final: "Votação final", novo_decreto: "Novo decreto", confirmacao_decreto: "Confirmação do decreto" };
const MONTHS = ["jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez."];
const fmt = (iso: string) => { const [y, m, d] = iso.split("-").map(Number); return `${d} ${MONTHS[m - 1]} ${y}`; };
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const norm = (s: string) => s.toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const PAGE = 50;

const cache = new Map<string, VoteRow[]>();
let leg = document.querySelector<HTMLButtonElement>("[role=tab][aria-selected=true]")?.dataset.leg ?? Object.keys(D.columns)[0];
let shown = PAGE;

async function index(l: string) {
  if (!cache.has(l)) cache.set(l, await (await fetch(`${BASE}/data/parlamento/${l}/votes.json`)).json());
  return cache.get(l)!;
}

async function render() {
  const rows = await index(leg);
  const col = D.columns[leg];
  const dis = new Set(D.dissent[leg] ?? []);
  const q = norm($<HTMLInputElement>("rq").value.trim());
  const v = $<HTMLSelectElement>("rv").value;
  const onlyD = $<HTMLInputElement>("rd").checked;
  const list: { r: VoteRow; c: string; d: boolean }[] = [];
  for (let i = rows.length - 1; i >= 0; i--) {
    const c = col[i];
    if (!c || c === "-") continue;
    const r = rows[i];
    const d = dis.has(r[0]);
    if (v && c !== v) continue;
    if (onlyD && !d) continue;
    if (q && !norm(r[5] + " " + r[6]).includes(q)) continue;
    list.push({ r, c, d });
  }
  $("rcount").textContent = `${list.length.toLocaleString("pt-PT")} ${list.length === 1 ? "votação" : "votações"}`;
  $("rlist").innerHTML = list.slice(0, shown).map(({ r, c, d }) => {
    const res = r[4] === "A" ? "Aprovado" : r[4] === "R" ? "Rejeitado" : "";
    return `<li><a href="${BASE}/parlamento/votacao/${r[0]}/"><span class="d mono xs muted">${fmt(r[2])}</span>
      <span class="t">${esc(r[5])}<small>${esc(r[6])} · ${PHASE[r[3]] ?? r[3]}${res ? " · " + res : ""}${d ? " · <b>votou contra o grupo</b>" : ""}</small></span>
      <span class="pos ${c}${d ? " dz" : ""}">${LABEL[c]}</span></a></li>`;
  }).join("");
  $<HTMLButtonElement>("rmore").hidden = shown >= list.length;
}

for (const id of ["rq", "rv", "rd"]) $(id).addEventListener("input", () => { shown = PAGE; render(); });
$("rmore").addEventListener("click", () => { shown += PAGE * 2; render(); });
document.querySelectorAll<HTMLButtonElement>("#record [role=tab]").forEach((t) =>
  t.addEventListener("click", () => {
    document.querySelectorAll<HTMLButtonElement>("#record [role=tab]").forEach((x) => x.setAttribute("aria-selected", String(x === t)));
    leg = t.dataset.leg!;
    shown = PAGE;
    render();
  }),
);
render().catch(() => { $("rcount").textContent = "Não foi possível carregar o registo."; });
