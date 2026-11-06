# ADR 0010 — Session points, weights and fair sharing

Status: Proposed
Date: 2026-11-06

## Context

With parallel increments across several chains, the master needs a limit on how much
runs at once and a rule for who gets the next slot. Models burn the subscription at very
different rates, so counting sessions is too coarse. Too many parallel sessions can use
up the 5h window before any of them finishes.

## Decision

- **Session points** measure what a running session costs: Haiku 1, Sonnet 2, Opus 4,
  Fable 8. The user sets the total for the machine across all projects, sized to the
  subscription (e.g. 20 = 2 Fable + 1 Opus, or 5 Opus, or 20 Haiku). Faster burn is fine:
  the ratio of tokens to finished work stays the same, the work just finishes sooner.
- **Weights** work like CSS grid `fr`: each chain has a weight of 1 or more; its share is
  its weight divided by the sum of the weights in the pool. Weight 2 gets twice the share
  of weight 1. Only chains that are **active and have a ready or running increment** are
  in the pool; inactive or fully blocked chains hold no share.
- Shares are **guarantees, not caps** (work-conserving): points a chain cannot use go to
  the others by the same weights.
- Shares are **averages over time** (deficit round robin): an increment bigger than the
  chain's share may start once enough points are free; the chain then runs a deficit and
  waits until the others have caught up.
- There is no exclusive priority level. Exclusivity is having only one chain active.

## Consequences

- The scheduler needs per-chain point accounting over time (deficits) and the readiness
  of every chain's graph.
- The dashboard shows weight, share and points in use per chain; the cinema view draws
  shares as beam thickness.
- The thresholds of ADR 0008 and the total points interact; both are tuned together.
