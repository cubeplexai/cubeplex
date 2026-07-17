# Egress addon — canonical source

`inject.py` here is the **canonical** mitmproxy addon. The chart embeds it
into the `egress-inject-addon` ConfigMap at render time via `.Files.Get`,
which is restricted to files under the chart directory.

`deploy/kubernetes/egress-bundle/addon/inject.py` is a symlink to this
file, so the rest of the egress-bundle layout (pytest tests, ad-hoc
edits, the historical `kubectl apply -f k8s/` flow) keeps working
without a sync step.

Edit this file directly — the symlink will follow.
