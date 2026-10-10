# Principles

## No invented frontmatter

apm-lite writes only the fields that Claude Code and Antigravity document, and the field names upstream APM already uses in `apm.yml` and `apm.lock.yaml`. If a source file carries something else, apm-lite drops it. It never makes up a new key.

## Portability

A package is an ordinary Git repo, and the manifest and lockfile stay readable by upstream APM. Moving a project to upstream APM, or a package to another team, needs no rewrite. The same inputs give the same bytes on every machine.

## Reliability over magic

apm-lite rejects input it would have to guess at, such as `owner/repo` shorthand or a ref that looks like a semver range, and says which forms it supports. It pins commits, checks content hashes, keeps files you edited, and undoes everything when a command fails. The only network access is `git`.
