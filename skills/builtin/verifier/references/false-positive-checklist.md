# False Positive Checklist

Before accepting a candidate as true positive, answer these questions:

- Can the claim be restated precisely?
- Does the replay produce the same result?
- Is the condition caused by default content, cache, WAF or a generic
  template regex?
- Is the version evidence real or inferred?
- Does a validation or authorization control block the path in practice?
- Would a senior tester accept the evidence?

Only a reproducible, scoped and impact-bearing result is a true positive.
