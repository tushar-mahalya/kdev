// Site-wide behaviour. `boot` runs on every page load, including client-side
// navigations; listeners bound to the document are tied to an AbortController
// that is cancelled before the next swap.

let ctl: AbortController | null = null;
document.addEventListener('astro:before-swap', () => ctl?.abort());

export function boot() {
  ctl?.abort();
  ctl = new AbortController();
  const signal = ctl.signal;
  scrollState(signal);
  themeToggle(signal);
  menu(signal);
  copyButtons(signal);
  search(signal);
}

function scrollState(signal: AbortSignal) {
  const root = document.documentElement;
  const on = () => root.toggleAttribute('data-scrolled', window.scrollY > 8);
  on();
  window.addEventListener('scroll', on, { passive: true, signal });
}

function currentTheme(): 'light' | 'dark' {
  const set = document.documentElement.dataset.theme;
  if (set === 'light' || set === 'dark') return set;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function themeToggle(signal: AbortSignal) {
  document.querySelectorAll<HTMLButtonElement>('[data-theme-toggle]').forEach((b) => {
    b.addEventListener(
      'click',
      () => {
        const next = currentTheme() === 'dark' ? 'light' : 'dark';
        const apply = () => {
          document.documentElement.dataset.theme = next;
          try { localStorage.setItem('kdev-theme', next); } catch (_) { /* private mode */ }
          window.dispatchEvent(new CustomEvent('kdev:theme'));
        };
        const doc = document as Document & { startViewTransition?: (cb: () => void) => { ready: Promise<void>; finished: Promise<void> } };
        if (doc.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
          // A transition skipped by a resize rejects `ready`; the theme still applies.
          const t = doc.startViewTransition(apply);
          t.ready.catch(() => {});
          t.finished.catch(() => {});
        }
        else apply();
      },
      { signal },
    );
  });
}

function menu(signal: AbortSignal) {
  const btn = document.querySelector<HTMLButtonElement>('[data-menu-toggle]');
  const panel = document.querySelector<HTMLElement>('[data-menu]');
  if (!btn || !panel) return;
  const set = (open: boolean) => {
    btn.setAttribute('aria-expanded', String(open));
    btn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    panel.hidden = !open;
    document.documentElement.toggleAttribute('data-menu-open', open);
    document.body.style.overflow = open ? 'hidden' : '';
  };
  set(false);
  btn.addEventListener('click', () => set(btn.getAttribute('aria-expanded') !== 'true'), { signal });
  panel.addEventListener('click', (e) => { if ((e.target as HTMLElement).closest('a')) set(false); }, { signal });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') set(false); }, { signal });
}

const COPY_ICON = '<svg class="i-copy" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="5" y="5" width="8.5" height="8.5" rx="2"/><path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5"/></svg><svg class="i-done" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m3.5 8.5 3 3 6-7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function copyButtons(signal: AbortSignal) {
  // Code blocks in prose get a copy button too.
  document.querySelectorAll<HTMLPreElement>('.prose pre').forEach((pre) => {
    if (pre.querySelector('.copy')) return;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'copy copy--pre';
    b.setAttribute('aria-label', 'Copy code');
    b.dataset.copyFrom = 'pre';
    b.innerHTML = COPY_ICON;
    pre.appendChild(b);
  });
  document.addEventListener(
    'click',
    async (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-copy], [data-copy-from]');
      if (!b) return;
      const text = b.dataset.copy ?? b.closest('pre')?.querySelector('code')?.innerText ?? '';
      try {
        await navigator.clipboard.writeText(text.trim());
        b.setAttribute('data-copied', '');
        const label = b.getAttribute('aria-label');
        b.setAttribute('aria-label', 'Copied');
        setTimeout(() => { b.removeAttribute('data-copied'); if (label) b.setAttribute('aria-label', label); }, 1600);
      } catch (_) { /* clipboard blocked: the text is still selectable */ }
    },
    { signal },
  );
}

type PagefindResult = { data: () => Promise<{ url: string; meta: { title?: string }; excerpt: string }> };
type Pagefind = { search: (q: string) => Promise<{ results: PagefindResult[] }>; init?: () => Promise<void> };
let pagefind: Pagefind | null = null;

async function loadPagefind(): Promise<Pagefind | null> {
  if (pagefind) return pagefind;
  try {
    const url = '/pagefind/pagefind.js';
    pagefind = (await import(/* @vite-ignore */ url)) as Pagefind;
    await pagefind.init?.();
  } catch (_) {
    pagefind = null;
  }
  return pagefind;
}

function search(signal: AbortSignal) {
  const dlg = document.querySelector<HTMLDialogElement>('[data-search]');
  const input = document.querySelector<HTMLInputElement>('[data-search-input]');
  const out = document.querySelector<HTMLElement>('[data-search-results]');
  if (!dlg || !input || !out) return;
  const open = () => {
    if (dlg.open) return;
    dlg.showModal();
    input.select();
    void loadPagefind();
  };
  document.querySelectorAll('[data-search-open]').forEach((b) => {
    b.addEventListener('click', open, { signal });
    // Warm the index before the click, so the first query is instant.
    b.addEventListener('pointerenter', () => void loadPagefind(), { signal, once: true });
    b.addEventListener('focus', () => void loadPagefind(), { signal, once: true });
  });
  dlg.querySelector('[data-search-close]')?.addEventListener('click', () => dlg.close(), { signal });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); }, { signal });
  out.addEventListener('click', (e) => { if ((e.target as HTMLElement).closest('a')) dlg.close(); }, { signal });
  document.addEventListener(
    'keydown',
    (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); open(); }
      else if (e.key === '/' && !(e.target as HTMLElement).closest('input, textarea, [contenteditable]')) { e.preventDefault(); open(); }
    },
    { signal },
  );

  dlg.querySelector('[data-search-form]')?.addEventListener('submit', (e) => {
    e.preventDefault();
    out.querySelector<HTMLAnchorElement>('a')?.click();
  }, { signal });

  let seq = 0;
  const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
  input.addEventListener(
    'input',
    async () => {
      const q = input.value.trim();
      const mine = ++seq;
      if (!q) { out.innerHTML = '<p class="search__hint">Type to search every docs page.</p>'; return; }
      if (!pagefind) out.innerHTML = '<p class="search__hint">Searching…</p>';
      const pf = await loadPagefind();
      if (!pf) { out.innerHTML = '<p class="search__hint">Search is built with the site. Run <code>npm run build</code> to try it locally.</p>'; return; }
      const res = await pf.search(q);
      const items = await Promise.all(res.results.slice(0, 8).map((r) => r.data()));
      if (mine !== seq) return;
      out.innerHTML = items.length
        ? items.map((d) => `<a href="${esc(d.url)}"><div class="r-t">${esc(d.meta.title ?? d.url)}</div><div class="r-x">${d.excerpt}</div></a>`).join('')
        : `<p class="search__hint">Nothing for “${esc(q)}”. Try <em>restore</em>, <em>quota</em> or <em>tunnel</em>.</p>`;
    },
    { signal },
  );
}
