export const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");
export const url = (p: string) => `${BASE}${p.startsWith("/") ? p : "/" + p}`;

const MONTHS = ["jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez."];
const MONTHS_LONG = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];

export function fmtDate(iso?: string | null, long = false) {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  return long ? `${d} de ${MONTHS_LONG[m - 1]} de ${y}` : `${d} ${MONTHS[m - 1]} ${y}`;
}

export const fmtNum = (n: number) => n.toLocaleString("pt-PT");
export const pct = (a: number, b: number) => (b ? Math.round((100 * a) / b) : 0);

export const POSITION_LABEL: Record<string, string> = {
  favor: "A favor",
  contra: "Contra",
  abstencao: "Abstenção",
  ausente: "Ausente",
};

export const STATUS_LABEL: Record<string, string> = {
  aprovado: "Aprovada",
  rejeitado: "Rejeitada",
  em_curso: "Aprovada na generalidade",
  outro: "Outro",
};

export const PHASE_LABEL: Record<string, string> = {
  generalidade: "Generalidade",
  especialidade: "Especialidade",
  final_global: "Votação final global",
  deliberacao: "Votação",
  global: "Votação global",
  final: "Votação final",
  novo_decreto: "Novo decreto",
  confirmacao_decreto: "Confirmação do decreto",
};

export const FISCAL_LABEL: Record<string, string> = {
  aumenta_despesa: "Aumenta a despesa",
  reduz_despesa: "Reduz a despesa",
  aumenta_receita: "Aumenta a receita",
  reduz_receita: "Reduz a receita",
  misto: "Efeito misto",
  neutro: "Sem impacto relevante",
  incerto: "Impacto incerto",
};

export const resultClass = (r?: string | null) => {
  const x = (r ?? "").toLowerCase();
  return x.startsWith("aprov") ? "ok" : x.startsWith("rejeit") ? "no" : "neutral";
};

export const voteCode: Record<string, string> = { favor: "F", contra: "C", abstencao: "A", ausente: "X" };
