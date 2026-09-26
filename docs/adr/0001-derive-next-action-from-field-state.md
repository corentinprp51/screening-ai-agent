# Derive the next action from field state, not from a stage pointer

The screening has no stored `current_stage` that transitions move forward. `next_action(state, config)` is a pure function that checks consent, then the knock-outs, then walks the configured fields in order and acts on the first one that isn't resolved, then the recap. Extraction may fill any field, not only the pending one. With this design, volunteered answers skip ahead, corrections re-run the knock-outs, and a returning candidate resumes, all without special-case transitions. The stage is still written to the candidate row for the dashboard and analytics, but it is computed from the state.

## Considered Options

- **Explicit stage pointer with a transition table.** Easier to draw as a state machine, but every correction (mid-flow or at the recap) and every answer given ahead of its turn needs its own hand-written branch.
