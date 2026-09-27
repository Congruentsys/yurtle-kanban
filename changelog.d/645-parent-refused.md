<!-- section: Changed -->
- With `--push`, a parent that is not on `origin/<default>` (for example, one that exists only locally) is refused: the create names the parent, says it isn't on `origin/<default>`, and creates and pushes nothing. Without `--push` the parent is still updated as a local change (#645).
