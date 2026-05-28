# Who This Is For

## What the tool gives you

A timestamped, reproducible record that says: *on this date, we ran these adversarial probes against this model endpoint, and the results were these pass rates.* 

---

## The practical workflow

**1. Pre-deployment model selection**

Before going live, run Glokta against the candidate model(s). If you're choosing between two providers, the comparative leaderboard gives you a documented basis for your decision: "we tested Model A and Model B on prompt injection, jailbreak, and data leakage probes; Model B scored higher on the risk categories relevant to our use case."

Relevant to: Article 9 risk management, NIS2 supply chain security, general standard of care.

**2. Establish a minimum acceptable threshold**

Decide upfront what scores your organisation requires before deploying a model. Document this policy — even a one-page internal document: "we require >90% pass rate on `promptinject` and `leakreplay` probes before production deployment." A failed threshold blocks deployment; a passed threshold is evidence of a controlled process.

**3. Run on a schedule after deployment**

Model providers update models without notice. A model you tested last quarter may behave differently today. Scheduled Glokta scans detect regression automatically. If a score drops below your threshold, you have an alert and a record — both of which matter if an incident occurs later.

Relevant to: Article 9(2)(b) post-market monitoring, NIS2 incident preparedness.

**4. Export and store the evidence package**

Export the JSON response from `/api/leaderboard/{model_id}` after each scan, timestamp it, and store it alongside your technical documentation. This is your adversarial testing evidence — it names the probes, the model, the date, and the results.

---

## Regulatory coverage

*draft*

| Requirement                                                              | Covered | How                                                                                                       |
| ------------------------------------------------------------------------ | ------- | --------------------------------------------------------------------------------------------------------- |
| Pre-deployment due diligence — deployer obligations (Art 26, Recital 79) | Yes     | Scan results demonstrate the deployer assessed the model before use; methodology is open and verifiable   |
| Risk management system — high-risk AI deployments only (Art 9)           | Partial | Identifies probe-level failures as risk management inputs; applies to deployers of Annex III systems only |
| Supply chain security due diligence (NIS2)                               | Yes     | Pre-deployment testing of third-party model endpoints                                                     |
| Post-market monitoring (Art 9(2)(b))                                     | Partial | Scheduled scans                                                                                           |
| Bias and fairness testing                                                | No      | garak doesn't cover this well; needs a separate tool                                                      |
| Full Annex IV / XI technical documentation                               | No      | Tool is one input; documentation requires human authoring around it                                       |

---

## The pitch

> You need to show you took your model selection and security posture seriously. Glokta gives you a reproducible, documented adversarial testing record built on an NVIDIA-backed open-source scanner — the kind of thing a notified body or data protection officer can inspect and verify. It won't write your technical documentation for you, but it produces the testing evidence that sits inside it. Run it before you deploy, run it quarterly, store the outputs. That's a defensible process at a cost of almost nothing.
