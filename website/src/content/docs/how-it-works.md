---
title: "How it works"
description: "The architecture, what kdev up does, how your files persist and come back, how accounts and machines share one box, and the security model."
group: Concepts
order: 3
---

Kaggle's API can save a notebook and run it, but it has no "give me a shell"
call. kdev writes a small cell at the top of your notebook. When Kaggle runs
that cell, it turns the container into an SSH server and connects it to your
Cloudflare tunnel. Your laptop then reaches it by name.

## The big picture

<figure class="fig" id="fig-architecture">
<div class="fig__art" data-pagefind-ignore>
<svg viewBox="0 0 760 400" role="img" aria-labelledby="f1-t">
<title id="f1-t">Your machine talks to the Kaggle API over HTTPS to start the box. The box and your ssh both dial out to Cloudflare's edge, which joins them by hostname. Saved versions are restored into the box at each start.</title>
<defs>
<marker id="f1-m" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah" d="M0 0L10 5L0 10z"/></marker>
<marker id="f1-ma" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah ah--a" d="M0 0L10 5L0 10z"/></marker>
<marker id="f1-mo" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah ah--ok" d="M0 0L10 5L0 10z"/></marker>
</defs>
<rect class="z" x="16" y="14" width="220" height="276" rx="16"/>
<text class="zl" x="34" y="36">Your machine</text>
<rect class="z" x="516" y="14" width="228" height="378" rx="16"/>
<text class="zl" x="534" y="36">Kaggle</text>
<path class="w w--a" d="M216 78H534" marker-end="url(#f1-ma)"/>
<text class="l" x="376" y="70" text-anchor="middle">HTTPS · save + run, quota, logs</text>
<path class="w w--d" d="M126 110V203" marker-end="url(#f1-m)"/>
<text class="s" x="136" y="152">writes</text>
<text class="s" x="136" y="167">~/.ssh/config</text>
<path class="w" d="M630 108V134" marker-end="url(#f1-m)"/>
<text class="l" x="640" y="126">Save &amp; Run All</text>
<path class="w w--a" d="M552 237H462" marker-end="url(#f1-ma)"/>
<text class="l" x="498" y="229" text-anchor="middle">dials out</text>
<path class="w w--a" d="M216 237H298" marker-end="url(#f1-ma)"/>
<text class="l" x="258" y="229" text-anchor="middle">ssh kaggle</text>
<path class="w w--ok" d="M596 296V322" marker-end="url(#f1-mo)"/>
<text class="l" x="588" y="314" text-anchor="end">saved</text>
<path class="w w--a" d="M664 324V298" marker-end="url(#f1-ma)"/>
<text class="l" x="672" y="314">restored</text>
<rect class="b" x="36" y="46" width="180" height="64" rx="14"/>
<text class="h" x="56" y="72">kdev</text>
<text class="s" x="56" y="91">the command line</text>
<rect class="b" x="36" y="205" width="180" height="64" rx="14"/>
<text class="h" x="56" y="231">VS Code / ssh</text>
<text class="s" x="56" y="250">Host kaggle</text>
<rect class="b b--a" x="300" y="209" width="160" height="56" rx="28"/>
<text class="h" x="380" y="233" text-anchor="middle">Cloudflare</text>
<text class="s" x="380" y="251" text-anchor="middle">edge · your hostname</text>
<text class="s" x="380" y="292" text-anchor="middle">both sides dial out:</text>
<text class="s" x="380" y="307" text-anchor="middle">no port is open on the box</text>
<rect class="b" x="536" y="48" width="188" height="60" rx="14"/>
<text class="h" x="556" y="73">Kaggle API</text>
<text class="s" x="556" y="92">save · run · quota</text>
<rect class="b b--a" x="536" y="136" width="188" height="160" rx="14"/>
<text class="h" x="556" y="162">The box</text>
<text class="s" x="556" y="180">a notebook session</text>
<rect class="cp" x="552" y="192" width="156" height="26" rx="7"/>
<text class="c" x="564" y="209">kdev's cell</text>
<rect class="cp cp--a" x="552" y="224" width="156" height="26" rx="7"/>
<text class="c" x="564" y="241">sshd · cloudflared</text>
<rect class="cp" x="552" y="256" width="156" height="26" rx="7"/>
<text class="c" x="564" y="273">/kaggle/working</text>
<rect class="b b--ok" x="536" y="324" width="188" height="56" rx="14"/>
<text class="h" x="556" y="348">Saved versions</text>
<text class="s" x="556" y="367">v12 · v13 · v14 …</text>
</svg>
</div>
<figcaption><b>The architecture.</b> kdev starts the box through the Kaggle API. The box and your <code>ssh</code> both dial out to Cloudflare's edge, which joins them by hostname, so nothing listens on a public port. When a session ends, Kaggle keeps <code>/kaggle/working</code> as a saved version, and the next start restores it.</figcaption>
</figure>

- **Your machine** runs `kdev`. It talks to the Kaggle API over HTTPS to save
  and run the notebook, read quota and stream the session log. It also writes
  a `kaggle` host into `~/.ssh/config`, so `ssh kaggle` and VS Code
  Remote-SSH work.
- **The box** is one notebook session. kdev's cell runs `sshd` and
  `cloudflared`, exports the notebook's environment (CUDA, Python, Kaggle
  variables) to every ssh session, and restores your files.
- **Cloudflare's edge** is where the two meet. Both sides dial out to it, so
  no port is open on the box.
- **Saved versions** are Kaggle's own: when a session ends, `/kaggle/working`
  is kept as that version's output.

The box reports back through its own session log: `KDEV_STAGE` lines drive
the progress board, `KDEV_READY` carries the hostname, `KDEV_ALIVE` is a
heartbeat, and `KDEV_DONE` marks the end.

## What `kdev up` does

<figure class="fig" id="fig-up">
<div class="seq-wrap" data-pagefind-ignore>
<div class="seq" aria-label="Sequence of kdev up">
<div class="seq__p" style="grid-row:1;grid-column:1">You</div>
<div class="seq__p seq__p--a" style="grid-row:1;grid-column:2">kdev</div>
<div class="seq__p" style="grid-row:1;grid-column:3">Kaggle API</div>
<div class="seq__p seq__p--a" style="grid-row:1;grid-column:4">The box</div>
<div class="seq__p" style="grid-row:1;grid-column:5">Cloudflare</div>
<div class="seq__f" style="grid-row:4/21"></div>
<div class="seq__m" style="grid-row:2;grid-column:1/3;--n:2"><b class="seq__k">1</b><span><code>kdev up</code></span></div>
<div class="seq__m" style="grid-row:3;grid-column:2/4;--n:2"><b class="seq__k">2</b><span>session status: running already?</span></div>
<div class="seq__g" style="grid-row:4"><b>if</b> a box is already running</div>
<div class="seq__m seq__m--l seq__m--d seq__m--ok" style="grid-row:5;grid-column:1/3;--n:2"><b class="seq__k">3</b><span>connect to it, no second box</span></div>
<div class="seq__g seq__g--else" style="grid-row:6"><b>else</b> nothing is running</div>
<div class="seq__m" style="grid-row:7;grid-column:2/4;--n:2"><b class="seq__k">4</b><span>pick the saved version to build on</span></div>
<div class="seq__m" style="grid-row:8;grid-column:2/4;--n:2"><b class="seq__k">5</b><span>SaveKernel: notebook + kdev's cell</span></div>
<div class="seq__m" style="grid-row:9;grid-column:3/5;--n:2"><b class="seq__k">6</b><span>Save &amp; Run All: start the container</span></div>
<div class="seq__n" style="grid-row:10;grid-column:4"><b class="seq__k">7</b> record the plan in <code>.kdev/state.json</code></div>
<div class="seq__n" style="grid-row:11;grid-column:4"><b class="seq__k">8</b> sshd · environment · git clone</div>
<div class="seq__m seq__m--a" style="grid-row:12;grid-column:4/6;--n:2"><b class="seq__k">9</b><span>open the tunnel, dialling out</span></div>
<div class="seq__m seq__m--l seq__m--d seq__m--a" style="grid-row:13;grid-column:2/5;--n:3"><b class="seq__k">10</b><span><code>KDEV_READY</code> in the session log</span></div>
<div class="seq__m" style="grid-row:14;grid-column:2/5;--n:3"><b class="seq__k">11</b><span>ssh: is this the session we started?</span></div>
<div class="seq__m" style="grid-row:15;grid-column:2/4;--n:2"><b class="seq__k">12</b><span>fresh file list + signed URLs</span></div>
<div class="seq__m" style="grid-row:16;grid-column:2/5;--n:3"><b class="seq__k">13</b><span>"restore this", over ssh</span></div>
<div class="seq__m seq__m--l" style="grid-row:17;grid-column:3/5;--n:2"><b class="seq__k">14</b><span>download every file straight from Kaggle</span></div>
<div class="seq__n" style="grid-row:18;grid-column:4"><b class="seq__k">15</b> put back symlinks, empty dirs, exec bits</div>
<div class="seq__m" style="grid-row:19;grid-column:2/5;--n:3"><b class="seq__k">16</b><span>ssh check</span></div>
<div class="seq__m seq__m--l seq__m--d seq__m--ok" style="grid-row:20;grid-column:1/3;--n:2"><b class="seq__k">17</b><span>ready: VS Code or a shell</span></div>
</div>
</div>
<figcaption><b>One <code>kdev up</code>, start to finish.</b> Solid arrows are requests, dashed ones are answers. kdev checks that the tunnel reaches the session it started before it restores anything, and checks ssh once more before it calls the box ready.</figcaption>
</figure>

1. **Is a box already running?** kdev asks the Kaggle API for the session
   status. If one is up, from any account or machine, it connects to it: no
   second session.
2. **Which saved version to build on.** It walks back from the latest
   version to the one that holds your files
   ([how it picks](#picking-the-version-to-restore)).
3. **Save & Run All.** Your notebook goes up with kdev's cell on top
   (`SaveKernel`), and Kaggle starts the container on the quota of the account
   that asked. Nothing below kdev's cell runs.
4. **The box sets itself up.** It records the plan in `.kdev/state.json`
   before anything slow, then starts `sshd`, exports the notebook environment
   and clones your repo, if you set one.
5. **The tunnel opens.** `cloudflared` dials out to Cloudflare, and the box
   reports `KDEV_READY` with its hostname through the session log.
6. **Check, then restore.** kdev verifies over ssh that the tunnel reaches the
   session it started. Then it fetches a fresh file list with signed URLs and
   tells the box to restore it. The box downloads every file straight from
   Kaggle, then puts back symlinks, empty directories and exec bits.
7. **Hand over.** A final ssh check, and kdev opens VS Code or a shell.

The rail you watch while this happens shows each step's own clock, and how
long that step usually takes on your machine, so a slow queue looks like a
queue and not a hang. This is a real run, a CPU box restoring 2,428 files:

```
  ┌  alice · CPU only · 1h
  │
  ◇  Submitting                               4s
  ◇  Waiting for a machine                    6s
  ◇  Installing SSH server                   21s
  ◇  Exporting notebook environment           0s
  ◇  Syncing workspace                        0s
  ◇  Starting Cloudflare tunnel              15s
  ⠹  Restoring your files                  1m12s  usually 1m46s
  │  1704/2428 files
  ○  Ready
  │
  └  6/8 complete  ·  2m01s
```

If several machines run `kdev up` at the same moment, they
[converge on one box](#security-model).

## Files and persistence

<figure class="fig" id="fig-files">
<div class="fig__art" data-pagefind-ignore>
<svg viewBox="0 0 760 372" role="img" aria-labelledby="f3-t">
<title id="f3-t">The box's /kaggle/working is kept by Kaggle as a saved version however the session ends, and kdev up restores it into the next box, on any account or machine. A git remote and kdev backup are optional extra copies.</title>
<defs>
<marker id="f3-ma" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah ah--a" d="M0 0L10 5L0 10z"/></marker>
<marker id="f3-mo" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah ah--ok" d="M0 0L10 5L0 10z"/></marker>
</defs>
<path class="w" d="M270 144C293 144 293 97 316 97"/>
<path class="w" d="M270 144C293 144 293 129 316 129"/>
<path class="w" d="M270 144C293 144 293 161 316 161"/>
<path class="w" d="M270 144C293 144 293 193 316 193"/>
<path class="w w--ok" d="M444 97C472 97 472 144 498 144" marker-end="url(#f3-mo)"/>
<path class="w w--ok" d="M444 129C472 129 472 144 498 144" marker-end="url(#f3-mo)"/>
<path class="w w--ok" d="M444 161C472 161 472 144 498 144" marker-end="url(#f3-mo)"/>
<path class="w w--ok" d="M444 193C472 193 472 144 498 144" marker-end="url(#f3-mo)"/>
<path class="w w--a" d="M610 224C610 300 190 306 190 252" marker-end="url(#f3-ma)"/>
<text class="l l--a" x="400" y="306" text-anchor="middle">kdev up restores it, on any account or machine</text>
<text class="s" x="400" y="322" text-anchor="middle">and puts back symlinks, empty dirs, exec bits</text>
<path class="w w--d" d="M70 248V294"/>
<text class="s" x="80" y="276">optional copies</text>
<rect class="b b--a" x="20" y="40" width="250" height="208" rx="14"/>
<text class="h" x="40" y="68">The box</text>
<text class="s" x="40" y="87">/kaggle/working</text>
<rect class="cp" x="36" y="102" width="218" height="28" rx="7"/>
<text class="c" x="48" y="120">your files</text>
<text class="r" x="244" y="120" text-anchor="end">.git, venvs, data</text>
<rect class="cp" x="36" y="136" width="218" height="28" rx="7"/>
<text class="c" x="48" y="154">.kdev/state.json</text>
<text class="r" x="244" y="154" text-anchor="end">the plan</text>
<rect class="cp" x="36" y="170" width="218" height="28" rx="7"/>
<text class="c" x="48" y="188">.kdev/meta.json</text>
<text class="r" x="244" y="188" text-anchor="end">every 30 s</text>
<rect class="cp" x="36" y="204" width="218" height="28" rx="7"/>
<text class="c" x="48" y="222">.kdev/fetched</text>
<text class="r" x="244" y="222" text-anchor="end">what arrived</text>
<text class="s" x="380" y="72" text-anchor="middle">however it ends</text>
<rect class="cp" x="316" y="84" width="128" height="26" rx="13"/>
<text class="c" x="380" y="101" text-anchor="middle">kdev down</text>
<rect class="cp" x="316" y="116" width="128" height="26" rx="13"/>
<text class="c" x="380" y="133" text-anchor="middle">Stop session</text>
<rect class="cp" x="316" y="148" width="128" height="26" rx="13"/>
<text class="c" x="380" y="165" text-anchor="middle">time runs out</text>
<rect class="cp" x="316" y="180" width="128" height="26" rx="13"/>
<text class="c" x="380" y="197" text-anchor="middle">a crash</text>
<rect class="b b--ok b--back" x="520" y="44" width="220" height="160" rx="14"/>
<rect class="b b--ok b--back" x="510" y="54" width="220" height="160" rx="14"/>
<rect class="b b--ok" x="500" y="64" width="220" height="160" rx="14"/>
<text class="h" x="520" y="92">Saved versions</text>
<text class="s" x="520" y="111">v12 · v13 · v14 …</text>
<text class="s" x="520" y="140">kept by Kaggle as output</text>
<text class="s" x="520" y="158">regular files, up to 20 GB</text>
<text class="s" x="520" y="176">owned by the notebook,</text>
<text class="s" x="520" y="194">not by an account</text>
<rect class="b b--ghost" x="20" y="296" width="200" height="60" rx="12"/>
<text class="s s--ink" x="34" y="320">git-remote · every 15 min</text>
<text class="s s--ink" x="34" y="340">kdev backup · a local copy</text>
</svg>
</div>
<figcaption><b>Files across sessions.</b> However a session ends, Kaggle keeps <code>/kaggle/working</code> as that version's output. The next <code>kdev up</code>, from any account or machine, restores it into the new box. A git remote and <code>kdev backup</code> are optional extra copies.</figcaption>
</figure>

`/kaggle/working` behaves like a disk that is always there.

- **Kaggle saves it.** When a "Save & Run All" session ends, Kaggle keeps
  `/kaggle/working` as that version's output, however the session ends. A
  clean exit, a crash and a cancel were all measured to save every file up to
  the moment the container stopped.
- **kdev puts it back.** Kaggle starts every batch session with an empty
  `/kaggle/working`; its "Persistence" setting only covers the web editor. So
  each `kdev up` works out which saved version you left off at, and the new
  box downloads it before you connect.
- **kdev fills Kaggle's gaps.** Kaggle's saved output keeps every regular
  file, but drops symlinks and empty directories and loses executable bits,
  which would break a Python virtualenv. The box records those in
  `.kdev/meta.json` every 30 seconds, and the restore puts them back.
- **Extra copies, if you want them.** With `git-remote` set, the box clones
  the repo at boot and pushes every 15 minutes and at teardown: the one copy
  that survives a failure of Kaggle's own machine. `kdev backup` copies the
  running box to this machine; `kdev restore --from-backup` pushes it back.

Every way a session can end, and what the next start gives you, is listed in
[Your files](/docs/files/).

## Picking the version to restore

<figure class="fig" id="fig-restore">
<div class="fig__art" data-pagefind-ignore>
<svg viewBox="0 0 760 320" role="img" aria-labelledby="f4-t">
<title id="f4-t">Start at the latest saved version. If it has no files, step back one version and ask again. If its restore finished, restore it alone; if not, stack it on its base layers, which fill only what it never got.</title>
<defs>
<marker id="f4-m" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah" d="M0 0L10 5L0 10z"/></marker>
<marker id="f4-mo" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah ah--ok" d="M0 0L10 5L0 10z"/></marker>
<marker id="f4-mw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah ah--warn" d="M0 0L10 5L0 10z"/></marker>
</defs>
<path class="w w--d" d="M320 64C320 22 106 22 106 76" marker-end="url(#f4-m)"/>
<text class="l" x="213" y="22" text-anchor="middle">no: a source-only save, try v(N−1)</text>
<path class="w" d="M196 110H244" marker-end="url(#f4-m)"/>
<path class="w" d="M394 110H484" marker-end="url(#f4-m)"/>
<text class="l" x="439" y="102" text-anchor="middle">yes</text>
<path class="w w--ok" d="M560 156C560 196 146 176 146 212" marker-end="url(#f4-mo)"/>
<text class="l l--ok" x="353" y="189" text-anchor="middle">yes</text>
<path class="w w--warn" d="M560 156V212" marker-end="url(#f4-mw)"/>
<text class="l l--warn" x="570" y="190">no</text>
<rect class="b" x="16" y="78" width="180" height="64" rx="14"/>
<text class="h" x="36" y="104">Latest version</text>
<text class="s" x="36" y="123">vN, the newest save</text>
<path class="b" d="M320 64L394 110L320 156L246 110Z"/>
<text class="h h--s" x="320" y="115" text-anchor="middle">Has files?</text>
<path class="b" d="M560 64L634 110L560 156L486 110Z"/>
<text class="h h--s" x="560" y="106" text-anchor="middle">Did its restore</text>
<text class="h h--s" x="560" y="123" text-anchor="middle">finish?</text>
<rect class="b b--ok" x="16" y="214" width="260" height="92" rx="14"/>
<text class="h" x="32" y="240">Restore vN alone</text>
<text class="s" x="32" y="262">restored: true in its</text>
<text class="s" x="32" y="278">.kdev/state.json, or kdev</text>
<text class="s" x="32" y="294">never touched it</text>
<rect class="b b--warn" x="316" y="214" width="428" height="92" rx="14"/>
<text class="h" x="332" y="240">Stack it on its base</text>
<text class="s" x="332" y="262">cut short, or worked in before</text>
<text class="s" x="332" y="278">the restore: base layers fill</text>
<text class="s" x="332" y="294">only what vN never got</text>
<rect class="lyr lyr--top" x="576" y="226" width="152" height="20" rx="5"/>
<text class="s s--ink" x="586" y="240">vN · on top</text>
<rect class="lyr" x="576" y="250" width="152" height="20" rx="5"/>
<text class="s" x="586" y="264">v(N−1) · fills gaps</text>
<rect class="lyr" x="576" y="274" width="152" height="20" rx="5"/>
<text class="s" x="586" y="288">v(N−2) · fills gaps</text>
</svg>
</div>
<figcaption><b>How <code>kdev up</code> picks what to restore.</b> It walks back past versions with no files. A version whose restore finished is restored alone; one that was cut short is stacked on the versions it was meant to be built on, so nothing done in it is lost.</figcaption>
</figure>

A version with no files is a source-only save (group setup, a Quick Save), so
kdev walks past it. A session that was stopped before its restore finished,
or that you worked in before restoring, is not skipped: it gets stacked on the
versions it was meant to be built on. The layers underneath fill in only what
that session's restore never delivered (the box logs each file as it lands, in
`.kdev/fetched`), so a file you deleted there stays deleted.

A restore never overwrites a file written in the current session, so it is
safe to run again at any time:

```bash
kdev restore               # finish or repeat the restore on the running box
kdev restore --from v12    # add files v12 has that the box doesn't (not a rollback)
kdev workspace files       # what `kdev up` would restore (--version v12 for another)
```

## Several accounts, several machines

<figure class="fig" id="fig-accounts">
<div class="fig__art" data-pagefind-ignore>
<svg viewBox="0 0 760 300" role="img" aria-labelledby="f5-t">
<title id="f5-t">Every account in the Kaggle group can start the one shared notebook on its own weekly quota. Every machine reaches the same box with kdev up and ssh; its key is saved in the workspace.</title>
<defs>
<marker id="f5-m" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah" d="M0 0L10 5L0 10z"/></marker>
<marker id="f5-ma" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah ah--a" d="M0 0L10 5L0 10z"/></marker>
</defs>
<rect class="z" x="16" y="14" width="196" height="272" rx="16"/>
<text class="zl" x="34" y="36">Machines</text>
<rect class="z" x="548" y="14" width="196" height="272" rx="16"/>
<text class="zl" x="566" y="36">Kaggle group</text>
<path class="w w--a" d="M196 84C240 84 240 232 284 232" marker-end="url(#f5-ma)"/>
<path class="w w--a" d="M196 152C240 152 240 232 284 232" marker-end="url(#f5-ma)"/>
<path class="w w--a w--d" d="M196 220C240 220 240 232 284 232" marker-end="url(#f5-ma)"/>
<path class="w" d="M564 84C520 84 520 83 476 83" marker-end="url(#f5-m)"/>
<path class="w" d="M564 152C520 152 520 83 476 83" marker-end="url(#f5-m)"/>
<path class="w" d="M564 220C520 220 520 83 476 83" marker-end="url(#f5-m)"/>
<path class="w" d="M380 126V176" marker-end="url(#f5-m)"/>
<text class="l" x="390" y="156">Save &amp; Run All</text>
<rect class="b" x="32" y="56" width="164" height="56" rx="12"/>
<text class="h" x="48" y="80">laptop 1</text>
<text class="s" x="48" y="99">kdev up · ssh</text>
<rect class="b" x="32" y="124" width="164" height="56" rx="12"/>
<text class="h" x="48" y="148">laptop 2</text>
<text class="s" x="48" y="167">kdev up · ssh</text>
<rect class="b b--ghost" x="32" y="192" width="164" height="56" rx="12"/>
<text class="h" x="48" y="216">a new laptop</text>
<text class="s" x="48" y="235">kdev setup joins</text>
<rect class="b" x="286" y="40" width="188" height="86" rx="14"/>
<text class="h" x="306" y="66">Shared notebook</text>
<text class="s" x="306" y="85">the group has Can Edit</text>
<text class="s" x="306" y="104">+ its saved versions</text>
<rect class="b b--a" x="286" y="178" width="188" height="108" rx="14"/>
<text class="h" x="306" y="204">The box</text>
<text class="s" x="306" y="223">one at a time</text>
<rect class="cp" x="298" y="238" width="164" height="26" rx="7"/>
<text class="c" x="308" y="255">.kdev/authorized_keys</text>
<rect class="b" x="564" y="56" width="164" height="56" rx="12"/>
<text class="h" x="580" y="80">account A</text>
<text class="s" x="580" y="96">30 GPU-hours a week</text>
<rect class="bar" x="580" y="102" width="132" height="4" rx="2"/>
<rect class="bar__f bar__f--warn" x="580" y="102" width="22" height="4" rx="2"/>
<rect class="b" x="564" y="124" width="164" height="56" rx="12"/>
<text class="h" x="580" y="148">account B</text>
<text class="s" x="580" y="164">30 GPU-hours a week</text>
<rect class="bar" x="580" y="170" width="132" height="4" rx="2"/>
<rect class="bar__f" x="580" y="170" width="92" height="4" rx="2"/>
<rect class="b" x="564" y="192" width="164" height="56" rx="12"/>
<text class="h" x="580" y="216">account C</text>
<text class="s" x="580" y="232">30 GPU-hours a week</text>
<rect class="bar" x="580" y="238" width="132" height="4" rx="2"/>
<rect class="bar__f" x="580" y="238" width="132" height="4" rx="2"/>
</svg>
</div>
<figcaption><b>One box, many accounts and machines.</b> Every account in the group can start the shared notebook, on its own weekly quota. Every machine that has used the workspace has its key saved there, so it can reach every later box.</figcaption>
</figure>

- **Quota is per account.** A session is charged to the account that starts
  it, not to the notebook's owner (measured).
- **Switching is a question, not a wall.** If the active account can't finish
  the session you asked for, `kdev up` shows every account's remaining hours
  and lets you pick. `kdev account use <name>` switches by hand.
- **The box is still running?** `kdev up` from any account or machine
  connects to it instead of starting a second one.
- **A new laptop.** `kdev setup` signs you in and joins the workspace. The
  tunnel and git settings come from the notebook, and the new laptop's SSH key
  is saved in the workspace (`.kdev/authorized_keys`).
- **Stopping a box someone else started.** Kaggle only lets the account that
  started a session cancel it. When the box doesn't answer, `kdev down` finds
  out which account that was and cancels as it, or tells you which account
  has to.

More in [Several accounts, several machines](/docs/accounts/).

## Security model

<figure class="fig" id="fig-security">
<div class="fig__art" data-pagefind-ignore>
<svg viewBox="0 0 760 324" role="img" aria-labelledby="f6-t">
<title id="f6-t">Your SSH key and Kaggle tokens stay on your machine. Cloudflare routes ssh to your hostname, where only a box holding the workspace's tunnel credentials can answer. Those credentials sit in the private notebook, readable by anyone with Can Edit. The box accepts public keys only.</title>
<defs>
<marker id="f6-m" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah" d="M0 0L10 5L0 10z"/></marker>
<marker id="f6-ma" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah ah--a" d="M0 0L10 5L0 10z"/></marker>
<marker id="f6-mw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path class="ah ah--warn" d="M0 0L10 5L0 10z"/></marker>
</defs>
<rect class="z" x="16" y="14" width="220" height="298" rx="16"/>
<text class="zl" x="34" y="36">Your machine</text>
<rect class="z" x="524" y="14" width="220" height="298" rx="16"/>
<text class="zl" x="542" y="36">Kaggle</text>
<path class="w w--a" d="M222 230H304" marker-end="url(#f6-ma)"/>
<text class="l" x="264" y="222" text-anchor="middle">your key</text>
<path class="w w--a" d="M538 230H456" marker-end="url(#f6-ma)"/>
<text class="l" x="497" y="222" text-anchor="middle">dials out</text>
<path class="w w--warn w--d" d="M546 99C420 99 380 140 380 200" marker-end="url(#f6-mw)"/>
<text class="l l--warn" x="440" y="122" text-anchor="middle">answers on your hostname</text>
<path class="w" d="M634 148V176" marker-end="url(#f6-m)"/>
<text class="l" x="644" y="167">builds</text>
<rect class="cp" x="30" y="58" width="192" height="28" rx="7"/>
<text class="c" x="42" y="76">SSH private key</text>
<text class="r" x="212" y="76" text-anchor="end">stays here</text>
<rect class="cp" x="30" y="94" width="192" height="28" rx="7"/>
<text class="c" x="42" y="112">config.json</text>
<text class="r" x="212" y="112" text-anchor="end">mode 0600</text>
<rect class="cp" x="30" y="130" width="192" height="28" rx="7"/>
<text class="c" x="42" y="148">Kaggle tokens</text>
<text class="r" x="212" y="148" text-anchor="end">per account</text>
<rect class="b" x="30" y="204" width="192" height="52" rx="12"/>
<text class="h h--s" x="46" y="227">ssh · VS Code</text>
<text class="s" x="46" y="244">public-key auth only</text>
<rect class="b b--a" x="306" y="202" width="148" height="56" rx="28"/>
<text class="h" x="380" y="226" text-anchor="middle">Cloudflare</text>
<text class="s" x="380" y="244" text-anchor="middle">routes by hostname</text>
<text class="s" x="380" y="286" text-anchor="middle">no open port on the box</text>
<rect class="b b--warn" x="538" y="50" width="192" height="98" rx="14"/>
<text class="h" x="554" y="74">Private notebook</text>
<rect class="cp" x="546" y="86" width="176" height="26" rx="7"/>
<text class="c" x="556" y="103">tunnel creds</text>
<text class="r" x="712" y="103" text-anchor="end">Can Edit sees</text>
<rect class="cp" x="546" y="116" width="176" height="26" rx="7"/>
<text class="c" x="556" y="133">deploy key</text>
<text class="r" x="712" y="133" text-anchor="end">if you set one</text>
<rect class="b b--a" x="538" y="178" width="192" height="122" rx="14"/>
<text class="h" x="554" y="202">The box</text>
<rect class="cp" x="546" y="212" width="176" height="24" rx="7"/>
<text class="c" x="556" y="228">sshd</text>
<text class="r" x="712" y="228" text-anchor="end">keys only, no password</text>
<rect class="cp" x="546" y="240" width="176" height="24" rx="7"/>
<text class="c" x="556" y="256">authorized_keys</text>
<text class="r" x="712" y="256" text-anchor="end">revoke</text>
<rect class="cp" x="546" y="268" width="176" height="24" rx="7"/>
<text class="c" x="556" y="284">host key</text>
<text class="r" x="712" y="284" text-anchor="end">new every session</text>
</svg>
</div>
<figcaption><b>Who can reach the box.</b> Your SSH private key stays on your machine, and the box accepts public keys only. Only a box holding the workspace's tunnel credentials can answer on your hostname, and those credentials sit in the private notebook, where anyone with Can Edit can read them.</figcaption>
</figure>

- **Access** is public-key SSH only; password login is disabled. The box
  accepts every key saved in the workspace's `.kdev/authorized_keys`: remove a
  line there to revoke a machine.
- **Secrets in the notebook.** Kaggle has no API for notebook Secrets, so the
  tunnel credentials (and a git deploy key, if you set one) sit in the private
  notebook's source. Anyone with Can Edit can read them. Scope a deploy key to
  one repository.
- **Host keys.** Every session is a new container with a new host key, so the
  `kaggle` block in `~/.ssh/config` doesn't pin one; a pinned key would break
  VS Code on every other machine. Only a box holding the workspace's tunnel
  credentials can answer on that hostname, and anyone holding those can
  already edit the notebook that builds the box.
- **Simultaneous starts converge on one box.** If several machines run
  `kdev up` together, the lowest live notebook version wins. Each losing
  client stops its own run as the account that started it, waits for the
  cancellation to finish, then connects to the winner. Startup verifies the
  SSH session before restoring files, so cancelled contenders never replace
  the winner's saved workspace; their output stays available through
  `kdev restore --from vN`.
- **On your machine,** settings live in `~/.config/kdev/config.json`
  (mode 0600), and `kdev config` never shows secrets.

Limits, Kaggle's terms and the rest are in
[Security and limits](/docs/security/).
