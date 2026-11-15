---
title: "Everyday use"
description: "Start, check and stop the box; pick the GPU and session length once."
group: Start
order: 2
---

```bash
kdev                  # the home screen: the box and quota, live; one key per action
kdev up               # start the box, or connect to the one already running
kdev status           # running? who started it? when does it end? files back?
kdev down             # stop it now
```

Bare `kdev` in a terminal is the home screen. It is usable at once: the box
and every account's quota fill in while you look, a running box counts down
in place, and the active account's bar shows what the next box will use.
Every action has a key — `1` starts or opens the box, `s` status, `a`
accounts, `d` doctor, `q` quits — and arrows, `j`/`k` and Enter work too. The
highlighted item shows the exact command it runs (`$ kdev up`), so anything
you do from the menu you can also type or script. Read-only picks come back
to the menu; `up`, `ssh` and VS Code hand you the box, and `down` ends it.

In a short terminal, such as an editor's panel, the logo gives way and the
secondary actions fold onto one line of keys.

Without a terminal (`kdev | cat`, CI), the same facts print as plain lines,
tables become tab-separated so `cut -f2` works, and advice (hints, next
steps) goes to stderr, so stdout carries only data.

The box you start is a setting — pick once, and `kdev up` stops asking:

```bash
kdev config set gpu t4
kdev config set hours 9
```

**Leaving needs no ceremony.** Close the laptop, lose the wifi, walk away.
The box runs to the end of its session and saves as it stops. `kdev down` is
for stopping *early*, to hand the quota back.
