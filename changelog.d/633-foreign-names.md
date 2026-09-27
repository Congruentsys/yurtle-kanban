<!-- section: Changed -->
- Another theme's status names are no longer read on a board whose theme doesn't define
  them: a software item's `status: arrived` (nautical) or `status: active` (hdd) now reads
  like any unknown status, i.e. backlog. So do `planning` and `completed`, which no theme
  defines. The hard-coded cross-theme tables are gone (#633).
