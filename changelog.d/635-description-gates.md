<!-- section: Changed -->
- Workflow rules on `len(item.description)` count only the body: comments are their own field since #605, so an item with a short body and long comments can now fail such a gate (#635).
