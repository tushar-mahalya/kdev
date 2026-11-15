---
title: "Everyday use"
description: "Start, check and stop the box; pick the GPU and session length once."
group: Start
order: 2
---

```bash
kdev                  # overview: the box, every account's quota, what to do next
kdev up               # start the box, or connect to the one already running
kdev status           # running? who started it? when does it end? files back?
kdev down             # stop it now
```

Bare `kdev` in a terminal ends with a menu of the actions that make sense
right now: connect or stop when the box is up; start it, or switch account,
when it isn't.

The box you start is a setting — pick once, and `kdev up` stops asking:

```bash
kdev config set gpu t4
kdev config set hours 9
```

**Leaving needs no ceremony.** Close the laptop, lose the wifi, walk away.
The box runs to the end of its session and saves as it stops. `kdev down` is
for stopping *early*, to hand the quota back.
