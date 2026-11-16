# Rod Library redirects and ARKs

Redirects deprecated UNI ScholarWorks (Digital Commons) links, and Rod Library's ARKs
under NAAN 32498, to their new homes in Preservica. Everything lives in this
repository: the redirect lists, the ARK tooling, the Apache rules, and the checks. The
Apache servers install from here.

```
redirects.csv  ◀── DCDS: old ScholarWorks address → Preservica address
arks/new_arks.csv ──Mint ARKs──▶ arks/ark_master_list.csv  (every ARK ever minted)
                                 arks/arks_manifest.csv     (one row per ARK)
Preservica (MODS mods.identifier) ──Match ARKs in Preservica (weekly)──┘  fills preservica_ref
        │
        │  every change: pull request → Checks (validate, test in Apache) → review → merge
        ▼
main ──every 15 minutes, each server: git pull + scripts/install_maps.sh──▶ Apache
        scholarworks.uni.edu   old ScholarWorks addresses → Preservica   (301)
        library.uni.edu        ark:/32498/...              → Preservica   (302)
```

Every change reaches `main` through a pull request, so a person reviews new
redirects, ARKs and matches before the servers pick them up.

## Files

| File | What it is |
|---|---|
| [`redirects.csv`](redirects.csv) | **Old ScholarWorks addresses and where they go**, maintained by DCDS. See [redirects.csv](#redirectscsv). |
| [`arks/arks_manifest.csv`](arks/arks_manifest.csv) | **The ARK list.** One row per ARK: its metadata, the Preservica record it points to, and the redirect status. Edit it here (through a pull request) to fix a match. |
| [`arks/ark_master_list.csv`](arks/ark_master_list.csv) | Every ARK ever minted, append-only. `generate_arks.py` checks it so no ARK is issued twice. Never edit or delete rows. |
| [`arks/new_arks.csv`](arks/new_arks.csv) | Rows waiting to be given ARKs by the **Mint ARKs** workflow; empty between runs. |
| [`arks/generate_arks.py`](arks/generate_arks.py) | Mints ARKs (the same script as in `rodlibrary/Scripts/arks`). |
| [`arks/ark_manifest.py`](arks/ark_manifest.py) | Keeps the manifest in step with the master list, matches ARKs in Preservica, builds the ARK redirects, checks the files. |
| [`scripts/build_map.py`](scripts/build_map.py) | Validates the redirect lists and builds the lookup files Apache reads. |
| [`scripts/install_maps.sh`](scripts/install_maps.sh) | Installs the redirects on a server (run by cron). |
| [`apache/`](apache/) | The Apache rules (`redirects.conf`, `ark.conf`), example virtual hosts, and the "moved" page. |
| [`tests/`](tests/) | The Apache test setup, test data and expected answers. |
| [`settings.env`](settings.env) | Shared settings: Preservica host and tenant, the MODS fields to search, the hostnames redirects may use. |
| `.github/workflows/` | The three workflows below. |

The ARK redirects aren't stored as a file: they're built from `arks/arks_manifest.csv`
whenever they're needed, so the two can't disagree. Each **Checks** run attaches them
and the lookup files the servers would install (artifact **redirects-preview**).

## redirects.csv

One row per old address. Two required columns, one optional:

| Column | What goes in it | Example |
|---|---|---|
| `dc_path` | the old ScholarWorks address, in full | `https://scholarworks.uni.edu/unigreek_images/1` |
| `p_path` | the new Preservica address, in full, or `410` if the item was withdrawn | `https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C9dbdd64c-5ab8-4344-ae48-99fc70625610` |
| `ark` *(optional)* | the item's ARK, so it resolves to `p_path` too | `ark:/32498/xo2gohohwd` |

```csv
dc_path,p_path
https://scholarworks.uni.edu/unigreek_images/1,https://uni.access.preservica.com/portal/en-US/asset/sdb%3AIO%7C9dbdd64c-5ab8-4344-ae48-99fc70625610
https://scholarworks.uni.edu/unigreek_images,https://uni.access.preservica.com/portal/en-US/folder/sdb%3ASO%7C...
https://scholarworks.uni.edu/etd/125,410
```

To change it, open `redirects.csv` on GitHub → pencil icon (or upload a replacement
with the same header) → **Commit changes** → *Create a new branch and start a pull
request*. Merge when the checks pass.

What `dc_path` can be:

* **Any page address:** an item (`/unigreek_images/1`), a collection
  (`/unigreek_images`), or a journal article (`/pias/vol94/iss4/3`). Case, a trailing
  slash, `http`/`https` and any `?...` on the visitor's link don't matter.
* **A file address:** `.../cgi/viewcontent.cgi?article=1001&context=unigreek_images`
  or `.../context/unigreek_images/article/1001/viewcontent/photo.jpg`. Old download
  links only redirect if their file address is listed; the page address alone doesn't
  cover them.
* **Not** the home page (`https://scholarworks.uni.edu/`): it always goes to
  `https://digital.library.uni.edu/` (set in the Apache configuration).

Rules the checks enforce (they name the line and the problem):

* `dc_path` must be a full `https://scholarworks.uni.edu/...` address, listed once.
  The same address twice with the same `p_path` is fine; with different ones it's an
  error.
* `p_path` must be a full `https://` address on `uni.access.preservica.com` or
  `digital.library.uni.edu`. Copy it from the portal as it is: `sdb%3AIO%7C` must stay
  encoded (a raw `|` is rejected).
* ARKs must be under NAAN 32498. Hyphens are ignored.
* **`redirects.csv` and the ARK list must agree.** An item listed in both, by address
  or by ARK, must point to the same place, or the checks fail naming both lines.
* Each row on its own line, and the file ends with a line break. Editing in Excel:
  save as **CSV UTF-8**.

A collection row also catches pages inside the collection that aren't listed, such as
`/unigreek_images/about.html` or `/unigreek_images/index.2.html`.

## What visitors get

| Request | Response |
|---|---|
| an address listed in `redirects.csv` or the ARK list | 301 to where it points |
| a listed file address (`viewcontent.cgi`, `/context/...`) | 301 to where it points |
| a page inside a listed collection | 301 to the collection |
| a listed address marked `410`, or an ARK marked `withdrawn` | 410 Gone, with the "moved" page |
| `https://scholarworks.uni.edu/` | 301 to `https://digital.library.uni.edu/` |
| `library.uni.edu/ark:/32498/<name>` for a matched ARK | 302 to its Preservica page (also on scholarworks.uni.edu) |
| an ARK under 32498 that isn't matched yet, or unknown | 404 |
| anything else on scholarworks.uni.edu | 404, with the "moved" page (search link, `library@uni.edu`) |

Old addresses get 301 (permanent) so search engines and Google Scholar update their
links. ARKs get 302 because an ARK is a persistent name whose target may move again.

ARK redirects come from `arks/arks_manifest.csv`: `active` rows go to their `url` if
set, or else to the Preservica portal page of their `preservica_ref`; `withdrawn` rows
answer 410; `unmatched` rows are left out until they're matched. A row with a
`dc_path` redirects that old address too.

## Workflows

| Workflow | Runs | What it does |
|---|---|---|
| **Checks** | every pull request and push | Checks the ARK manifest (unique ARKs, NAAN 32498, every ARK in the master list, valid references and statuses) and runs the script tests (`check`); validates `redirects.csv` and the ARK redirects together and attaches the result (`redirects`); runs a real Apache with the rules and checks every kind of redirect plus every real one (`apache_test`); scans for committed passwords (`secret_scan`). |
| **Mint ARKs** | by hand | Mints ARKs for the rows in `arks/new_arks.csv`, adds them to the master list and the manifest, empties `new_arks.csv`, and opens a pull request. The ANVL records for N2T/EZID are in the run's artifact. Refuses to run while an earlier minting pull request is still open. |
| **Match ARKs in Preservica** | Mondays 11:47 UTC, and by hand | Searches Preservica once for every record whose `mods.identifier` has a value, finds the ARKs in those values, and fills in each ARK's `preservica_ref`. Opens a pull request when anything changed; the run summary lists what it found. |

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

For redirects, `url` overrides the Preservica page, and `dc_path` / `dc_article`
(`context/article`, e.g. `etd/1124`) add the item's old ScholarWorks page and file
addresses.

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

### 3. Secret scanning

Settings → Advanced Security (or Code security): turn on **Secret Protection** and
**Push protection**. They're free for public repositories and block a password or
token from being pushed at all; the `secret_scan` check catches anything they miss.

### 4. Protect `main`

Settings → Rules → Rulesets → New branch ruleset: target the default branch, require a
pull request, require the status checks `check`, `redirects`, `apache_test` and
`secret_scan`, and block force pushes. GitHub doesn't let people approve their own
pull requests, so add a second person or a team to `.github/CODEOWNERS` before
requiring approvals. The servers install whatever is on `main`, so this is what keeps
an unchecked change off them.

### 5. Set up a server

Each server keeps a copy of this repository and installs the redirects from it. Needs
Apache 2.4 with `mod_rewrite` (and `mod_ssl`), `httxt2dbm` (in `httpd-tools` on RHEL,
`apache2-utils` on Debian/Ubuntu), Python 3, git and curl, and outbound HTTPS to
`github.com`. The repository is public, so no token is needed to download it.

```bash
sudo git clone https://github.com/rodlibrary/redirects.git /opt/redirects
```

**scholarworks.uni.edu:**

```bash
sudo mkdir -p /etc/httpd/redirects
sudo cp /opt/redirects/apache/redirects.conf /opt/redirects/apache/ark.conf /etc/httpd/redirects/
sudo cp -r /opt/redirects/apache/www /etc/httpd/redirects/www
sudo env SMOKE=0 /opt/redirects/scripts/install_maps.sh     # first install: Apache isn't using it yet
sudo cp /opt/redirects/apache/vhost-example.conf /etc/httpd/conf.d/scholarworks-redirects.conf   # then edit
sudo apachectl configtest && sudo systemctl reload httpd
```

The Apache user must be able to read everything under `/etc/httpd/redirects`. On
Debian/Ubuntu the configuration directory is `/etc/apache2/sites-available/`.

Keep it up to date with `/etc/cron.d/redirects`:

```
*/15 * * * * root cd /opt/redirects && git pull -q --ff-only && scripts/install_maps.sh >> /var/log/redirects-install.log 2>&1
```

`install_maps.sh` builds the ARK redirects from the manifest, validates them together
with `redirects.csv`, does nothing if nothing changed, refuses to drop more than 20%
of the redirects at once (`FORCE=1` to accept a deliberate clean-up), switches to the
new lookup files in one step (no Apache reload needed), tries five redirects against
this server and switches back if any fail. It reads `settings.env`; its own settings
are listed at the top of the script. If the server's certificate isn't trusted
locally, set `SMOKE_INSECURE=1` in the cron line.

To roll back by hand: `ls /etc/httpd/redirects/releases`, then
`sudo ln -sfn /etc/httpd/redirects/releases/<stamp> /etc/httpd/redirects/.current.new && sudo mv -Tf /etc/httpd/redirects/.current.new /etc/httpd/redirects/current`.

**library.uni.edu (ARKs).** NAAN 32498 is registered to Rod Library with the target
`https://library.uni.edu/ark:/${content}`, so n2t.net and arks.org send ARK requests
there. Clone the repository and add the cron line on that server too (with
`SMOKE_HOST=library.uni.edu SMOKE_ONLY=ark` before `scripts/install_maps.sh`), copy
`apache/ark.conf` to `/etc/httpd/redirects/`, and add the lines in
`apache/library-vhost-example.conf` to the existing library.uni.edu virtual host,
**before any other RewriteRule**. Only `/ark:/32498/...` paths are touched. If both
sites run on one Apache server, one copy of the files serves both.

Before pointing `scholarworks.uni.edu` at the server, test it directly:

```bash
python3 /opt/redirects/tests/check_redirects.py /etc/httpd/redirects/current/sample.tsv --server https://<server-ip>
```

## Day to day

**Add or fix old-address redirects:** edit `redirects.csv` through a pull request
(see [redirects.csv](#redirectscsv)). Live within 15 minutes of merging.

**Mint new ARKs**

1. Open `arks/new_arks.csv` on GitHub → pencil icon (or upload a replacement file
   with the same header), add one row per item (`identifier` and `title` at least;
   `url`, `dc_path`, `dc_article`, `preservica_ref` and `status` are optional), and
   commit it through a pull request. Merge it when the checks pass.
2. Actions → **Mint ARKs** → Run workflow.
3. Review the pull request it opens and merge it. The next matching run finds their
   Preservica records.

**Match** happens every Monday by itself; run **Match ARKs in Preservica** by hand
after ingesting new content. Review and merge its pull request; the servers pick up
the new ARK redirects within 15 minutes.

**Fix a match by hand:** edit the row in `arks/arks_manifest.csv` (paste the right
`preservica_ref`, or set `redirect_status` to `withdrawn`), and commit it through a
pull request. Hand-entered references are never overwritten.

## Test locally

On a Linux machine with Apache and `httxt2dbm` installed:

```bash
python3 -m unittest discover -s arks -p 'test_*.py'               # the ARK scripts
python3 arks/ark_manifest.py redirects --out build/ark_redirects.csv
python3 scripts/build_map.py --check redirects.csv build/ark_redirects.csv
sudo tests/run_apache_test.sh                                       # same as the apache_test check
```

## Things to know

* **Mint here, not on your own computer.** `generate_arks.py` still has its
  `--push-to-gitlab` option (it's shared with `rodlibrary/Scripts`), but GitLab is no
  longer part of the workflow. A local run would mint against an out-of-date master
  list; use the **Mint ARKs** workflow.
* **The first matching run** starts from the 76 ARKs that were in GitLab on
  2026-10-30: 34 already had a Preservica reference (kept, marked `entered by hand`),
  and 42 had none.
* **History.** Until November 2026 the redirect side lived in the GitLab project
  `rod-library/redirects` on `git.uni.edu`, and this repository pushed
  `redirects.csv` there. Its files were moved here on 2026-11-16 without its history;
  the GitLab project keeps that, including the earlier ScholarWorks harvesting and
  title-matching scripts (last version in commit `dc1639d`).
