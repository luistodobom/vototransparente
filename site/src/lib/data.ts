// Build-time data access. Nothing here is shipped to the browser.
import fs from "node:fs";
import path from "node:path";

export type Position = "favor" | "contra" | "abstencao" | "ausente";

export interface Vote {
  id: string;
  date: string;
  phase: string;
  phaseLabel: string;
  result: string | null;
  unanimous: boolean;
  description: string | null;
  positions: Record<string, Position>;
  splits: Record<string, Partial<Record<Position, number | null>>> | null;
  mps: { name: string; party: string; vote: Position }[] | null;
  meeting: string | null;
  inferred?: boolean;
  dar: { url: string; nr: string | null; sl: string | null; date: string | null; pages: string } | null;
  source?: "opendata" | "page";
}

export interface Summary {
  short_title: string;
  summary: string;
  key_points: string[];
  plain_language: string;
  context: string | null;
  analysis: string | null;
  fiscal_impact: { direction: string | null; explanation: string } | null;
  affected: string[];
  categories: string[];
  tags: string[];
  _meta: { model: string; generated: string | null; source: string; legacy?: boolean };
}

export interface Initiative {
  id: string;
  leg: string;
  type: string;
  typeSlug: string;
  number: string;
  session: string | null;
  ref: string;
  title: string;
  authors: { label: string; parties: string[]; deputies: { name: string; party: string; id: string }[]; other: string | null };
  dateIn: string | null;
  url: string;
  docUrl: string | null;
  timeline: { date: string; phase: string }[];
  votes: Vote[];
  lastVoteDate: string;
  lastResult: string | null;
  keyVoteId: string;
  status: "aprovado" | "rejeitado" | "em_curso" | "outro";
  categories: string[];
  summary: Summary | null;
}

export interface Meta {
  generated: string;
  parties: Record<string, { name: string; color: string }>;
  legislatures: Record<string, { label: string; start: string; end: string | null; seats: Record<string, number> }>;
  categories: Record<string, { label: string; count: number }>;
  types: Record<string, { label: string; count: number }>;
  stats: { initiatives: number; votes: number; summarized: number; byStatus: Record<string, number>; byLeg: Record<string, number> };
  sources: { generated: string; files: { label: string; page: string; file: string; retrieved: string; bytes: number; sha256: string | null }[] } | null;
}

export const OPEN_DATA_PAGE = "https://www.parlamento.pt/Cidadania/Paginas/DAIniciativas.aspx";
export const ROSTER_PAGE = "https://www.parlamento.pt/Cidadania/Paginas/DAInformacaoBase.aspx";
export const ATTENDANCE_PAGE = "https://www.parlamento.pt/DeputadoGP/Paginas/reunioesplenarias.aspx";
export const bioUrl = (id: string) => `https://www.parlamento.pt/DeputadoGP/Paginas/Biografia.aspx?BID=${id}`;
export const darLabel = (v: Vote) =>
  v.dar ? `Diário da Assembleia da República, I série${v.dar.nr ? ` n.º ${v.dar.nr}` : ""}${v.dar.date ? ` (${v.dar.date})` : ""}${v.dar.pages ? `, pág. ${v.dar.pages}` : ""}` : "";

export interface PartyStats {
  [leg: string]: {
    votes: Record<string, Partial<Record<Position | "total" | "winning", number>>>;
    agreement: Record<string, { same: number; total: number }>;
    authored: Record<string, Partial<Record<string, number>>>;
  };
}

const DATA_DIR = path.resolve(process.cwd(), "src/data");
const load = <T>(f: string): T => JSON.parse(fs.readFileSync(path.join(DATA_DIR, f), "utf8"));

let _ini: Initiative[] | undefined;
export const initiatives = (): Initiative[] => (_ini ??= load<Initiative[]>("initiatives.json"));
let _meta: Meta | undefined;
export const meta = (): Meta => (_meta ??= load<Meta>("meta.json"));
let _ps: PartyStats | undefined;
export const partyStats = (): PartyStats => (_ps ??= load<PartyStats>("parties.json"));

export const LEG_ORDER = ["XVII", "XVI", "XV", "XIV"];
export const currentLeg = () => LEG_ORDER.find((l) => meta().legislatures[l]) ?? "XVII";

export const keyVote = (i: Initiative): Vote => i.votes.find((v) => v.id === i.keyVoteId) ?? i.votes[i.votes.length - 1];

export const displayTitle = (i: Initiative) => i.summary?.short_title || i.title;

/** Parties in a legislature ordered left → right (seat map order). */
export const legParties = (leg: string) => Object.keys(meta().legislatures[leg]?.seats ?? {});

/** Seat-weighted tally of a vote using each legislature's seat map (and split counts when known). */
export function tally(v: Vote, leg: string) {
  const seats = meta().legislatures[leg]?.seats ?? {};
  const t: Record<Position, number> = { favor: 0, contra: 0, abstencao: 0, ausente: 0 };
  for (const [p, pos] of Object.entries(v.positions)) {
    const n = seats[p] ?? 1;
    const split = v.splits?.[p];
    if (split && Object.values(split).every((x) => typeof x === "number")) {
      let used = 0;
      for (const [k, c] of Object.entries(split)) { t[k as Position] += c as number; used += c as number; }
      if (n > used) t.ausente += n - used;
    } else if (split) {
      // counted minorities + the rest of the group in the uncounted position
      let used = 0;
      for (const [k, c] of Object.entries(split)) if (typeof c === "number") { t[k as Position] += c; used += c; }
      const rest = Object.entries(split).find(([, c]) => c == null)?.[0] as Position | undefined;
      t[rest ?? pos] += Math.max(0, n - used);
    } else t[pos] += n;
  }
  return t;
}

// ---------------------------------------------------------------- MPs (deputados)
export type VoteCode = "F" | "C" | "A" | "X" | "-" | "?";
export interface DeputyLeg {
  circle: string | null;
  parties: { party: string; from: string; to: string | null }[];
  party: string;
  inOffice: { from: string; to: string | null; status?: string }[];
  F: number; C: number; A: number; X: number;
  dissent: string[];
  known: number;          // votes on dates with an official attendance record
  absentVotes: number;    // of those, votes missed through a recorded absence
  meetings: Partial<Record<string, number>>; // { meetings, FJ, AMP, FI, … } plenary meetings in office / absences by type
}

let _att: Record<string, { absent: Record<string, [string, string | null]>; sources: string[] }> | undefined;
export const attendanceFor = (leg: string, date: string) =>
  (_att ??= exists("attendance.json") ? load("attendance.json") : {})[`${leg}|${date}`] ?? null;

/** Participation in votes (share of votes on attendance-covered dates not missed through an absence). */
export const participation = (L: DeputyLeg) => (L.known ? 1 - L.absentVotes / L.known : null);
export interface Deputy { id: string; name: string; fullName: string | null; photo: boolean; legs: Record<string, DeputyLeg> }
export interface LegMatrix { mps: string[]; votes: string[]; rows: string[]; partial: string[] }

const exists = (f: string) => fs.existsSync(path.join(DATA_DIR, f));
let _dep: Record<string, Deputy> | undefined;
export const deputies = (): Record<string, Deputy> => (_dep ??= exists("deputies.json") ? load("deputies.json") : {});
let _mv: Record<string, LegMatrix> | undefined;
export const mpVotes = (): Record<string, LegMatrix> => (_mv ??= exists("mpvotes.json") ? load("mpvotes.json") : {});
export const hasMpData = () => Object.keys(mpVotes()).length > 0;

let _vix: Map<string, { leg: string; i: number }> | undefined;
/** voteId -> position of its row in the legislature matrix */
export const voteRowIndex = () => {
  if (_vix) return _vix;
  _vix = new Map();
  for (const [leg, M] of Object.entries(mpVotes())) M.votes.forEach((v, i) => _vix!.set(v, { leg, i }));
  return _vix;
};

export const partyAt = (d: DeputyLeg, date: string) =>
  d.parties.find((p) => p.from <= date && (!p.to || p.to >= date))?.party ?? d.party;

let _vmap: Map<string, { vote: Vote; ini: Initiative }> | undefined;
export const voteById = () => {
  if (_vmap) return _vmap;
  _vmap = new Map();
  for (const ini of initiatives()) for (const v of ini.votes) _vmap.set(v.id, { vote: v, ini });
  return _vmap;
};

/** Per-MP view of one vote: [{dep, party, code}] for every MP in office. */
export function mpRow(voteId: string) {
  const loc = voteRowIndex().get(voteId);
  if (!loc) return null;
  const M = mpVotes()[loc.leg];
  const row = M.rows[loc.i];
  const { vote } = voteById().get(voteId)!;
  const deps = deputies();
  const out: { id: string; name: string; party: string; circle: string | null; code: VoteCode }[] = [];
  M.mps.forEach((id, j) => {
    const code = row[j] as VoteCode;
    if (code === "-") return;
    const d = deps[id];
    const L = d.legs[loc.leg];
    out.push({ id, name: d.name, party: partyAt(L, vote.date), circle: L.circle, code });
  });
  return { leg: loc.leg, partial: M.partial.includes(voteId), mps: out };
}

/** One MP's codes across a legislature's votes (aligned with mpVotes()[leg].votes). */
export function mpColumn(id: string, leg: string) {
  const M = mpVotes()[leg];
  const j = M?.mps.indexOf(id) ?? -1;
  if (j < 0) return "";
  return M.rows.map((r) => r[j]).join("");
}
