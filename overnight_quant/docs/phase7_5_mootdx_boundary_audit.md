# Mootdx 600000 Boundary Audit

This audit is independent from the five-stock qualification probe. It observes
only `600000` at `14:49:57`, `14:50:01`, and `14:50:08` using the same fixed
mootdx endpoint as the main probe.

Each target launches one killable, single-page transaction worker with a strict
2000 ms deadline. The audit worker does not wait for the later full attribution
window. There are no retries and missed targets are not replayed. Each target
writes a separate, exclusive UTF-8 JSON file under the ignored cache directory.
Existing files are never replaced.

The audit records first observation time, source position, minute label, price,
raw lot volume, normalized share volume (`lot -> share x100`), trade count,
packet hash, and source response hashes. Its hash namespace is audit-only and is
never included in the minute probe evidence, qualification ledger, scoring,
candidates, tickets, or orders.

Run a configuration check before scheduling:

```powershell
D:\A-stock\.venv\Scripts\python.exe overnight_quant\scripts\run_mootdx_boundary_audit.py `
  --date YYYY-MM-DD `
  --endpoint HOST:7709 `
  --validate-only
```

One ambiguous observation cannot change attribution rules. At least three
independent boundary cases and a separate versioned design review are required
before proposing any algorithm change.
