import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

const docs = defineCollection({
  loader: glob({ pattern: '*.md', base: './src/content/docs' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    group: z.enum(['Start', 'Concepts', 'Reference', 'Project']),
    order: z.number(),
  }),
});

// The repository's own CHANGELOG.md is the single source for /changelog.
const changelog = defineCollection({
  loader: glob({ pattern: 'CHANGELOG.md', base: '..' }),
});

export const collections = { docs, changelog };
