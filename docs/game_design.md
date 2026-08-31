# Game design

## Premise

Each game has a target species that is concealed during the transition stages
and shown in the ultimate stage. The player reconstructs the evolutionary path
toward it by repeatedly identifying the closest selected relative:

> Which visible species takes us closer to the target?

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
| Target species | 1 |
| Unlock species | `M - 1`, initially 4 |
| Mulligan species | `M`, initially 5 |
| Decoy species | `M * N - 2M`, initially 40 |

## Invariants

### Phylogeny is the game

The player's task is to compare recency of common ancestry. Target trait clues
such as habitat, behavior, anatomy, or geography do not belong in the core
mode. A future alternate mode may use them without changing this mode.

### Perfect knowledge guarantees a perfect score

There is no unavoidable chance in answering a represented stage. In a
transition stage, the uniquely deepest selected relative is the unlock species
and the uniquely second-deepest is the mulligan. In the ultimate stage, the
target is the terminal choice and the mulligan is the deepest selected
relative. Choosing a mulligan does not advance or end the stage, but awards a
one-point bonus so `mulligan → unlock` or `mulligan → target` scores the same
as choosing the stage-ending card immediately. The generator must never impose
ordered relative roles across a polytomy.

### Target visibility is stage-scoped

The target is the concealed endpoint during the first `M - 1` stages. In the
ultimate stage it is shown as a normal card with its image and names, occupies
the terminal selectable position, and ends the game when clicked.

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
species: one target and `M * N - 1` unique relatives. It is not the full
induced phylogeny among the selected relatives. Each relative labels a side
branch from the backbone; intervening structure inside that off-target branch
is contracted because the game represents only its relationship to the target.

The current vocabulary is:

- A **relative species** has the complete metadata required to construct its
  card and may be selected from an off-target branch of the backbone.
- A **target species** meets the same metadata requirement and has enough
  ordered relative capacity to construct a valid playable lineage.
- An **unlock species** is the uniquely deepest selected relative in a
  transition stage. Choosing it advances to the next stage. There are `M - 1`
  unlock species; the target replaces the unlock in the ultimate stage.
- A **mulligan species** is the uniquely second-deepest selected relative in a
  stage. It occupies a distinct shallower tier from the unlock. Choosing it
  awards one bonus point but does not advance play. There are `M` mulligans.
- A **decoy species** is any selected species that is neither the target nor an
  unlock or mulligan species. Decoys do not advance the stage or end the game.
  Their total count is `M * N - 2M`, initially 40.

For the initial rich-card mode, complete card metadata means a scientific name,
a preferred English vernacular name, and a selected image with nonempty URL,
rights, and licence fields. This is a configurable presentation policy, not a
statement about biological validity.

A target is eligible only if its lineage can support a coherent game. Initial
quality signals include:

- sufficient collapsed lineage depth and relative-bearing tiers;
- capacity for `M * N - 1` unique relatives in the requested stage shape;
- adequate topological resolution;
- complete metadata for the target card.

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

The sister-clade terminal rule is deferred. In a transition stage, the selected
relative on the deepest tier is the unlock and the selected relative on the
next-deepest tier is the mulligan. In the ultimate stage, the target replaces
the unlock and the mulligan is the deepest selected relative. Roles must occupy
distinct tiers, and every mulligan is deeper than every decoy in its stage. A
selected tier has exactly one role. Unselected members of a source polytomy do
not constrain the projected lineage.

The final stage need not reach the target's literal closest biological sister
event. Empty, metadata-poor, or simply unselected deeper source tiers may be
skipped. The target remains the endpoint of the backbone.

## Stage construction

Each stage samples ordered tiers from a successive region of the target
backbone. The cards are shuffled; their tier ordering is never exposed
directly.

For initial `M` and `N`:

- each of the first `M - 1` stages contains `N - 2` decoys, one mulligan, and
  one unlock; and
- the ultimate stage contains `N - 2` decoys, one mulligan, and the selectable
  target, producing exactly `M * N` lineage members overall.

The generator should adapt to each target:

1. Trace the collapsed lineage from the current game root to the target.
2. Remove tiers with no valid relative cards.
3. Select `M * N - 1` unique relatives from successive ordered tiers.
4. Assign a distinct second-deepest mulligan tier and deepest unlock tier in
   each transition stage, with all decoys shallower.
5. In the ultimate stage, place one mulligan deeper than all decoys and put the
   selectable target at the endpoint; no closest-sister tier is required.
6. Validate hierarchy, role separation, uniqueness, and stage-scoped target
   visibility.

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

Large off-target groups require representative sampling. The initial selector
uses a local deterministic seed to vary both valid tier allocation and species
choice. It minimizes selected same-tier groups larger than two before sampling
among valid layouts, then gives popularity a moderate nonzero weight. Every
metadata-valid species remains possible; unfamiliar organisms are a feature,
so recognizability is not an absolute filter. Identical versioned inputs and a
seed reproduce the same selection, while different seeds can produce different
games for one target.

## Relative cards

Each relative should show:

- a clear representative image;
- a preferred vernacular name when available;
- an italicized scientific name.

The initial rich-card mode requires all three fields. A future relaxed mode may
allow a scientific name without a reliable vernacular; scientific names may
themselves provide fair phylogenetic information.

## Guess and reveal rules

A transition stage begins with all of its cards active. Choosing the unlock
completes the stage and descends to the next backbone region. The ultimate
stage shows its relative cards and target card together; clicking the target
completes the game.

Choosing the mulligan does not complete the stage. It awards one bonus point,
places the second-deepest relationship, and leaves the deeper unlock active.
The bonus makes this route score-equivalent to choosing the unlock immediately.

If the chosen species is a decoy:

- the chosen decoy is placed;
- relatives in strictly more distant tiers become placeable and may be
  revealed;
- unguessed peers in the selected tier may remain unresolved and active;
- every relative in a deeper tier remains active;
- the score is reduced only for information the game actually revealed.

Example with `G` as the mulligan and `H` as the unlock:

```text
True tiers: A - B - [C,D,E] - F - G - H

Choose B: place A - B - ???; C D E F G H remain.
Choose C: place C; D E F G H remain because D/E share C's tier.
Choose F: place the intervening structure; G H remain.
Choose G: gain the mulligan point; H remains.
Choose H: complete the stage.
```

Reveal behavior must never remove a species that could still be deeper than the
player's guess. Clicking the visible target resolves the ultimate stage.

## Scoring

The score represents relative relationships resolved without requiring a
reveal. A mulligan guess awards one explicit bonus point; scoring must calibrate
the corresponding extra guess or reveal so `mulligan → unlock` or `mulligan →
target` and an immediate stage-ending choice produce the same stage score.

The implementation must track revealed information, not merely incorrect
clicks. One guess can expose several more-distant relationships, while guessing
one member of a polytomy need not forfeit points associated with unresolved
same-tier members. Exact presentation of score changes will be tested with the
prototype.

### Reveal-weighted model, version 1

Each stage is worth `N` points, so a perfect game scores `M * N`.

- The stage-ending card costs nothing. Choosing it resolves the whole stage.
- Any other card costs one for itself plus one for every still-active relative
  on a strictly shallower tier, because choosing it exposes those as more
  distant. A wrong guess deep in a stage therefore costs more than a shallow
  one: it collapses more of the tree at once.
- A mulligan is a flat cost of one, cancelled by its bonus. Its reveals are
  free, which is exactly what makes `mulligan → unlock` tie an immediate
  unlock.
- Same-tier peers are never charged and never placed by a guess.

Only decoys are ever charged, and each at most once, so a stage score never
falls below one unlock plus one mulligan. Scores are therefore always positive
and stage maxima are uniform across stages and games of one configuration.

The engine reports banked score and the open stage's standing value separately
so the two are never conflated, and reports each guess's cost and bonus so the
interface can frame them. An alternative model that counts tiers rather than
relatives was considered and rejected because it makes stage maxima vary
between games. A per-relative model in which the player earns each relationship
they demonstrate was also rejected: it is provably equal to counting incorrect
clicks.

## Continuous cladogram

All stage results extend one persistent cladogram. The display distinguishes:

- topology from representative species;
- resolved from unresolved branches;
- inferred from revealed relationships;
- polytomies from ordered divergence levels;
- the hidden continuation toward the target.

The view can zoom toward the active branch while retaining access to the full
history. Divergence ages are optional labels and never determine correctness.

The prototype encodes each distinction with a separate visual channel, so none
of them has to be inferred from another: stroke style carries resolved versus
unresolved, tip fill carries inferred versus revealed, and a polytomy is a rake
from one trunk node rather than a nested sequence. Only placed species are
drawn, so the board cannot disclose the open stage's answer.

## Endgame

Clicking the target in the final stage ends the game. The initial endgame then:

1. resolves remaining stage structure;
2. highlights the already visible target image, vernacular name, and scientific
   name;
3. shows the completed cladogram;
4. shows the final score and relationship history.

Free-text or early target identification is deferred as a possible alternate
mode because it tests different knowledge from the core topology puzzle.

## Initial scope

The first playable version includes extant species, OneZoom topology and
metadata, preserved OTT identifiers, collapsed monotypic chains, genuine
polytomies, adaptive seeded generation, stage-scoped target visibility, species
cards, positive scoring, and the persistent cladogram.

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
- Is the mulligan bonus and its non-advancing behavior clear to players?
- How strongly should selection avoid polytomies of three or more?
