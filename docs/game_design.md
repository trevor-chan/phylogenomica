# Game design

## Premise

Each game has a hidden target species. The player reconstructs the evolutionary
path toward it by repeatedly answering:

> Which visible species is most closely related to the hidden target?

The answer is determined entirely by the phylogenetic topology represented by
the game. Candidate images and names identify organisms; the core puzzle does
not depend on behavioral, ecological, geographic, or morphological clues.

A working game has approximately five stages of ten candidates each. These are
configurable starting values to test against real data and player experience,
not permanent constraints.

| Parameter | Initial value |
|---|---:|
| Candidates per stage, N | About 10 |
| Stages per game, M | About 5 |
| Candidate presentations | About 50 |
| Nominal maximum score | `(N - 1)M`, about 45 |
| Hidden targets | 1 |
| Valid terminal answers | Usually 2 or more |

## Invariants

### Phylogeny is the game

The player's task is to compare recency of common ancestry. Target trait clues
such as habitat, behavior, anatomy, or geography do not belong in the core
mode. A future alternate mode may use them without changing this mode.

### Perfect knowledge guarantees a perfect score

There is no unavoidable chance in answering a represented stage. If topology
cannot distinguish candidates, every topologically equivalent candidate is
accepted. The generator must never impose an arbitrary order on a polytomy.

### The target starts hidden

The target is both the unknown endpoint of the tree and a mystery constrained
by each revealed relationship. It is not shown among the candidate cards and
receives no direct clues during normal play.

### One continuous tree is constructed

Stages are successive regions of one root-to-target lineage, not independent
multiple-choice questions. Previously resolved branches remain the visible
history as the view descends into the unresolved target-containing branch.

```text
Life
 ├── resolved distant lineage
 └── target-containing lineage
      ├── resolved nearer lineage
      └── target-containing lineage
           └── ???
```

### Every guess adds knowledge

An incorrect choice places valid parts of the tree and narrows the active
candidate set. The interaction rhythm is:

> guess → reveal → narrow → guess → resolve → descend

Scoring is framed as relationships inferred without reveal, rather than as a
punishment count.

## The target lineage

For target species `T`, collapse non-branching nodes and consider the genuine
branching events from a game root toward `T`:

```text
C0 ⊃ C1 ⊃ C2 ⊃ ... ⊃ Ck ⊃ T
```

At every event, one child continues toward the target and one or more children
leave that lineage. Species sampled from those off-target sister branches form
a candidate pool at that evolutionary depth.

A target is eligible only if its lineage can support a coherent game. Initial
quality signals include:

- sufficient collapsed lineage depth and candidate-bearing sister groups;
- capacity for approximately five stages of approximately ten candidates;
- adequate topological resolution;
- a scientific name and reliable source identifiers;
- preferably a vernacular name and usable, licensed image.

An ineligible target can still appear as a relative.

## Evolutionary depth tiers

Gameplay operates on ordered divergence tiers rather than a flat candidate
ranking. Candidates in one tier have the same relevant relationship to the
target.

```text
Tier 1: A
Tier 2: B
Tier 3: C, D, E
Tier 4: F, G
Target lineage: ???
```

Increasing tier depth means a more recent common ancestor with the target.
Named taxonomic ranks do not define depth.

### Monotypic nodes

A chain with one child at every level contains no choice of evolutionary path.
These nodes are collapsed for gameplay even if several named ranks occur in
the chain. They may be retained separately as display metadata.

### Polytomies

Genuine unresolved or multifurcating nodes remain multifurcating. Multiple
candidates sampled at that divergence level share a tier and are topologically
equivalent relative to the target. The UI must not visually imply a false order
among them.

Guessing one non-terminal member of a polytomy places that candidate, but need
not automatically place its same-tier peers. They may remain active because the
guess established no order within their tier.

### Terminal sister group

The deepest visible tier is the closest visible sister group to the continuing
target lineage. Every candidate in that tier is correct. The generator should
provide at least two representatives in the terminal tier where possible so
the topology, rather than one arbitrarily chosen species, defines success.

## Stage construction

Each stage samples several candidate-bearing tiers from a contiguous region of
the target lineage. The candidate cards are shuffled; their tier ordering is
never exposed directly.

The generator should adapt to each target:

1. Trace the collapsed lineage from the current game root to the target.
2. Retain genuine branching events with viable sister-group candidates.
3. Divide usable depth into approximately five broad regions.
4. Select approximately ten unique candidates within each region.
5. Ensure the deepest selected tier is a valid terminal group.
6. Validate ordering, equivalence, uniqueness, and target hiding.

When a lineage is deeper than the game needs, sample across its complete usable
range rather than taking only broad or only recent branches. Exact uniformity
is less important than representative traversal and good candidate quality.

This naturally creates a difficulty ramp. Early stages may contrast plants,
arthropods, and vertebrates; later stages may distinguish neighboring families
or genera. Whether the final stage should always reach genus level remains an
empirical question.

## Candidate selection

Candidate choice balances topological validity with playability. Initial
preferences, not hard requirements, are:

1. extant and reliably identified species;
2. usable licensed image;
3. useful vernacular name;
4. OneZoom popularity or another documented recognizability signal;
5. distinctiveness and metadata completeness;
6. no reuse within a game.

Large sister groups require representative sampling. A deterministic, seeded,
weighted sampler is sufficient initially. Some unfamiliar organisms are a
feature, so recognizability must not become an absolute filter.

## Candidate cards

Each candidate should show:

- a clear representative image;
- a preferred vernacular name when available;
- an italicized scientific name.

If no reliable vernacular name exists, the scientific name is sufficient.
Scientific names may themselves provide fair phylogenetic information.

## Guess and reveal rules

A stage begins with all candidate cards active. If the player chooses a member
of the deepest active tier, the stage completes. Every member of that terminal
tier produces the same fully correct outcome.

If the chosen candidate is in an earlier tier:

- the chosen candidate is placed;
- candidates in strictly more distant tiers become placeable and may be
  revealed;
- unguessed peers in the selected tier may remain unresolved and active;
- every candidate in a deeper tier remains active;
- the score is reduced only for information the game actually revealed.

Example:

```text
True tiers: A - B - [C,D,E] - F - [G,H]

Choose B: place A - B - ???; C D E F G H remain.
Choose C: place C; D E F G H remain because D/E share C's tier.
Choose F: place the intervening structure; G H remain.
Choose G or H: complete the stage.
```

Reveal behavior must never remove a candidate that could still be closer to
the target than the player's guess.

## Scoring

The score represents candidate relationships resolved without requiring a
reveal. A ten-candidate stage nominally offers nine scoring opportunities, so
the initial five-stage maximum is about 45.

The implementation must track revealed information, not merely incorrect
clicks. One guess can expose several more-distant relationships, while guessing
one member of a polytomy need not forfeit points associated with unresolved
same-tier members. Exact presentation of score changes will be tested with the
prototype.

## Continuous cladogram

All stage results extend one persistent cladogram. The display distinguishes:

- topology from representative species;
- resolved from unresolved branches;
- inferred from revealed relationships;
- polytomies from ordered divergence levels;
- the hidden continuation toward the target.

The view can zoom toward the active branch while retaining access to the full
history. Divergence ages are optional labels and never determine correctness.

## Endgame

After the final stage, the initial endgame:

1. resolves remaining stage structure;
2. reveals the target image, vernacular name, and scientific name;
3. shows the completed cladogram;
4. shows the final score and relationship history.

Free-text or early target identification is deferred as a possible alternate
mode because it tests different knowledge from the core topology puzzle.

## Initial scope

The first playable version includes extant species, OneZoom topology and
metadata, preserved OTT identifiers, collapsed monotypic chains, genuine
polytomies, adaptive seeded generation, the hidden target, candidate cards,
positive scoring, and the persistent cladogram.

It deliberately defers extinct species, alternate dating databases, independent
taxonomy integration, trait clues, multiplayer, user-authored trees, free-text
target identification, sophisticated difficulty modes, and competing
phylogenetic hypotheses.

## Gameplay questions for testing

- Are ten simultaneous cards engaging or overwhelming?
- Is five stages the right duration?
- How should score lost to a reveal be explained?
- How much previous tree context should remain visible while zoomed in?
- Do images make some relationships too easy or too obscure?
- How often do large polytomies affect play, and do players understand them?
- Should terminal groups always contain multiple visible representatives?
