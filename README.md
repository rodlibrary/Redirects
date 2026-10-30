# Rod Library ARKs and Preservica matching

This repository keeps the list of every ARK Rod Library has minted under NAAN 32498,
finds the Preservica record each ARK belongs to, and sends the resulting
`redirects.csv` to the GitLab redirect repository
([`rod-library/redirects`](https://git.uni.edu/rod-library/redirects) on `git.uni.edu`),
which serves the redirects.

```
arks/new_arks.csv ──Mint ARKs──▶ arks/ark_master_list.csv  (every ARK ever minted)
                                 arks/arks_manifest.csv     (one row per ARK, maintained here)
                                          │
Preservica (MODS mods.identifier) ──Match ARKs in Preservica (weekly)──┘  fills preservica_ref
                                          │  pull request → review → merge
                                          ▼
                              Push to GitLab (on every change to main)
                                          ▼
          git.uni.edu rod-library/redirects:  redirects.csv, ark_master_list.csv
```

Every change reaches `main` through a pull request, so a person reviews new ARKs and
new matches before they go to GitLab.

## Files

| File | What it is |
|---|---|
| [`arks/arks_manifest.csv`](arks/arks_manifest.csv) | **The ARK list.** One row per ARK: its metadata, the Preservica record it points to, and the redirect status. Edit it here (through a pull request) to fix a match. |
| [`arks/ark_master_list.csv`](arks/ark_master_list.csv) | Every ARK ever minted, append-only. `generate_arks.py` checks it so no ARK is issued twice. Never edit or delete rows. |
| [`arks/new_arks.csv`](arks/new_arks.csv) | Rows waiting to be given ARKs by the **Mint ARKs** workflow; empty between runs. |
| [`arks/generate_arks.py`](arks/generate_arks.py) | Mints ARKs (the same script as in `rodlibrary/Scripts/arks`). |
| [`arks/ark_manifest.py`](arks/ark_manifest.py) | Keeps the manifest in step with the master list, matches ARKs in Preservica, builds `redirects.csv`, checks the files. |
| [`arks/push_to_gitlab.py`](arks/push_to_gitlab.py) | Pushes files to GitLab in one commit, only if they changed. |
| [`settings.env`](settings.env) | Preservica host and tenant, the MODS fields to search, the GitLab project and paths. |
| `.github/workflows/` | The four workflows below. |

`redirects.csv` isn't stored here: it's built from `arks/arks_manifest.csv` each time
it's pushed, so the two can't disagree. The **Checks** run on every pull request
attaches a preview of it (artifact **redirects-preview**).

## Workflows

| Workflow | Runs | What it does |
|---|---|---|
| **Mint ARKs** | by hand | Mints ARKs for the rows in `arks/new_arks.csv`, adds them to the master list and the manifest, empties `new_arks.csv`, and opens a pull request. The ANVL records for N2T/EZID are in the run's artifact. Refuses to run while an earlier minting pull request is still open. |
| **Match ARKs in Preservica** | Mondays 11:47 UTC, and by hand | Searches Preservica once for every record whose `mods.identifier` has a value, finds the ARKs in those values, and fills in each ARK's `preservica_ref`. Opens a pull request when anything changed; the run summary lists what it found. |
| **Push to GitLab** | when the manifest, master list or settings change on `main`, and by hand | Builds `redirects.csv` and pushes it and `ark_master_list.csv` to the top level of `rod-library/redirects`, in one commit. Skips files GitLab already has unchanged. Run by hand with **dry run** ticked to see what would change. |
| **Checks** | every pull request and push | Checks the manifest (unique ARKs, NAAN 32498, every ARK in the master list, valid references and statuses), runs the script tests, builds the `redirects.csv` preview, and scans for committed passwords (gitleaks). |

## How matching works

The Preservica account searches for records with any value in the fields listed in
`ARK_FIELDS` (`mods.identifier` by default) and reads every ARK in them, in any of the
usual forms: `ark:/32498/x7k9q`, `ark:32498/x7-k9q` or `https://n2t.net/ark:/32498/x7k9q`.
Hyphens are ignored when comparing, as the ARK rules require. Then, for each row of the
manifest:

| Found in Preservica | Result (`match` column) |
|---|---|
| in exactly one record, and the row has no `preservica_ref` | `preservica_ref` set to `IO_<uuid>` (Asset) or `SO_<uuid>` (Folder), status `active`: `ark in mods.identifier` |
| in the record already in `preservica_ref` | kept (a bare UUID gets the type Preservica reports) |
| in a different record from the one in `preservica_ref` | **kept as it is**, marked `conflict`, with both references in `notes` |
| in several records | left unmatched, marked `several records`, with the references in `notes` |
| nowhere | left as it is, marked `not found` (or `entered by hand` if a reference was typed in) |

A `preservica_ref` is never replaced automatically, and rows with status `withdrawn`
are never changed. ARKs that appear in Preservica but not in the manifest (minted
somewhere else, or mistyped) are listed in the run summary.

The search uses the query `*`: on Rod Library's Preservica 9.4, the `%` that
pyPreservica uses by default returns nothing.

If the ARKs are recorded in a different indexed field too, add it to `ARK_FIELDS` in
`settings.env`, separated by a comma (`mods.identifier,mods.recordIdentifier`).
Indexed field names are listed in `preservica/indexed_fields.csv` in
`rodlibrary/Scripts`.

## The manifest's columns

`ark`, `resolvable_url`, `naan`, `shoulder`, `ark_blade`, `identifier`, `title`,
`url`, `type`, `commitment`, `format`, `relation`, `source`, `dc_path`, `dc_article`
are as written by `generate_arks.py`. The rest:

| Column | Meaning |
|---|---|
| `preservica_ref` | the Preservica record: `IO_<uuid>` or `SO_<uuid>`. You can paste a bare UUID, `sdb:IO\|<uuid>` or a portal address; it's tidied on the next run. |
| `redirect_status` | `active` (has a `preservica_ref` or `url`), `unmatched`, or `withdrawn` (served as 410 Gone). Set `withdrawn` by hand. |
| `match` | how `preservica_ref` was found: `ark in mods.identifier`, `entered by hand`, `conflict`, `several records`, `not found` |
| `preservica_title` | the title of the matched record in Preservica, for checking |
| `checked` | when the matching last ran over this row |
| `notes` | details of conflicts and multiple matches, or notes from minting |

In `redirects.csv` the columns are `dc_path, dc_article, ark, preservica_ref,
target_url, status, notes`, the format the GitLab redirect tooling reads. `target_url`
comes from the manifest's `url`.

## Setup (one time)

You need the Admin role on this repository.

### 1. Secrets

Settings → Secrets and variables → Actions → **Secrets** tab → New repository secret.
Add them as *repository* secrets: not on the Variables tab, and not as environment
secrets, which the workflows can't see.

| Name | Value |
|---|---|
| `PRESERVICA_USER` | the Preservica service account's user name |
| `PRESERVICA_PASSWORD` | its password |
| `GITLAB_TOKEN` | the GitLab token from step 3 |

The service account needs **Read Metadata** on the security tags of the records (Read
Content alone isn't enough; without it the search finds nothing and the workflow says
so). Everything else, including the Preservica host `us.preservica.com` and tenant
`UNIOWA`, is in `settings.env`.

### 2. Let workflows open pull requests

Settings → Actions → General → Workflow permissions: tick **Allow GitHub Actions to
create and approve pull requests**, then Save. If it's greyed out, an organization
owner has to allow it first in the organization's Settings → Actions → General.
Without it, the workflows still push their branch and print a link for opening the
pull request by hand.

### 3. GitLab token

In GitLab, open `rod-library/redirects` → Settings → Access tokens → Add new token:

| Field | Value |
|---|---|
| Token name | `GitHub rodlibrary/Redirects` |
| Expiration date | as late as allowed; put the date in your calendar |
| Role | **Developer** (or Maintainer, if Developers can't push to `main`) |
| Scopes | **api** |

Copy it into the `GITLAB_TOKEN` secret. If `main` is protected in GitLab, its
"Allowed to push and merge" setting has to include the token's role. Then run **Push
to GitLab** by hand with **dry run** ticked: it reports what it would change, which
proves the token and the connection work.

### 4. Protect `main` (optional; needs a paid GitHub plan for private repositories)

Settings → Rules → Rulesets → New branch ruleset: target the default branch, require a
pull request, require the status checks `check`, `preview` and `secret_scan`, and
block force pushes. GitHub doesn't let people approve their own pull requests, so add
a second person or a team to `.github/CODEOWNERS` before requiring approvals.

## Day to day

**Mint new ARKs**

1. Open `arks/new_arks.csv` on GitHub → pencil icon (or upload a replacement file
   with the same header), add one row per item (`identifier` and `title` at least;
   `url`, `dc_path`, `dc_article`, `preservica_ref` and `status` are optional), and
   commit it through a pull request. Merge it when the checks pass.
2. Actions → **Mint ARKs** → Run workflow.
3. Review the pull request it opens and merge it. The new ARKs go to GitLab within a
   minute or two, and the next matching run finds their Preservica records.

**Match** happens every Monday by itself; run **Match ARKs in Preservica** by hand
after ingesting new content. Review and merge its pull request; merging pushes the new
`redirects.csv` to GitLab.

**Fix a match by hand:** edit the row in `arks/arks_manifest.csv` (paste the right
`preservica_ref`, or set `redirect_status` to `withdrawn`), and commit it through a
pull request. Hand-entered references are never overwritten.

## Things to know

* **GitHub now owns two files in GitLab:** `redirects.csv` and `ark_master_list.csv`
  at the top of `rod-library/redirects`. Every push replaces them, so don't edit them
  in GitLab, and **stop running `generate_arks.py --push-to-gitlab` on your own
  computer**: mint here instead. A local run would mint against an out-of-date master
  list and be overwritten by the next push from here.
* **What reads the top-level `redirects.csv` in GitLab?** The GitLab project's own
  pipeline builds its redirect maps from `manifest/redirects.csv` and
  `manifest/overrides.csv`, not from the top-level file. If the ARK redirects should
  be served from it, either set `GITLAB_REDIRECTS_PATH=manifest/overrides.csv` in
  `settings.env` or copy the rows across in GitLab. (Its validator needs every
  `active` row to have a `preservica_ref` or `target_url`, which this file always
  satisfies.)
* **The first matching run** starts from the 76 ARKs that were in GitLab on
  2026-10-30: 34 already had a Preservica reference (kept, marked `entered by hand`),
  and 42 had none.
* **Earlier version of this repository.** Until 2026-10-30 this repository also held
  a copy of the whole GitLab redirect pipeline (Apache rules, deploy scripts, tests).
  GitLab is staying the redirect repository, so that copy was removed to stop the two
  drifting apart. It's in this repository's history if ever needed.
