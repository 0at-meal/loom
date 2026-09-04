---
name: eli12
description: Turns an uploaded document (PDF, Word/.docx, .txt, or .md) into a first-principles explainer report written in Markdown. Use this whenever the user uploads a file and asks to have it "explained like I'm 12," "broken down from scratch," "explained simply," wants to "build intuition" for a paper/document, or asks for a plain-language / first-principles walkthrough of something they uploaded. Also trigger on phrases like "explain this paper to me," "help me actually understand this document," or "dumb this down but don't lose the substance." The output is a self-contained Markdown report that follows the input document's overall flow, builds every concept up from fundamentals, explains jargon the moment it appears, and ends with a glossary. Do not use this for simple one-line lookups or when the user just wants a short summary — this skill is for a full explanatory rewrite of a document.
---

# ELI12: First-Principles Explainer

Turns a dense or jargon-heavy document into a report that builds genuine intuition from the ground up — the way you'd explain it to a sharp, curious 12-year-old who has some general context but no specialist background in this particular topic.

## What "ELI12" actually means here

This is **not** "make it babyish" or "oversimplify until it's wrong." The target reader:
- Is smart and curious, and can handle multi-step reasoning.
- Does **not** have specialist background in *this specific* topic (e.g., no assumed ML research knowledge, no assumed finance jargon, no assumed legal terms) — but may well be generally educated.
- Wants to understand *why* things are true and *how* ideas connect, not just be told the conclusion.
- Should come away able to explain the core ideas back to someone else, in their own words.

So: no unexplained jargon, no "as is well known," no skipped reasoning steps. But also no condescension, no baby-talk, and no dropping of substance. Rigor and simplicity are both required — simplify the *language and scaffolding*, not the *content*.

## Process

### Step 1: Get the input file's content

Check `/mnt/user-data/uploads` for the file. Depending on type, route to the right reading approach:
- **PDF** → consult `/mnt/skills/public/pdf-reading/SKILL.md` for extraction strategy (text vs. scanned, tables, figures).
- **.docx** → consult `/mnt/skills/public/docx/SKILL.md` for reading approach.
- **.txt / .md** → read directly.

If the file type isn't one of these (e.g. .xlsx, images, code), tell the user this skill is built for text-heavy documents and confirm how they'd like to proceed before continuing.

Read the *entire* document before writing anything. Note figures, tables, and equations even if you can't reproduce them exactly — describe what they show in your own words when relevant.

### Step 2: Map the document's flow

Identify the input's overall structure (its sections/arguments in order). You don't need to mirror headings 1:1 — **loosely follow the same overall flow**, and feel free to:
- Merge two sections if splitting them would fragment one idea.
- Reorder slightly if the source builds knowledge in a confusing sequence (e.g., defines a term after using it).
- Add a short "big picture" framing at the very top (see Step 3) even if the source doesn't have one.

Jot down (mentally or in scratch notes) the chain of ideas: what has to be understood first for the next part to make sense? This dependency order is what your report should follow, even more than the source's literal section order.

### Step 3: Write the report

Structure:

1. **Big picture (2-4 sentences).** Before any details: what is this document about, and why would someone care? Answer "what problem is this solving" before "how does it solve it."
2. **Body sections, following the mapped flow from Step 2.** For each section:
   - Open with the core idea in one plain sentence before elaborating.
   - Build up from a concrete example, analogy, or scenario *before* stating the general/abstract version — ground-up, not top-down.
   - Every time a piece of jargon, acronym, or technical term first appears, explain it immediately inline (a short parenthetical or one clause is usually enough) — don't just define it in the glossary and move on.
   - Explain *why* each step or claim follows from the last. Don't skip the reasoning even if the source document does.
   - Use short sentences and everyday words. If a sentence needs a semicolon or three subordinate clauses, split it.
   - Where the source has a figure/table/equation, describe in words what it shows and why it matters, rather than reproducing it verbatim.
3. **"Why it matters" or "so what" close (optional but encouraged if the source supports it).** A few sentences connecting the ideas back to real-world consequence or the bigger picture from step 1.
4. **Glossary.** Every jargon term used in the report, alphabetized, each with a one-to-two sentence plain-language definition. This is a safety net for re-reading, not a replacement for inline explanation.

Formatting:
- Markdown output: `#`/`##` headings, short paragraphs, bullet lists where they aid scanning, **bold** for first-use jargon terms (matches glossary entries).
- Medium depth by default: thorough enough that no real idea from the source is skipped, but tight enough to stay digestible — prefer clarity over exhaustiveness. Don't pad; don't compress so much that the "why" disappears.
- Keep the report self-contained: someone should be able to read only this report (never the original) and come away actually understanding the material.

### Step 4: Save and deliver

Save the report as a `.md` file in `/mnt/user-data/outputs/` (filename based on the source document's title/topic, e.g. `topic-name-explained.md`). Use `present_files` to share it. Keep any chat message brief — the report speaks for itself.

## Quality self-check before delivering

Before presenting the report, verify:
- [ ] Every technical term is explained the moment it's first used, not just in the glossary.
- [ ] Every section leads with a concrete example/analogy before the abstract statement.
- [ ] No step in the reasoning is skipped — a reader with no background in *this* topic could follow the logic chain.
- [ ] The glossary contains every jargon term used, alphabetized.
- [ ] Nothing important from the source was dropped in the name of simplicity — content is preserved, only the scaffolding is simplified.
- [ ] The report could stand alone without the original document.
