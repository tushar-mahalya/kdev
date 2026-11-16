// Docs pages may hold diagrams as inline HTML (<figure class="fig">). The
// plain-text outputs (/docs/*.md and /llms-full.txt) carry each figure's
// caption instead of its markup.
const entities: Record<string, string> = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" };

export function plainMarkdown(body: string): string {
  return body.replace(/<figure\b[^>]*>[\s\S]*?<\/figure>/g, (figure) => {
    const caption = /<figcaption[^>]*>([\s\S]*?)<\/figcaption>/.exec(figure)?.[1] ?? '';
    let text = caption
      .replace(/&(amp|lt|gt|quot|#39);/g, (e) => entities[e])
      .replace(/<code>([\s\S]*?)<\/code>/g, '`$1`')
      .replace(/<\/?(b|strong)>/g, '**');
    // Strip tags until none are left (a nested `<scr<b>ipt>` needs two
    // passes), then drop any stray bracket, so no markup survives.
    for (let previous = ''; previous !== text; ) {
      previous = text;
      text = text.replace(/<[^<>]*>/g, '');
    }
    text = text.replace(/[<>]/g, '').replace(/\s+/g, ' ').trim();
    return text ? `> Diagram: ${text}` : '';
  });
}
