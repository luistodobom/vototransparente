// Static JSON: the roster of one legislature, aligned with the vote matrix columns.
// Fetched lazily (once, then cached) by vote pages and the MP directory.
import type { APIRoute } from "astro";
import { deputies, mpVotes } from "../../../../lib/data";

export function getStaticPaths() {
  return Object.keys(mpVotes()).map((leg) => ({ params: { leg } }));
}

export const GET: APIRoute = ({ params }) => {
  const leg = params.leg!;
  const deps = deputies();
  const rows = mpVotes()[leg].mps.map((id) => {
    const L = deps[id].legs[leg];
    return [id, deps[id].name, L.circle, L.parties.map((p) => [p.party, p.from, p.to]), deps[id].photo ? 1 : 0];
  });
  return new Response(JSON.stringify(rows), { headers: { "Content-Type": "application/json" } });
};
