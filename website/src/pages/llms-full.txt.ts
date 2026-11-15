import type { APIRoute } from 'astro';
import { getCollection } from 'astro:content';
import { site } from '../data/site';
import { plainMarkdown } from '../lib/plain-markdown';

export const GET: APIRoute = async () => {
  const docs = (await getCollection('docs')).sort((a, b) => a.data.order - b.data.order);
  const text = [`# kdev: full documentation\n\n> ${site.description}\n\nSource: ${site.repo}\n`, ...docs.map((d) => `\n---\n\n# ${d.data.title}\n\n> ${d.data.description}\n\n${plainMarkdown(d.body ?? '')}`)].join('\n');
  return new Response(text, { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
};
