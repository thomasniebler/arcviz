# ADR-0036: Vendored CC0 icon subsets as Mermaid icon packs

- Status: Proposed
- Date: 2026-10-06
- Spec: `docs/architecture/specs/cloud-icons.md`

## Context

#100 asks for Azure, AWS and GCP icons in deployment diagrams via Mermaid icon packs
(`registerIconPacks` in the vendored Mermaid 11.17.2), vendored for offline use, only a subset, with
each vendor's licence checked and the size documented. The app is MIT-licensed and ships as a wheel and
a Docker image (CLAUDE.md rule 5: self-contained). Terms checked on 2026-10-06:

- **Azure Architecture Icons** (learn.microsoft.com/azure/architecture/icons): "Microsoft permits the use
  of these icons in architectural diagrams, training materials, or documentation. You may copy,
  distribute, and display the icons only for the permitted use unless granted explicit permission by
  Microsoft. Microsoft reserves all other rights."
- **AWS Architecture Icons** (aws.amazon.com/architecture/icons): "We allow customers and partners to use
  these toolkits and assets to create architecture diagrams." No licence for redistribution.
- **Google Cloud icons** (cloud.google.com/icons): offered for creating diagrams; no open licence for
  redistribution found.
- **Iconify `logos`** (svg-logos by Gil Barbara) and **`simple-icons`**: CC0-1.0 collections that include
  AWS service logos (about 70), Google Cloud product logos and monochrome Azure/Google Cloud service icons;
  the marks remain trademarks of their owners.

## Options

1. **Vendor the official icon sets (subset).** Best fidelity, but redistributing them inside an
   MIT-licensed package goes beyond "use … to create architecture diagrams"; needs explicit permission.
2. **Vendor CC0 subsets from Iconify (`logos`, `simple-icons`).** Redistributable, offline, small;
   coverage is good for AWS, partial for Azure and GCP (missing services fall back to the vendor logo).
3. **Load icons from a CDN at runtime.** No redistribution, but breaks the offline rule and the CSP-free
   self-contained image.
4. **Let operators supply their own packs** (a directory of Iconify JSON). Leaves licensing to the
   operator; no icons out of the box.

## Decision

Option 2, conservative while the maintainer cannot be asked: three packs (`aws`, `azure`, `gcp`) built
by `scripts/vendor_icons.py` from pinned CC0 sources, a subset of about 15–25 icons each, with sources,
licences, a trademark note and sizes in `arcviz/static/vendor/icons/README.md`. Option 4 can be added
later without changing the kinds-file format, which would also let operators who obtained permission
use the official icons.

## Consequences

- Azure and GCP packs mix colour logos with monochrome service icons recoloured in the vendor colour;
  some Azure kinds show the Azure logo. The README says which.
- Updating icons means bumping the pinned versions in the script and re-running it.
- Follow-up candidate: operator-provided icon packs (option 4).
