# Chart-vendored egress artefacts

Files Helm needs at template time (chart `.Files.Get` cannot read outside the
chart directory). Canonical sources live next door under
`deploy/kubernetes/egress-bundle/`.

Re-sync after editing the canonical copies:

```bash
deploy/kubernetes/scripts/sync-egress-files.sh
```
