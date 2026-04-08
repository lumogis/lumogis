# Maintainer notes: public repository

## Canonical public remote

The **public** open-source tree is intended to live at:

**https://github.com/lumogis/lumogis**

Forks may carry extra branches or experiments. **Only push commits that are meant for that public tree.**

## Before you push to the public remote

1. **Ignored paths can still be tracked**  
   If something was committed before it was added to `.gitignore`, it keeps shipping with every push. Before you push the branch that tracks the public default branch, review what is in the commit:

   ```bash
   git ls-tree -r --name-only HEAD
   ```

   Drop anything from the index that belongs only on a maintainer machine (draft notes, editor artefacts, credentials, unreleased design docs). Use `git rm --cached <path>` (and commit) after the team agrees — or reset the branch to match public history.

2. **Secrets**  
   Never `git add -f` secret-bearing files. When in doubt, run `git diff --cached` and read every path before pushing.

## Reviews

PR expectations and review targets are summarised under **Governance** in [CONTRIBUTING.md](../CONTRIBUTING.md#governance).
