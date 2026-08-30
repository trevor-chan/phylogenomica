# Game design

## Premise

Each game has a hidden target species. The player reconstructs the evolutionary
path toward it by repeatedly identifying one of the two visible species that
lead deepest toward the target:

> Which visible species takes us closer to the hidden target?

The answer is determined entirely by the phylogenetic topology represented by
the game. Candidate images and names identify organisms; the core puzzle does
not depend on behavioral, ecological, geographic, or morphological clues.

A working game has approximately five stages of ten lineage members each.
These are configurable starting values to test against real data and player
experience, not permanent constraints.

| Parameter | Initial value |
|---|---:|
| Lineage members per stage, N | About 10 |
| Stages per game, M | About 5 |
| Total lineage members | `M * N`, about 50 |
| Unique relative species | `M * N - 1`, about 49 |
| Hidden target species | 1 |
| Unlock species | `2(M - 1)`, initially 8 |

## Invariants

### Phylogeny is the game

The player's task is to compare recency of common ancestry. Target trait clues
such as habitat, behavior, anatomy, or geography do not belong in the core
mode. A future alternate mode may use them without changing this mode.

### Perfect knowledge guarantees a perfect score

There is no unavoidable chance in answering a represented stage. The two
deepest selected relative species are explicit unlock species; either advances
the game. If topology cannot distinguish selected species, the generator must
never impose an arbitrary order on them.

### The target starts hidden

The target is both the unknown endpoint of the tree and a mystery constrained
by each revealed relationship. It is not shown among the relative cards and
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
relative set. The interaction rhythm is:

> guess → reveal → narrow → guess → resolve → descend

Scoring is framed as relationships inferred without reveal, rather than as a
punishment count.

## Playable-lineage model

For target species `T`, collapse non-branching nodes and consider the genuine
branching events from a game root toward `T`:

```text
C0 ⊃ C1 ⊃ C2 ⊃ ... ⊃ Ck ⊃ T
```

At every event, one child continues toward the target and one or more children
leave that lineage. Species sampled from those off-target branches form the
relative-species pool at that evolutionary depth.

The **backbone** is this collapsed root-to-target path. The **playable
lineage** is a target-relative projection containing exactly `M * N` selected
species: one hidden target and `M * N - 1` unique relatives. It is not the full
induced phylogeny among the selected relatives. Each relative labels a side
branch from the backbone; intervening structure inside that off-target branch
is contracted because the game represents only its relationship to the target.

The current vocabulary is:

- A **relative species** has the complete metadata required to construct its
  card and may be selected from an off-target branch of the backbone.
- A **target species** meets the same metadata requirement and has enough
  ordered relative capacity to construct a valid playable lineage.
- An **unlock species** is one of the two deepest selected relatives in a
  non-ultimate stage. Choosing either advances to the next stage. There are
  `2(M - 1)` unlock species.
- A **decoy species** is any selected species that is neither the target nor an
  unlock species. Decoys do not advance the stage or end the game. Their total
  count is `M * N - 1 - 2(M - 1)`, initially 41.

For the initial rich-card mode, complete card metadata means a scientific name,
a preferred English vernacular name, and a selected image with nonempty URL,
rights, and licence fields. This is a configurable presentation policy, not a
statement about biological validity.

A target is eligible only if its lineage can support a coherent game. Initial
quality signals include:

- sufficient collapsed lineage depth and relative-bearing tiers;
- capacity for `M * N - 1` unique relatives in the requested stage shape;
- adequate topological resolution;
- complete metadata for the target reveal.

An ineligible target can still appear as a relative.

## Evolutionary depth tiers

Index the retained backbone nodes from root to target starting at zero. A
selected relative's **tier** is the index of the backbone node where its
off-target branch attaches after projection. Species in one tier have the same
represented relationship to the target.

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
relatives sampled at that divergence level share a tier and are topologically
equivalent relative to the target. The UI must not visually imply a false order
among them.

Guessing one non-terminal member of a polytomy places that relative, but need
not automatically place its same-tier peers. They may remain active because the
guess established no order within their tier.

### Stage unlock boundary

The sister-clade terminal rule is deferred. In each non-ultimate stage, the two
selected relatives with the deepest tiers are unlock species. They may occupy
the same tier or two different tiers, but every unlock must be deeper than every
decoy in that stage. A selected tier may not contain both roles within one
stage. Unselected members of a source polytomy do not constrain the projected
lineage.

The final stage need not reach the target's literal closest biological sister
event. Empty, metadata-poor, or simply unselected deeper source tiers may be
skipped. The target remains the endpoint of the backbone.

## Stage construction

Each stage samples ordered tiers from a successive region of the target
backbone. The cards are shuffled; their tier ordering is never exposed
directly.

For initial `M` and `N`:

- each of the first `M - 1` stages contains `N - 2` decoys and two unlock
  species; and
- the ultimate stage contributes `N - 1` relative species followed by the
  hidden target, producing exactly `M * N` lineage members overall.

The generator should adapt to each target:

1. Trace the collapsed lineage from the current game root to the target.
2. Remove tiers with no valid relative cards.
3. Select `M * N - 1` unique relatives from successive ordered tiers.
4. Assign two deepest relatives as unlocks in each transition stage without
   mixing unlock and decoy roles within a selected tier.
5. End the ultimate stage at the hidden target; no closest-sister endpoint is
   required.
6. Validate hierarchy, role separation, uniqueness, and target hiding.

When a lineage is deeper than the game needs, sample backbone tiers across its
usable range rather than clustering all choices near the root or target. A
uniform seeded sample of valid branching points is the initial strategy to
test. Exact uniformity is less important than representative traversal and
good relative quality.

This naturally creates a difficulty ramp. Early stages may contrast plants,
arthropods, and vertebrates; later stages may distinguish neighboring families
or genera. Whether the final stage should always reach genus level remains an
empirical question.

## Relative selection

Relative choice balances topological validity with playability. Initial
preferences, not hard requirements beyond the configured card filter, are:

1. extant and reliably identified species;
2. usable licensed image;
3. useful vernacular name;
4. OneZoom popularity or another documented recognizability signal;
5. distinctiveness and metadata completeness;
6. small selected polytomies.

Species are never reused within a game. Selected polytomies of two are welcome;
groups of three or more should be avoided when alternatives provide comparable
depth and metadata quality.

Large off-target groups require representative sampling. A deterministic,
seeded, weighted sampler is sufficient initially. Some unfamiliar organisms
are a feature, so recognizability must not become an absolute filter.

## Relative cards

Each relative should show:

- a clear representative image;
- a preferred vernacular name when available;
- an italicized scientific name.

The initial rich-card mode requires all three fields. A future relaxed mode may
allow a scientific name without a reliable vernacular; scientific names may
themselves provide fair phylogenetic information.

## Guess and reveal rules

A non-ultimate stage begins with all relative cards active. If the player
chooses either unlock species, the stage completes and play descends to the
next backbone region. Unlocks are the two deepest selected species even when
they occupy distinct tiers.

If the chosen species is a decoy:

- the chosen decoy is placed;
- relatives in strictly more distant tiers become placeable and may be
  revealed;
- unguessed peers in the selected tier may remain unresolved and active;
- every relative in a deeper tier remains active;
- the score is reduced only for information the game actually revealed.

Example with `G` and `H` designated as unlock species:

```text
True tiers: A - B - [C,D,E] - F - [G,H]

Choose B: place A - B - ???; C D E F G H remain.
Choose C: place C; D E F G H remain because D/E share C's tier.
Choose F: place the intervening structure; G H remain.
Choose G or H: complete the stage.
```

Reveal behavior must never remove a species that could still be deeper than the
player's guess. The precise action that resolves the ultimate stage and reveals
the hidden target remains an explicit gameplay decision to settle before the
engine is implemented; target eligibility does not depend on that interaction.

## Scoring

The score represents relative relationships resolved without requiring a
reveal. The earlier nominal maximum of about 45 must be recalibrated around
decoy reveals, unlock choices, and the still-open ultimate-stage transition.

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
polytomies, adaptive seeded generation, the hidden target, relative cards,
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
- Is accepting two unlocks at distinct depths intuitive to players?
- What player action resolves the ultimate stage and reveals the target?
- How strongly should selection avoid polytomies of three or more?
