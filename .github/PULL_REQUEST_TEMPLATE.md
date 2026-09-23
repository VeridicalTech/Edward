## Summary

<!-- What does this PR do? One or two sentences. -->

## Design decision

<!-- If this PR settles a design question (naming, contract, behavior),
     state the decision and the alternative you rejected. Delete this
     section if there is none. -->

## Checklist

- [ ] `python -m unittest test_product test_receipts test_stepshield` green — tests are offline-only, no network or scorer required
- [ ] Docs updated if a behavior or contract changed (README / BENCHMARK.md / docstrings)
- [ ] No secrets, tokens, or internal hostnames committed
- [ ] New behavior has a test that would fail without it
