import type { APIRoute } from 'astro';

// Search engines and AI assistants are welcome: the docs exist to be found.
const bots = ['Googlebot', 'Bingbot', 'GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'ClaudeBot', 'Claude-SearchBot', 'Claude-User', 'PerplexityBot', 'Perplexity-User', 'Google-Extended', 'Applebot', 'Applebot-Extended', 'DuckDuckBot', 'CCBot'];

export const GET: APIRoute = ({ site }) =>
  new Response(
    `${bots.map((b) => `User-agent: ${b}\nAllow: /\n`).join('\n')}\nUser-agent: *\nAllow: /\n\nSitemap: ${new URL('/sitemap-index.xml', site)}\n# LLM summary: ${new URL('/llms.txt', site)}\n`,
    { headers: { 'Content-Type': 'text/plain; charset=utf-8' } },
  );
