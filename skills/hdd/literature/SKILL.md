---
name: literature
description: LLM-assisted literature review for HDD (Phase 0)
disable-model-invocation: true
allowed-tools: WebSearch, WebFetch, Write, Read, Glob
argument-hint: "<idea-id or topic> \"search query\""
---

# Literature Review

Conduct an LLM-assisted literature review as part of HDD Phase 0: Discovery.

## Overview

Literature reviews discover existing frameworks, prior work, and standards that
inform hypothesis formulation. HDD uses LLM assistance to rapidly survey a field.

## Steps

### 1. Identify the Topic

If starting from an idea:
```bash
cat research/ideas/IDEA-R-XXX.md
```

### 2. Search for Prior Work

Use web search to find relevant papers, standards, and frameworks:

- Academic papers (Google Scholar, arXiv)
- Industry standards (ISO, IEEE, RFCs)
- Existing frameworks
- Benchmarks and evaluation methods
- Similar projects or implementations

### 3. Create Literature File

Create `research/literature/LIT-{N}.md`:

```markdown
---
id: LIT-{N}
type: literature
topic: "$TOPIC"
status: active
created: YYYY-MM-DD
source-idea: IDEA-R-XXX
---

# LIT-{N}: Literature Review - $TOPIC

## Research Question

[The question being explored]

## Key Findings

### 1. [Framework/Standard/Paper Name]

**Source:** [URL or citation]
**Relevance:** [Why this matters]
**Key Concepts:**
- [Concept 1]
- [Concept 2]

### 2. [Next Framework]

...

## Prior Art Summary

| Prior Work | Contribution | Gap / What We Add |
|------------|--------------|-------------------|
| [Name] | [What they did] | [What's missing] |

## Implications for Hypothesis

Based on this review:

1. **Potential Hypothesis 1:** [Testable claim]
2. **Potential Hypothesis 2:** [Another possibility]

## References

1. [Full citation 1]
2. [Full citation 2]

## Next Steps

- [ ] Formalize hypothesis: `/hypothesis "claim"` (add `--paper <n>` only if a paper exists)
```

### 4. LLM-Assisted Research

Good prompts:
- "What are the standard frameworks for measuring [X]?"
- "How do researchers typically evaluate [Y]?"
- "What prior work exists on [Z]?"
- "What are the gaps in current approaches to [W]?"

**Document findings** as you go.

### 5. Synthesize

After researching:
1. Update the LIT file with findings
2. Link back to source idea
3. Propose potential hypotheses
4. Suggest which paper this might contribute to

## Output

Complete literature file with:
- Key findings summarized
- Prior art table filled
- At least one potential hypothesis proposed
- Clear next steps
