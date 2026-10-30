# UNI ScholarWorks → Preservica redirects and ARK resolver

Redirects for [UNI ScholarWorks](https://scholarworks.uni.edu/) (Rod Library,
University of Northern Iowa) as its Digital Commons content moves to Preservica.

This repository holds the URL manifest that maps retired Digital Commons URLs and
our ARKs to objects in Preservica, plus everything needed to serve those redirects
from Apache httpd.

```
Digital Commons (OAI-PMH) ──harvest_dc.py──▶ staging/dc_items.csv
                                                   │
Preservica Entity API ─────match_preservica.py─────┘   (manual workflow: Refresh manifest)
                                                   ▼
                     manifest/redirects.csv (+ overrides.csv)   ◀── edits via pull request
                                                   │
                   GitHub Actions: validate → build_maps → apache_test (+ secret_scan)
                                                   ▼
                     release "maps": redirect-maps.zip (maps/*.txt, stats.json, smoke_test.tsv)
                                                   │  pulled every 15 min by each server
                              ┌────────────────────┴────────────────────┐
                              ▼                                         ▼
   scholarworks.uni.edu: redirects.conf + ark.conf      library.uni.edu: ark.conf only
   (old ScholarWorks URLs and ARKs)                      (ARKs, as registered for NAAN 32498)

   Weekly scheduled workflow: check_targets → target_failures.csv
```

## What gets redirected

| Request | Response |
|---|---|
| `/etd/123/`, `/facsenate_documents/221` (landing page) | 301 → Preservica Asset |
| `/etd/`, `/journal-stem-arts/` (series or journal) | 301 → Preservica Folder |
| `/pias/vol94/iss4/3` (journal article) | 301 → Preservica Asset |
| `/pias/vol94/iss4/`, `/pias/vol94/` (issue, volume) | 301 → its Folder, or the journal's if it has none |
| `/pias/about.html`, `/etd/index.2.html` (pages inside a collection) | 301 → that collection's Folder |
| `/cgi/viewcontent.cgi?article=1124&context=etd` | 301 → Preservica Asset |
| `/context/etd/article/1124/viewcontent/file.pdf` | 301 → Preservica Asset |
| `/` (ScholarWorks home page) | 301 → `https://digital.library.uni.edu/` |
| `library.uni.edu/ark:/32498/x7k9q`, `/ark:32498/x7-k9q` | 302 → Preservica Asset |
| anything marked `withdrawn` | 410 Gone, with the "moved" page |
| anything else | 404, with the "moved" page and a search link |

Legacy URLs get 301 so search engines and Google Scholar update their links.
ARKs get 302 because the ARK is the persistent name and its target may move again.

## UNI settings

Checked against the live sites on 2026-10-13:

* **ScholarWorks:** `scholarworks.uni.edu`, OAI-PMH endpoint
  `https://scholarworks.uni.edu/do/oai/`.
* **Preservica API:** `us.preservica.com`, tenant `UNIOWA`, Preservica 9.4 (checked
  2026-10-30). The Access portal `uni.access.preservica.com` has no API: a login there
  returns 404.
* **Preservica:** item links go to the Access portal at `uni.access.preservica.com`.
  The ScholarWorks home page and the "moved" page link to `digital.library.uni.edu`.
  Contact address on the "moved" page: `library@uni.edu`.
* **ARKs:** NAAN 32498, registered to Rod Library. The ARK Alliance registry forwards
  `n2t.net/ark:/32498/...` and `arks.org/ark:/32498/...` to
  `https://library.uni.edu/ark:/32498/...` (302).
* **GitHub:** private repository `rodlibrary/redirects`, moved from the GitLab project
  `rod-library/redirects` on `git.uni.edu` on 2026-10-30 (see "Moving from GitLab").
* **Two numbers per item:** e.g. `/facsenate_documents/221` downloads as
  `viewcontent.cgi?article=1221&context=facsenate_documents`. Both are mapped.
* **Journals** (`pias`, `jias`, `istj`, `ukl`, `ijc`, `universitas`, `mtie`, `ptoj`,
  `ijgh`, `journal-stem-arts` and others) use `/journal/volN/issN/N` article URLs.
* **Names with hyphens** exist (`journal-stem-arts`), including inside OAI identifiers.

These values live in `settings.env` (read by every workflow), the
`deploy/pull.env*.example` files and the Apache configuration examples.

### Still to confirm

* **Preservica link format.** Links are built from `PRESERVICA_TARGET_TEMPLATE` in
  `settings.env`, currently `https://{access_host}/portal/en-US/{kind}/{sdb}`, which
  gives `https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C<uuid>`
  (Folders: `.../folder/sdb%3ASO%7C<uuid>`). Open one Asset and one Folder in the
  portal, compare their full addresses, and adjust the template before the first full
  `refresh_manifest` run. The template fields are listed at the top of
  `scripts/match_preservica.py`. Changing it later is safe: the next
  `refresh_manifest` run rebuilds every existing link.
* **Public hostname for links.** If `digital.library.uni.edu` is the same portal under
  the library's own name, set `PRESERVICA_ACCESS_HOST` to it, so redirects survive
  any future move of the Preservica portal.
* **library.uni.edu Apache.** Add the lines in `apache/library-vhost-example.conf`
  (see "ARKs" below).

### Not redirected

Community pages that aren't series (such as `/chas` or `/scua`), top-level pages such
as `/communities.html` and `/about.html`, and `/do/...` search pages get the "moved"
page with a search link. Add rows to `overrides.csv` for any of them you want to point
at a Preservica Folder; `redirects_miss.log` shows which ones people actually use.

## ARKs

The ARK Alliance registry sends `ark:/32498/...` requests to `library.uni.edu`, so
that site's Apache resolves them directly: it includes `apache/ark.conf` and keeps its
own copy of the maps, pulled from GitHub exactly like the redirect server's. Readers
get one redirect, straight to Preservica, and ARK resolution doesn't depend on the
ScholarWorks redirect server staying up. `apache/library-vhost-example.conf` has the
lines to add and the setup steps; `deploy/pull.env.library.example` is that server's
settings file (its post-deploy check tests ARKs only).

`ark.conf` touches only `/ark:/32498/...` paths and must be included before any other
RewriteRule in the library.uni.edu virtual host. ARKs also resolve on
`scholarworks.uni.edu`. Unknown names under 32498 get a 404; other NAANs are left
alone for the website to handle. If both hostnames end up on one Apache server, one
copy of the maps serves both.

## Repository layout

```
settings.env                hosts, NAAN, tenant and link template used by every workflow
.github/workflows/checks.yml          validate, build, Apache test, secret scan; publishes the maps
.github/workflows/refresh-manifest.yml  manual: rebuild the manifest and open a pull request
.github/workflows/check-targets.yml   weekly check that Preservica targets still resolve
.github/CODEOWNERS          who reviews changes to the manifest, Apache and deploy files
manifest/redirects.csv      the generated manifest (one row per item or collection)
manifest/overrides.csv      hand corrections; any key here replaces the same key above
staging/                    harvest snapshot and unmatched report (commit dc_items.csv!)
scripts/harvest_dc.py       OAI-PMH harvest of Digital Commons identifiers
scripts/match_preservica.py look up Preservica entities and write the manifest
scripts/validate.py         manifest checks (run on every pull request and push)
scripts/build_maps.py       manifest → Apache map files + smoke-test sample
scripts/check_redirects.py  send requests to Apache and check status/Location
scripts/check_targets.py    scheduled check that Preservica targets still resolve
apache/redirects.conf       rewrite rules for the old ScholarWorks URLs
apache/ark.conf             ARK rules for NAAN 32498 (scholarworks.uni.edu and library.uni.edu)
apache/vhost-example.conf   example virtual hosts for scholarworks.uni.edu
apache/library-vhost-example.conf  lines to add to the library.uni.edu virtual host
apache/www/moved.html       page shown for 404 and 410
deploy/pull_maps.sh         server-side installer with shrink guard and rollback
deploy/*.service, *.timer   systemd units to run it every 15 minutes
deploy/pull.env*.example    settings for the redirect server and for library.uni.edu
tests/                      fixtures, hand-written cases and the Apache test harness
```

The scripts need only Python 3.9+ and its standard library.

## Manifest format

`dc_path,dc_article,ark,preservica_ref,target_url,status,notes`

* **dc_path**: landing-page key `etd/123` or `pias/vol94/iss4/3`; a collection key
  `etd`, `pias`, `pias/vol94` or `pias/vol94/iss4`.
* **dc_article**: file key `etd/1124`. This is the *article* number from
  `viewcontent.cgi`, which is different from the number in the landing URL.
* **ark**: `32498/x7k9q`. An `ark:/` prefix and hyphens are stripped automatically;
  ARKs under any NAAN other than 32498 fail validation.
* **preservica_ref**: `IO_<uuid>` for an Asset, `SO_<uuid>` for a Folder. You can
  also paste a bare UUID (taken as an Asset), `sdb:IO|<uuid>`, or a whole portal
  address copied from the browser; it is converted to `IO_<uuid>` when read.
* **target_url**: the full Preservica address, already percent-encoded (`|` as
  `%7C`, `:` inside the ID as `%3A`). **Leave it blank when `preservica_ref` is filled
  in:** the link is then built from `PRESERVICA_TARGET_TEMPLATE`. Apache sends stored
  links out exactly as written, and validation rejects characters that must be encoded.
* **status**: `active`, `withdrawn` (410), or `unmatched` (ignored, kept for tracking).
  An `unmatched` row with a `preservica_ref` is treated as `active`.

Keys are lower-cased. Overrides are applied key by key: an override row with only
`dc_path` filled in changes the landing-page redirect and leaves that item's file
and ARK redirects as they were.

## One-time setup

### 1. Prepare Preservica

Each migrated Asset needs an external identifier that ties it to its Digital Commons
item. By default the matcher looks for type `dc_article` with value
`<context>-<article>`, e.g. `facsenate_documents-1221`, and for collection Folders
type `dc_series` with the path as value: `etd`, `pias`, and optionally `pias/vol94` or
`pias/vol94/iss4` for journal volume and issue Folders. If ARKs live in Preservica,
store them as type `ark`. Add these during ingest (an OPEX manifest or a pyPreservica
script can do it). Different names can be set with `--id-type`, `--id-template`,
`--series-id-type` and `--ark-id-type`.

Ask your Preservica administrator for a **service account** for the workflows:
**Read Metadata** on the security tags of everything migrated from ScholarWorks, no
edit or ingest rights, a Preservica password of its own (not a university single
sign-on login), and **no two-factor sign-in** (the scripts log in with a user name and
password only). API access depends on the licensed Preservica edition.

*Read Content* on its own isn't enough. Preservica hides records the account can't
read the metadata of, so lookups come back empty rather than failing; the matching
scripts stop with an explanation if the account can't see any records. To check the
account, sign in to Preservica in a browser as it and browse or search: what it sees
there is what the workflows will see.

Check the login from PowerShell before using it in GitHub (replace the host with the
server staff sign in to for Preservica's management screens, not the public portal):

```powershell
$cred = Get-Credential
Invoke-RestMethod -Method Post -Uri "https://us.preservica.com/api/accesstoken/login" `
  -Body @{ username = $cred.UserName; password = $cred.GetNetworkCredential().Password; tenant = "UNIOWA" }
```

A result containing `token` means the account works. Don't paste the output anywhere;
the token works as a temporary login. If it fails, the server's message says why:

| Message | Meaning |
|---|---|
| `incorrect.tenant` | the tenant is wrong, or the user name doesn't exist in tenant `UNIOWA` |
| `Bad credentials` | the password is wrong, or the account signs in through the university login and has no Preservica password |
| HTTP 404 | wrong host, e.g. the public portal instead of `us.preservica.com` |

The matching scripts report the same message when their login fails.

Finally, confirm the link format (see "Still to confirm" above).

### 2. Harvest Digital Commons while it is still live

Run the **Refresh manifest** workflow (step 4 below), which harvests and then matches,
or harvest by hand:

```bash
python3 scripts/harvest_dc.py --base-url https://scholarworks.uni.edu/do/oai/ \
    --dc-host scholarworks.uni.edu --out staging/dc_items.csv
git add staging/dc_items.csv && git commit -m "Snapshot of Digital Commons identifiers"
```

**Commit `staging/dc_items.csv`.** Once Digital Commons is switched off it's the only
record of the old URLs. Spot-check rows where `article_source` is `oai_id`: those
article numbers came from the OAI identifier, not from a file URL.

### 3. Configure GitHub

You need the Admin role on the repository (Settings is only visible to admins).

**Repository secrets** (Settings → Secrets and variables → Actions → **Secrets** tab →
New repository secret). Only the Preservica login goes here; every other setting,
including the host and the tenant `UNIOWA`, is in `settings.env`.

| Name | Value |
|---|---|
| `PRESERVICA_USER` | the service account's user name |
| `PRESERVICA_PASSWORD` | the service account's password |

Add them on the **Secrets** tab as *repository* secrets: not on the Variables tab, and
not as environment secrets, which the workflows can't see. GitHub hides secret values
in logs, and nobody can read them back once saved. Anyone who can push a branch to this
repository could write a workflow that uses them, so keep write access to trusted staff.

**Let the refresh open pull requests.** Settings → Actions → General → Workflow
permissions: tick **Allow GitHub Actions to create and approve pull requests**, then
Save. (If the box is greyed out, an organization owner has to allow it first under the
organization's Settings → Actions → General.) Without it, the refresh workflow still
pushes its branch and prints a link for opening the pull request by hand.

**Protect `main`.** Settings → Rules → Rulesets → New ruleset → New branch ruleset:

| Setting | Value |
|---|---|
| Ruleset name | `main` |
| Enforcement status | Active |
| Target branches | Add target → Include default branch |
| Require a pull request before merging | ✔ (Required approvals: 1 once a second reviewer is in `CODEOWNERS`) |
| Require status checks to pass | ✔, add `validate`, `build_maps`, `apache_test`, `secret_scan` |
| Block force pushes | ✔ |

Rulesets and code owner review on private repositories need a paid GitHub plan (Team or
Enterprise). On the Free plan the Rules page won't save them: merge only pull requests
whose checks have passed, which is also what keeps bad maps off the servers, since
maps are only published after every check passes on `main`. GitHub doesn't let people
approve their own pull requests, so add a second person or a team to
`.github/CODEOWNERS` before requiring approvals.

**Secret scanning.** The `secret_scan` check runs [gitleaks](https://github.com/gitleaks/gitleaks)
over every commit and fails if it finds a password or token committed by mistake.
(GitHub's own secret scanning is a paid add-on for private repositories.) If it ever
flags something real, change that password or revoke the token straight away: removing
it from the files doesn't remove it from the history.

**Token for the servers.** Each server downloads the maps with its own read-only token.
While signed in to GitHub: your profile picture → Settings → Developer settings →
Personal access tokens → **Fine-grained tokens** → Generate new token:

| Field | Value |
|---|---|
| Token name | e.g. `redirects pull: scholarworks.uni.edu` |
| Resource owner | `rodlibrary` |
| Expiration | the longest the organization allows; put the date in your calendar |
| Repository access | Only select repositories → `rodlibrary/redirects` |
| Permissions | Repository permissions → **Contents: Read-only** (Metadata: Read-only is added automatically) |

Copy the token (it starts `github_pat_`) into that server's `pull.env` as
`GITHUB_TOKEN`. Make a second token for library.uni.edu, so either can be revoked on
its own. The organization may need to approve fine-grained tokens before they work. A
personal token stops working if its owner leaves the organization; replace it before
that happens.

**Weekly link check.** Nothing to set up: `.github/workflows/check-targets.yml` runs
every Monday at 11:17 UTC (6:17 am Central in summer, 5:17 am in winter). Run it once
by hand to test it: Actions → Check redirect targets → Run workflow. When targets fail,
the run is marked failed and GitHub emails whoever last edited the schedule line, and
the run's **target-failures** artifact lists them. Private repositories use the
organization's GitHub Actions minutes (2,000 a month on the Free plan); with
`--pause 0.5`, checking a few thousand targets takes well under an hour.

### 4. Build the manifest

In GitHub: Actions → **Refresh manifest** → Run workflow, with *method* `identifiers`.
It harvests ScholarWorks, looks up each item in Preservica, and opens a pull request
with the new `manifest/redirects.csv` and `staging/dc_items.csv`, then runs the checks
on it. Review the changes in the pull request, and the items that couldn't be matched
in `staging/unmatched.csv` in the run's artifact (linked from the pull request). Merge
when it looks right.

Rerun it as more content is ingested; rows already resolved are carried forward
without new API calls, and their links are rebuilt with the current
`PRESERVICA_TARGET_TEMPLATE`. After Digital Commons is switched off, tick **Reuse the
committed staging/dc_items.csv** when running it, to re-match the last harvest.

**Matching by title.** If the Assets don't carry `dc_article` identifiers, run it with
*method* `titles`. It lists Preservica (or only the Folder given in *root folder*),
matches ScholarWorks titles to Asset titles, and puts candidates for anything uncertain
in `staging/title_match_review.csv` in the artifact. References already in the
manifest, including hand-entered ones, are kept.

**Matching by Preservica reference instead.** Fill in references by hand: open
`manifest/redirects.csv` from a refresh run (it lists every ScholarWorks item), paste
each item's Preservica reference into the `preservica_ref` column, and leave
`target_url` and `status` as they are. Save as **CSV UTF-8** if you use Excel. Commit
it through a pull request: the checks validate the references, build the links and test
them, and no Preservica login is involved. Rows still without a reference stay
unmatched, and later refresh runs keep every reference you entered.

The workflows run on GitHub's servers, which reach `scholarworks.uni.edu` and
`us.preservica.com` over the internet. If either site starts limiting which addresses
may connect, the harvest or the Preservica login will fail there. To run the same
steps by hand instead:

```bash
export PRESERVICA_HOST=us.preservica.com PRESERVICA_TENANT=UNIOWA PRESERVICA_USER=... PRESERVICA_PASSWORD=...
python3 scripts/match_preservica.py staging/dc_items.csv \
    --access-host uni.access.preservica.com \
    --existing manifest/redirects.csv --out manifest/redirects.csv \
    --unmatched-report staging/unmatched.csv --ark-id-type ark --series
python3 scripts/validate.py manifest/redirects.csv manifest/overrides.csv \
    --allowed-host uni.access.preservica.com --naan 32498
```

### 5. Set up the Apache servers

Both servers need Apache 2.4 with `mod_rewrite` and `mod_ssl`, `httxt2dbm` (in
`httpd-tools` on RHEL, `apache2-utils` on Debian/Ubuntu), Python 3 and curl.

**scholarworks.uni.edu (redirect server):**

```bash
sudo mkdir -p /etc/httpd/redirects /opt/dc-redirects /etc/dc-redirects
sudo cp apache/redirects.conf apache/ark.conf /etc/httpd/redirects/
sudo cp -r apache/www /etc/httpd/redirects/www
sudo install -m 755 deploy/pull_maps.sh /opt/dc-redirects/
sudo install -m 600 deploy/pull.env.example /etc/dc-redirects/pull.env   # then add the GitHub token
sudo cp deploy/dc-redirects-pull.{service,timer} /etc/systemd/system/

# First install: Apache won't start until the map files exist, so skip the smoke test once.
sudo env SKIP_SMOKE=1 /opt/dc-redirects/pull_maps.sh

sudo cp apache/vhost-example.conf /etc/httpd/conf.d/dc-redirects.conf   # then edit
sudo apachectl configtest && sudo systemctl reload httpd
sudo systemctl daemon-reload && sudo systemctl enable --now dc-redirects-pull.timer
```

**library.uni.edu (ARKs):** follow the steps at the top of
`apache/library-vhost-example.conf`, using `deploy/pull.env.library.example` as
`/etc/dc-redirects/pull.env`. If both hostnames are on one Apache server, skip the
second copy of the maps.

The Apache user must be able to read everything under `/etc/httpd/redirects`,
including each parent directory.

The first `pull_maps.sh` run needs the maps to exist on GitHub: the **maps** release
appears after the first successful run of the Checks workflow on `main` (Code → Releases).
The servers need outbound HTTPS to `api.github.com` and to GitHub's file storage
(`objects.githubusercontent.com` and `release-assets.githubusercontent.com`); if they
sit behind a firewall that limits outbound traffic, ask for those to be allowed.

Before changing DNS, test the redirect server directly:

```bash
python3 scripts/check_redirects.py maps/smoke_test.tsv --server https://<server-ip>
```

### 6. Cut over

1. Lower the TTL on `scholarworks.uni.edu` a few days ahead.
2. Point `scholarworks.uni.edu` at the redirect server; confirm TLS.
3. ARKs need no DNS change: NAAN 32498 already resolves to `library.uni.edu`. Make
   sure `ark.conf` is live there first.
4. Repoint DOIs and Handles directly at Preservica instead of relying on redirects.

## Day-to-day changes

1. Edit `manifest/overrides.csv` (or regenerate `redirects.csv`) on a branch.
2. Open a pull request. The checks validate, build and test in a real Apache.
3. Merge once the checks pass. The maps are republished, and within 15 minutes each
   server pulls them; Apache picks them up without a reload.

To fix one bad redirect, add a row to `overrides.csv` with just the key(s) that need
to change and the new, percent-encoded target.

## Working from Windows

* **Set once per copy of the repository:** `git config core.filemode false`. Windows
  doesn't store the "executable" flag, so without this setting Git reports every
  script as modified. Never commit those mode changes: the servers run
  `pull_maps.sh` directly and fail with "Permission denied" if it loses the flag.
* Git for Windows' default line-ending handling (`core.autocrlf=true`) is fine.
* Applying a patch file: put it outside the repository folder, then
  `git am C:\path\to\file.patch`. If a `git am` stops partway, run `git am --abort`
  before trying again.

## Deploy safeguards

`pull_maps.sh`:

* does nothing if the maps haven't changed;
* refuses an update that shrinks any map by more than `MAX_DROP_PCT` (default 2%),
  unless run with `FORCE=1`;
* installs each version in its own release directory and switches a symlink, so
  Apache never sees a half-written map;
* checks a random sample of redirects against the live server after switching and
  rolls back automatically if any fail;
* keeps the last `KEEP` releases, so a manual rollback is
  `ln -sfn releases/<stamp> current.new && mv -Tf current.new current`.

Logs go to the journal: `journalctl -u dc-redirects-pull.service`.

The Apache rules use the `NE` (no-escape) flag so stored links go out exactly as
written; without it Apache re-encodes `%3A` as `%253A` and breaks portal links. The
test suite fails if the flag is removed.

## Monitoring

* **Missed requests:** `redirects_miss.log` (see `vhost-example.conf`) records every
  request answered with 404 or 410, with its referrer. Check it regularly: frequent
  404s show items or pages the manifest is missing, which can be added to
  `overrides.csv`.
* **Broken targets:** the weekly **Check redirect targets** workflow saves
  `target_failures.csv` as the run's **target-failures** artifact, listing redirect targets that no longer resolve in Preservica and
  the manifest row each came from.
* **Deploys:** `journalctl -u dc-redirects-pull.service` on each server shows every
  pull, any update refused by the shrink guard, and any automatic rollback after a
  failed smoke test.
* **ARK use:** the optional `ark_access.log` in `library-vhost-example.conf` shows which
  ARKs are being cited.

## Moving from GitLab

This repository replaced the GitLab project `rod-library/redirects` on `git.uni.edu`
(project ID 2499) on 2026-10-30. The code is the same apart from what GitLab did:

| GitLab | GitHub |
|---|---|
| `.gitlab-ci.yml` `variables:` | `settings.env` |
| CI/CD variables `PRESERVICA_HOST`, `PRESERVICA_USER`, `PRESERVICA_PASSWORD` | repository secrets `PRESERVICA_USER`, `PRESERVICA_PASSWORD` (the host is in `settings.env`) |
| jobs `validate`, `build_maps`, `apache_test`, Secret Detection | the **Checks** workflow (`secret_scan` uses gitleaks) |
| `build_maps` artifact, downloaded with a project access token | `redirect-maps.zip` on the **maps** release, downloaded with a fine-grained token |
| manual jobs `refresh_manifest`, `match_by_title` | the **Refresh manifest** workflow, *method* `identifiers` or `titles`; it opens the pull request itself |
| pipeline schedule for `check_targets` | `check-targets.yml`, scheduled in the file |
| protected branch, code owner approval | ruleset on `main`, `.github/CODEOWNERS` |

The servers hadn't been set up to pull from GitLab yet, so they need nothing extra:
follow step 5 with the GitHub settings. Once this repository works, archive the GitLab
project (Settings → General → Advanced → Archive project) so nobody edits the old copy,
and delete its CI/CD variables and access tokens.
