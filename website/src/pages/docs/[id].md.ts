import type { APIRoute, GetStaticPaths } from 'astro';
import { getCollection } from 'astro:content';
import { plainMarkdown } from '../../lib/plain-markdown';

// Every docs page as plain Markdown, for people and language models that
// would rather read the source than the rendered page.
export const getStaticPaths: GetStaticPaths = async () =>
  (await getCollection('docs')).map((entry) => ({ params: { id: entry.id }, props: { entry } }));

export const GET: APIRoute = ({ props }) => {
  const { entry } = props as { entry: Awaited<ReturnType<typeof getCollection<'docs'>>>[number] };
  const body = `# ${entry.data.title}\n\n> ${entry.data.description}\n\n${plainMarkdown(entry.body ?? '')}`;
  return new Response(body, { headers: { 'Content-Type': 'text/markdown; charset=utf-8' } });
};
