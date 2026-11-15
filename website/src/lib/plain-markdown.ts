// Docs pages may hold diagrams as inline HTML (<figure class="fig">). The
// plain-text outputs (/docs/*.md and /llms-full.txt) carry each figure's
// caption instead of its markup.
const entities: Record<string, string> = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" };

export function plainMarkdown(body: string): string {
  return body.replace(/<figure\b[^>]*>[\s\S]*?<\/figure>/g, (figure) => {
    const caption = /<figcaption[^>]*>([\s\S]*?)<\/figcaption>/.exec(figure)?.[1] ?? '';
    const text = caption
      .replace(/<code>([\s\S]*?)<\/code>/g, '`$1`')
      .replace(/<\/?(b|strong)>/g, '**')
      .replace(/<[^>]+>/g, '')
      .replace(/&(amp|lt|gt|quot|#39);/g, (e) => entities[e])
      .replace(/\s+/g, ' ')
      .trim();
    return text ? `> Diagram: ${text}` : '';
  });
}
