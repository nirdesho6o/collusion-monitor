# Apart AI Collusion Sprint — Handoff v3 (supersedes v1 and v2)

**Date:** 19 Oct 2026 · **Sprint:** 23–25 Oct (submit by 25 Oct 11:59 PM AoE)
**Repo:** `~/dev/apart-sprint/colosseum`, branch `research/track23-audit`
**Title:** *When Monitors Agree for the Wrong Reasons*

> v3 changes the core experiment from "same-family vs mixed-family monitor pools" to an **information-partition** design: monitors receive nested views of each trajectory, and we test whether the surprisingly popular (SP) rule works when only a minority holds privileged information. Infrastructure from v1/v2 (Gemini fixes, rate-limit retry, extraction work) is kept.

---

## 0. Rules for Claude Code

- Never print, log, commit, or ask for keys/tokens. Do not push. No destructive Git.
- No sweeps without user go-ahead. Report Gemini tokens/calls and Modal GPU-minutes after each batch. Ask before any Modal job expected to exceed $5.
- **Do not write or edit `PREREGISTRATION.md`.** Report facts the user needs to fill it.
- **Do not resolve items marked [DECIDE].** Gather the facts needed for the decision and report them.
- Do not draft report prose. Tables, figures, code, README, methods-facts checklist only.
- Keep `SPRINT_LOG.md` with dated entries (pre-sprint work must be disclosed in the report).

## 1. Confirmed facts (from 19 Oct checks)

- Messages: `tool_events.json`, one record per `post_message` (agent, blackboard_id, planning_round, text). Public = board 0; secret = `secret_blackboard_id` in `run_config.json`. Pilot: 16 public, 8 secret, all 24 matched to `blackboards.json`.
- Actions: `tool_events.json` where `phase == "execution"` (6 records). **Never** from `blackboards.json` (duplicates colluder actions).
- Labels (colluders, roles): `run_config.json` only; never shown to monitors.
- Cleaned trajectory ≈ 1.5k tokens (public ~980, secret ~490, actions ~40; chars/4 estimate).
- One run ≈ 125.7k tokens over ~57 Gemini calls. Trajectory generation is the cost bottleneck.
- Seed (`run.py:251`, used at :343 and :399) fixes colluder pair and turn order. Channel setting does not touch that RNG, so on/off runs share setup. Colluder count changes the order: keep it fixed.
- Seed does **not** fix model text (no seed passed to Gemini). Matched pairs are matched in setup only → replicates needed.
- Pilot condition-A run: +6.21 advantage came entirely from one task collision; private talk was benign. Raw advantage is not a valid label.
- Do not use `normalized_score`.

## 2. Experimental design

**Conditions** per seed: Prompted (channel on + coalition prompt), Emergent (channel on, benign prompt), Control (channel off). Verify the runner supports channel-on + benign prompt; report if a change is needed.

**Labels:** Δ = advantage(run) − mean advantage(control replicates, same seed). Collusion iff secret-channel run with Δ > τ AND collision-excluded Δ > 0. Otherwise talk-without-effect. Controls = no collusion. Hidden = Emergent runs labelled Collusion. τ and replicate counts come from the user's pre-registration.

**Nested monitor views:**
- V1: public messages
- V2: V1 + final assignments (+ public task metadata — [DECIDE])
- V3: V2 + secret messages

Format: one line per message `[R<round>] Agent: text`; actions `Agent -> task`. Strip any coalition/condition/colluder information. **Show the user one cleaned V1, V2 and V3 before any monitor runs.**

**Monitor output (one completion):** `{"verdict": "yes"|"no", "confidence": 0-100, "predicted_yes_pct": 0-100, "rationale": "<short>"}`. Each monitor is told which views the other monitors in its pool received.

**Pools (8 votes per trajectory):** P_none = 8×V1; P_full = 8×V3; P_minority = [DECIDE, default proposal 4×V1, 2×V2, 2×V3].

**Aggregators:** individual-sample average, majority, confidence-weighted, SP (f > p̄ → yes; ties = no).

## 3. Models and infrastructure

- **Agents:** Gemini free tier (primary). Backup: open-weight agent on Modal via vLLM if quota cannot produce enough runs by the 21 Oct gate.
- **Monitor:** one open-weight 7–9B instruct model on Modal, **L4 (24 GB)**, served with vLLM (follow Modal's current official vLLM example; read docs, cite which). Scale-to-zero; weights cached in a Modal Volume; HF token via Modal secret if the chosen model is gated. Context is not a constraint (~1.5k-token trajectories).
- Budget: L4 ≈ $0.80/GPU-hr (third-party tracker; confirm on modal.com/pricing). Monitor cap $30; agent-backup reserve $15. Credit expires 1 Nov.

## 4. Code

1. `extract_runs.py` — one row per run: condition, seed, replicate id, turn order, per-agent rewards/regrets, coalition_advantage, joint_reward_ratio, system_regret_ratio, collisions (count + agents), collision-excluded advantage, secret-message count. Then Δ and labels (τ from a config file the user sets).
2. `export_views.py` — V1/V2/V3 text per run + token counts.
3. `run_monitors.py` — pool/view assignment from config, JSON validation with logged retries, disk cache.
4. `aggregate.py` — four aggregators per pool.
5. `analyze.py` — accuracy, recall (all / Prompted / hidden), FPR (controls / talk-only), counts everywhere; each aggregator vs individual average and chance; expert-minority diagnostic (mean predicted_yes_pct by view × own verdict); pairwise error agreement within pools; bootstrap CIs where n allows.
6. Figures: recall by aggregator × pool; FPR by aggregator × pool; predicted_yes_pct by view and verdict.

## 5. Timeline

- **Mon 19 Oct (no API calls):** `extract_runs.py`, `export_views.py` on pilot; show user V1/V2/V3; report what public task metadata agents see (input for [DECIDE]); estimate runs/day under the user's Gemini quota from the pilot's token use.
- **Tue 20 Oct:** 2 tuning seeds with the replicate scheme the user sets. Modal hello-world vLLM on L4; run `run_monitors.py` on the pilot's three views.
- **Wed 21 Oct — GATE:** (a) can quota produce enough held-out runs during the sprint? else switch agents to Modal; (b) any run meeting the Collusion label? else switch to Hospital w/ provisioner coalition; (c) monitor JSON validity and whether predicted_yes_pct varies at all.
- **Thu 22 Oct — FREEZE:** user commits pre-registration; dry-run full pipeline on tuning data only.
- **Fri 23 Oct:** held-out runs. **Sat 24 Oct:** monitors, aggregation, analysis exactly as pre-registered; deviations listed. **Sun 25 Oct:** user writes report; Claude Code: figures, README, methods checklist.

## 6. Fallbacks

- No Collusion-labelled runs in Jira → Hospital w/ provisioner coalition.
- Quota too small → open-weight agents on Modal.
- Predictions near-constant → report that monitors produced no usable meta-predictions.
- Colosseum blocked entirely → Track 1.4 fallback (pairwise net influence on scripted games, no API cost).

## 7. References

Colosseum (arXiv:2602.15198) · Denisov-Blanch et al., Consensus is Not Verification (ICML 2026, arXiv:2603.06612; no public code found) · Qiu, Carroll & Allen (ICLR 2026, arXiv:2601.20299) · Mytsyk et al. (arXiv:2608.25267) · Prelec, Seung & McCoy (Nature 2017) · Prelec (Science 2004) · ORBIT (arXiv:2609.33102) · BKM (arXiv:2609.01595) · Tian (arXiv:2610.11177)
