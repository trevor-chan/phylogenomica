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
relative. Choosing a mulligan does not advance or end the stage, and is not
scored either way, so `mulligan → unlock` or `mulligan → target` scores the
same as choosing the stage-ending card immediately. The generator must never impose
ordered relative roles across a polytomy.

### Target visibility is stage-scoped

In expert difficulty the target is the concealed endpoint during the first
`M - 1` stages. In the ultimate stage it is shown as a normal card with its
image and names, occupies the terminal selectable position, and ends the game
when clicked. Guided difficulty reveals it from the opening stage instead; see
[Difficulty](#difficulty).

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

The local prototype may replace a placeholder with a validated image while a
game is open when background media download is explicitly enabled. Image
availability is presentation state only: it does not activate or remove cards,
change the fixed cladogram geometry, affect scoring, or participate in answer
selection.

## Guess and reveal rules

A transition stage begins with all of its cards active. Choosing the unlock
completes the stage and descends to the next backbone region. The ultimate
stage shows its relative cards and target card together; clicking the target
completes the game.

Choosing the mulligan does not complete the stage. It places the
second-deepest relationship and leaves the deeper unlock active, at no cost:
the mulligan is unscored, which makes this route score-equivalent to choosing
the unlock immediately.

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
player's guess. In expert difficulty, clicking the visible target resolves the
ultimate stage; in guided difficulty the closest relative resolves it and the
target is not selectable.

## Difficulty

One generated game is playable at two difficulties. A difficulty decides which
of a stage's generated cards are dealt, which of those the player may choose,
and which one ends the stage. It never changes the lineage, the tiers, or the
selection, so one seed produces one topology in both modes and the sections
above describe the shape both are drawn from.

**Expert** is the mode the rest of this document describes: the target is
concealed until the ultimate stage, every stage deals a mulligan, and clicking
the revealed target ends the game.

**Guided** reveals the target — image and names — from the opening stage.
Naming the closest relative is then the whole task, which changes two things:

- No mulligan is dealt. It exists to make the second-deepest relative tempting,
  and a visible target settles that comparison outright. The one exception is
  the ultimate stage, whose deepest relative is generated as its mulligan; that
  card is dealt because it is the closest relative there, and is scored as the
  card that ends the stage rather than as a mulligan.
- The ultimate stage deals the target as an ordinary-looking card that cannot
  be chosen. The task is unchanged from every other stage: name the closest
  relative. Choosing it completes the game and places the target as the
  endpoint it was already known to be.

Guided play therefore offers one fewer choice per stage than expert play of the
same configuration. That missing choice is the unscored mulligan, so both modes
score over the same cards and reach the same maximum; scores are directly
comparable between them.

Guided difficulty does not weaken any correctness rule. The engine still
resolves every guess, the answer to the open stage is still withheld until it
is placed, and the target is the only thing the mode reveals early.

## Scoring

The score counts relationships the player did not have to spend a guess on. It
accrues from zero and never falls: every guess pays for what it resolved, and a
wrong guess simply earns less than the guess that would have ended the stage.
One number, out of the maximum, is the whole readout.

### Accrual model, version 2

A stage is scored over its decoys plus the card that ends it — `N` cards, one
point each — so a perfect game scores `M * N`. **The mulligan is outside the
scoring system.** Until a stage ends there is nothing to distinguish the
deepest relative from the second-deepest, so naming either is the same
achievement: choosing the mulligan costs nothing, resolving it earns nothing,
and it does not stop a stage from being clean.

- A guess earns one point for every scored card it resolves that the player did
  not choose. The card the player chose earns nothing.
- Ending a stage with no wrong guess in it earns one more point, so a clean
  stage is worth every card the stage scores over.
- Same-tier peers are never charged and never placed by a guess.

Equivalently, a stage scores `(N - 1) - w + b`, where `w` is the number of
wrong guesses and `b` is one for a clean stage. **Every wrong guess costs
exactly one point, however near or far it was.** A near miss resolves more
relationships and therefore banks more points immediately, so narrowing the
field is rewarded rather than charged. Because the mulligan is unscored,
`mulligan → unlock` and an immediate unlock both score `N`, and a stage's floor
is zero: spending a guess on every decoy leaves nothing to earn.

Stage maxima are uniform across stages and games of one configuration, and the
two difficulties share them. Expert deals one card more per stage than guided,
but that card is the unscored mulligan, so both modes score over the same
cards; see [Difficulty](#difficulty).

### Reveal-weighted model, version 1 (replaced)

Version 1 charged a wrong guess one point for itself plus one for every
still-active relative it exposed as more distant. A near miss therefore cost
the most, because it collapsed the most structure at once.

Playing it showed that penalty runs against the design: narrowing the field to
a confident near miss is the skill the game is trying to teach, and version 1
punished it harder than a wild guess. Version 2 replaces it. The per-relative
model this document previously rejected — "provably equal to counting incorrect
clicks" — is exactly the model now in use; that equivalence is the point rather
than an objection to it. An alternative that counts tiers rather than relatives
remains rejected, because it makes stage maxima vary between games.

## Continuous cladogram

All stage results extend one persistent cladogram. The display distinguishes:

- topology from representative species;
- resolved from unresolved branches;
- inferred from revealed relationships;
- polytomies from ordered divergence levels;
- the unresolved target clade.

The view can zoom toward the active branch while retaining access to the full
history. Divergence ages are optional labels and never determine correctness.

The prototype encodes each distinction with a separate visual channel, so none
of them has to be inferred from another: stroke style carries resolved versus
unresolved, tip fill carries inferred versus revealed, and a polytomy is a rake
from one trunk node rather than a nested sequence. At stage opening it draws
the complete geometry as anonymous empty leaf slots, each branching event
labelled with its divergence age so the player can read the clade's shape in
time before any card identifies it. Expert mode labels an empty mulligan slot
with `+1`, and both modes label an empty unlock slot with *guess me*. Both hints
match the light gray, italicized *target clade* label. It sends no unplaced
species-to-slot mapping or clade name. Placements fill
stable slots without reflow, so the board remains trackable without disclosing
the open stage's answer.

When a hinted slot is populated by either a guess or a reveal, its hint is
replaced by the species name. The hint identifies a destination on the tree,
not which shuffled card belongs there.

## Endgame

Ending the last stage ends the game: clicking the target in expert play, or its
closest relative in guided play. The endgame then:

1. resolves remaining stage structure;
2. highlights the target image, vernacular name, and scientific name;
3. reopens the completed cladogram at full width;
4. shows the final score and relationship history; and
5. awards a target-aware title for the player's score.

For a default 45-point game, title tiers are `0–34`, `35–39`, `40–44`, and a
perfect `45`. The implementation expresses these as 11 or more points lost,
6–10 lost, 1–5 lost, and none lost so configured games with another maximum
retain the same meaning. The endgame says, “You've attained the title:
*title*.”

Title matching uses the target's named ancestor clades, not a taxonomic rank.
Every curated taxon label in the score tier matching any clade in that lineage
joins the selection pool; deeper matches are not preferred. The system first
chooses uniformly among those labels and then among that label's titles. A
generic title is used only when no tagged label matches. A versioned alias table
bridges source vocabulary such as `Metazoa` to catalog vocabulary such as
`Animalia`. Selection is pseudorandom but deterministic for the game and score
tier, so different games gain variety without changing their title on reload.
Titles are presentation feedback and never affect generation, correctness, or
scoring.

Free-text or early target identification is deferred as a possible alternate
mode because it tests different knowledge from the core topology puzzle.

### The completed tree reopens

While a game is in progress the board compresses finished stages into narrow
history bands and hides their clade names, because the open stage is the one
the player has to reason about. A finished game has no open stage, so that
compression has nothing left to buy. Every stage reopens at full width with its
species labelled and every branching point showing both its name and its
divergence age.

The reopened tree is taller than the board and scrolls. It is scrolled back to
the root once, on completion, so the lineage reads from Life outward rather
than from the end the player just reached. After that the viewport is the
player's; nothing re-centres it.

The target is named in the endgame under both difficulties. Guided play has
shown it from the opening stage; expert play has just placed it. In neither
case does naming it disclose anything the completed lineage does not already.

### Species detail on hover

Lingering on a species opens an adjacent panel with an enlarged image, both
names, and a short description drawn from the lead section of its English
Wikipedia article. It works on the cards in the tray and on the names in the
cladogram, so a relative placed three stages ago is still worth looking at.

Detail is offered for cards that have not been guessed yet. A lead paragraph
describes one species; it does not rank that species against a concealed
target, so it teaches biology without answering the stage. Article text is
licensed separately from the images and always appears with its own credit.

A missing description is expected rather than exceptional — some species have
no English article — and degrades to the names and picture alone.

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
