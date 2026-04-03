# Record Labelling Guide

After the ingester produces a record, a human reviews and labels it. This guide covers the conventions for that review.

Currently this applies to audio/video records (speaker labelling). As other format handlers are built, this guide will expand to cover their review needs.

## Audio/video speaker labelling

The ingester produces a speaker roster in the frontmatter with anonymous labels. The reviewer's job is to identify speakers by listening to the source material at the timestamps provided.

### Speaker roster fields

```yaml
speakers:
  - id: speaker_00
    name: Unknown
    first_appearance: 00:00:01
    relevant: true
```

| Field | Set by | Description |
|-------|--------|-------------|
| `id` | Ingester | Stable identifier used in the body's speaker turn annotations. Never change this. |
| `name` | Reviewer | The speaker's identity. Starts as "Unknown". |
| `first_appearance` | Ingester | Timestamp of the speaker's first turn. Use this to find them in the source. |
| `relevant` | Reviewer | Whether this speaker's content matters. Starts as `true`. |

### Identifying speakers

1. Open the source (the `source_url` in the frontmatter)
2. For each Unknown speaker, skip to their `first_appearance` timestamp
3. Listen to what they say and identify them
4. Replace "Unknown" with their name

A speaker has been reviewed when their name is anything other than "Unknown".

### Names

Use plain legal names. No titles, ranks, honourifics, or suffixes.

| Do this | Not this |
|---------|----------|
| David Fravor | Commander David Fravor |
| Marco Rubio | Senator Marco Rubio |
| Luis Elizondo | Luis "Lue" Elizondo |
| Leslie Kean | Dr Leslie Kean |

Titles and ranks change over time and are captured separately as claims in the knowledge graph. A person's name is their identity, not their current job.

See the [person naming convention](https://github.com/anomalica/anomalica/blob/main/decisions/drafts/person-naming-convention.md) in the meta-repository for the full rationale.

### Over-split speakers

The diarisation model sometimes splits one person into multiple speaker IDs. This is expected and intentional (the threshold is set to prefer over-splitting).

To merge: give the same name to both entries. The digester will treat them as the same person.

```yaml
  - id: speaker_00
    name: Bill Whitaker
    first_appearance: 00:00:01
    relevant: true
  - id: speaker_04
    name: Bill Whitaker
    first_appearance: 00:01:35
    relevant: true
```

### Irrelevant speakers

Mark speakers as `relevant: false` when their content is not part of the actual programme - advertisements, sponsor tags, jingles, bumper music voiceovers.

```yaml
  - id: speaker_01
    name: Unknown
    first_appearance: 00:00:44
    relevant: false
```

You don't need to identify irrelevant speakers. Leave the name as "Unknown".

### Unidentifiable but relevant speakers

Sometimes you can hear that someone is speaking but can't determine who they are. This is fine - leave the name as "Unknown" and keep `relevant: true`. The content will still be ingested; it just won't be attributed to a named individual.

### Group or archival audio

Broadcast content often includes clips from other sources - cockpit recordings, archival footage, press conferences. These may contain multiple unidentified people.

Use a descriptive label rather than a person name:

```yaml
  - id: speaker_08
    name: Cockpit audio
    first_appearance: 00:05:12
    relevant: true
```

The content is often first-hand testimony and should be kept as relevant even if the individuals can't be identified.

## Checking your work

After labelling, every speaker in the roster should be in one of three states:

| State | `name` | `relevant` |
|-------|--------|------------|
| Identified | A person's name or descriptive label | `true` |
| Unidentified but relevant | Unknown | `true` |
| Irrelevant | Unknown | `false` |

No speaker should remain unreviewed - even if you can't identify them, consciously decide whether they're relevant.
