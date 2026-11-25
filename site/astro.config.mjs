import { defineConfig } from "astro/config";

// Served from the custom domain (see public/CNAME). For a GitHub Pages project URL instead, build with
// SITE_URL=https://<user>.github.io SITE_BASE=/vototransparente and remove public/CNAME.
const site = process.env.SITE_URL ?? "https://vototransparente.pt";
const base = process.env.SITE_BASE ?? "/";

export default defineConfig({
  site,
  base,
  trailingSlash: "always",
  build: { format: "directory", concurrency: 4 },
  vite: { build: { assetsInlineLimit: 0 } },
});
