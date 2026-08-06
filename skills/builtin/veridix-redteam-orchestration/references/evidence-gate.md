# Evidence Gate Checklist

Every materialized finding must pass each gate before it is reported.

1. Scope: the target is inside the approved asset and authorization scope.
2. Reproducibility: the exact request or command can be replayed.
3. Verification: the oracle or manual replay confirms the behavior.
4. Impact: the finding has a concrete security consequence, not only a
   scanner string.
5. Evidence: request, response, timestamp and tool output are attached.
6. Severity: the rating matches exploitability and real-world impact.

If any gate fails, keep the candidate in the working set instead of the final
finding list.
