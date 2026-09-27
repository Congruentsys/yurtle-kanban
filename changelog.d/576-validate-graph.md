<!-- section: Changed -->
- `validate` also reports dependency cycles and `depends_on` targets that are on no board, across every board, and now reports an ID duplicated across boards; the scan used to keep one of the two items silently, so that check never fired (#576).
