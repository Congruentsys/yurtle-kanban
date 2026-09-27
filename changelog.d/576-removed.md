<!-- section: Removed -->
- `WorkItem.to_yurtle()` (never called; frontmatter is the only source of truth) and `WorkItem.blocks` (never parsed; "X blocks Y" is "Y depends_on X"), with the `blocks` key of `to_dict()` and the `kb:blocks` triple `query` derived from it (#576).
